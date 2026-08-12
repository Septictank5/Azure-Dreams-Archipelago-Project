using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

namespace Adap.Client.Patching;

internal enum PatchStage
{
    VerifyingOriginal,
    CopyingOriginal,
    ApplyingPatch,
    WritingCue,
}

internal readonly record struct PatchProgress(PatchStage Stage, int Percent);

internal readonly record struct PatchResult(string BinPath, string CuePath, int RecordCount);

internal static class PpfPatchService
{
    // The workspace fingerprint of an untouched Azure Dreams (USA) disc
    // image (README "Source fingerprint"). The length gives an instant
    // verdict on wildly wrong files; the SHA-1 settles the rest.
    internal const string ExpectedOriginalSha1 = "FAB68454BE5E4E48F7A43AC797CC8C47325C6A20";
    internal const long ExpectedOriginalLength = 298_576_992;
    internal const int PpfHeaderSize = 56;

    public static (string BinPath, string CuePath) GetOutputPaths(
        string patchFilePath,
        string originalRomPath)
    {
        string patchDirectory = Path.GetDirectoryName(Path.GetFullPath(patchFilePath))
            ?? Environment.CurrentDirectory;
        string outputStem = Path.GetFileNameWithoutExtension(patchFilePath);
        string binPath = Path.Combine(patchDirectory, outputStem + ".bin");
        if (Path.GetFullPath(binPath).Equals(
                Path.GetFullPath(originalRomPath),
                StringComparison.OrdinalIgnoreCase))
        {
            binPath = Path.Combine(patchDirectory, outputStem + " [Patched].bin");
        }

        return (binPath, Path.ChangeExtension(binPath, ".cue"));
    }

    /// <summary>
    /// Applies the PPF to a copy of the original image.
    ///
    /// <para><paramref name="confirmUnverifiedOriginal"/> decides what a
    /// fingerprint mismatch means. Left null (the CLI path), a mismatch is a
    /// hard failure, as it always was. Supplied (the windowed launcher), it
    /// receives a one-line description of the mismatch and returns whether
    /// to patch anyway; declining cancels the operation. Nothing has been
    /// written either way - verification precedes the copy.</para>
    /// </summary>
    public static async Task<PatchResult> ApplyAsync(
        string patchFilePath,
        string originalRomPath,
        bool overwrite,
        IProgress<PatchProgress>? progress = null,
        CancellationToken cancellationToken = default,
        Func<string, Task<bool>>? confirmUnverifiedOriginal = null)
    {
        string patchPath = Path.GetFullPath(patchFilePath);
        string originalPath = Path.GetFullPath(originalRomPath);
        ValidateInputFiles(patchPath, originalPath);

        (string binPath, string cuePath) = GetOutputPaths(patchPath, originalPath);
        if (!overwrite && (File.Exists(binPath) || File.Exists(cuePath)))
        {
            throw new IOException(
                $"The patched output already exists: {Path.GetFileName(binPath)}");
        }

        string temporaryBinPath = binPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
        string temporaryCuePath = cuePath + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            progress?.Report(new PatchProgress(PatchStage.VerifyingOriginal, 0));
            string? mismatch = null;
            long actualLength = new FileInfo(originalPath).Length;
            if (actualLength != ExpectedOriginalLength)
            {
                mismatch =
                    $"The selected BIN is {actualLength:N0} bytes; an unmodified " +
                    $"Azure Dreams (USA) disc image is {ExpectedOriginalLength:N0}.";
            }
            else
            {
                string actualSha1 = await ComputeSha1Async(originalPath, cancellationToken);
                if (!actualSha1.Equals(ExpectedOriginalSha1, StringComparison.OrdinalIgnoreCase))
                {
                    mismatch =
                        $"The selected BIN's SHA-1 is {actualSha1}; an unmodified " +
                        $"Azure Dreams (USA) disc image is {ExpectedOriginalSha1}.";
                }
            }

            if (mismatch is not null)
            {
                if (confirmUnverifiedOriginal is null)
                {
                    throw new InvalidDataException(
                        "The selected ROM is not an unmodified Azure Dreams (USA) BIN. " +
                        mismatch);
                }
                if (!await confirmUnverifiedOriginal(mismatch))
                {
                    throw new OperationCanceledException(
                        "Patching cancelled: the selected disc image is not a " +
                        "verified original.");
                }
            }

            progress?.Report(new PatchProgress(PatchStage.VerifyingOriginal, 100));
            progress?.Report(new PatchProgress(PatchStage.CopyingOriginal, 0));
            await CopyFileAsync(originalPath, temporaryBinPath, progress, cancellationToken);

            progress?.Report(new PatchProgress(PatchStage.ApplyingPatch, 0));
            int recordCount;
            await using (FileStream patch = new(
                             patchPath,
                             FileMode.Open,
                             FileAccess.Read,
                             FileShare.Read,
                             64 * 1024,
                             FileOptions.Asynchronous | FileOptions.SequentialScan))
            await using (FileStream output = new(
                             temporaryBinPath,
                             FileMode.Open,
                             FileAccess.ReadWrite,
                             FileShare.None,
                             64 * 1024,
                             FileOptions.Asynchronous | FileOptions.RandomAccess))
            {
                recordCount = await ApplyRecordsAsync(
                    patch,
                    output,
                    progress,
                    cancellationToken);
                await output.FlushAsync(cancellationToken);
            }

            progress?.Report(new PatchProgress(PatchStage.WritingCue, 0));
            string cueText =
                $"FILE \"{Path.GetFileName(binPath)}\" BINARY{Environment.NewLine}" +
                $"  TRACK 01 MODE2/2352{Environment.NewLine}" +
                $"    INDEX 01 00:00:00{Environment.NewLine}";
            await File.WriteAllTextAsync(
                temporaryCuePath,
                cueText,
                new UTF8Encoding(false),
                cancellationToken);

            File.Move(temporaryBinPath, binPath, overwrite);
            File.Move(temporaryCuePath, cuePath, overwrite);
            progress?.Report(new PatchProgress(PatchStage.WritingCue, 100));
            return new PatchResult(binPath, cuePath, recordCount);
        }
        finally
        {
            TryDelete(temporaryBinPath);
            TryDelete(temporaryCuePath);
        }
    }

    internal static async Task<int> ApplyRecordsAsync(
        Stream patch,
        Stream output,
        IProgress<PatchProgress>? progress = null,
        CancellationToken cancellationToken = default)
    {
        if (!patch.CanRead || !patch.CanSeek)
            throw new ArgumentException("The PPF stream must be readable and seekable.", nameof(patch));
        if (!output.CanWrite || !output.CanSeek)
            throw new ArgumentException("The ROM stream must be writable and seekable.", nameof(output));
        if (patch.Length < PpfHeaderSize)
            throw new InvalidDataException("The selected patch is too small to be a PPF1 file.");

        byte[] header = new byte[PpfHeaderSize];
        await ReadExactlyAsync(patch, header, cancellationToken);
        if (!header.AsSpan(0, 4).SequenceEqual("PPF1"u8))
            throw new InvalidDataException("Only Azure Dreams PPF1 patch files are supported.");

        byte[] recordHeader = new byte[5];
        byte[] recordData = new byte[byte.MaxValue];
        int recordCount = 0;
        int lastPercent = -1;
        while (patch.Position < patch.Length)
        {
            await ReadExactlyAsync(patch, recordHeader, cancellationToken);
            uint offset = BinaryPrimitives.ReadUInt32LittleEndian(recordHeader);
            int length = recordHeader[4];
            if (length == 0)
                throw new InvalidDataException($"PPF record {recordCount + 1} has zero length.");
            if ((ulong)offset + (uint)length > (ulong)output.Length)
            {
                throw new InvalidDataException(
                    $"PPF record {recordCount + 1} writes beyond the selected ROM.");
            }

            await ReadExactlyAsync(
                patch,
                recordData.AsMemory(0, length),
                cancellationToken);
            output.Position = offset;
            await output.WriteAsync(recordData.AsMemory(0, length), cancellationToken);
            recordCount++;

            int percent = checked((int)(patch.Position * 100 / patch.Length));
            if (percent != lastPercent)
            {
                progress?.Report(new PatchProgress(PatchStage.ApplyingPatch, percent));
                lastPercent = percent;
            }
        }

        if (recordCount == 0)
            throw new InvalidDataException("The selected PPF1 file contains no patch records.");
        progress?.Report(new PatchProgress(PatchStage.ApplyingPatch, 100));
        return recordCount;
    }

    private static void ValidateInputFiles(string patchPath, string originalPath)
    {
        if (!File.Exists(patchPath))
            throw new FileNotFoundException("Select an Azure Dreams PPF patch file.", patchPath);
        if (!File.Exists(originalPath))
            throw new FileNotFoundException("Select the original Azure Dreams BIN file.", originalPath);
        if (patchPath.Equals(originalPath, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("The patch and original ROM must be different files.");
    }

    private static async Task<string> ComputeSha1Async(
        string path,
        CancellationToken cancellationToken)
    {
        await using FileStream input = new(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan);
        byte[] hash = await SHA1.HashDataAsync(input, cancellationToken);
        return Convert.ToHexString(hash);
    }

    private static async Task CopyFileAsync(
        string sourcePath,
        string destinationPath,
        IProgress<PatchProgress>? progress,
        CancellationToken cancellationToken)
    {
        await using FileStream source = new(
            sourcePath,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan);
        await using FileStream destination = new(
            destinationPath,
            FileMode.CreateNew,
            FileAccess.Write,
            FileShare.None,
            1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan);

        byte[] buffer = new byte[1024 * 1024];
        long copied = 0;
        int lastPercent = -1;
        while (true)
        {
            int read = await source.ReadAsync(buffer, cancellationToken);
            if (read == 0)
                break;
            await destination.WriteAsync(buffer.AsMemory(0, read), cancellationToken);
            copied += read;
            int percent = source.Length == 0
                ? 100
                : checked((int)(copied * 100 / source.Length));
            if (percent != lastPercent)
            {
                progress?.Report(new PatchProgress(PatchStage.CopyingOriginal, percent));
                lastPercent = percent;
            }
        }

        await destination.FlushAsync(cancellationToken);
        progress?.Report(new PatchProgress(PatchStage.CopyingOriginal, 100));
    }

    private static async Task ReadExactlyAsync(
        Stream input,
        Memory<byte> buffer,
        CancellationToken cancellationToken)
    {
        int read = 0;
        while (read < buffer.Length)
        {
            int count = await input.ReadAsync(buffer[read..], cancellationToken);
            if (count == 0)
                throw new InvalidDataException("The selected PPF1 file ends inside a record.");
            read += count;
        }
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
                File.Delete(path);
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }
}
