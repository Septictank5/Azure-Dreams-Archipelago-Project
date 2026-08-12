using System.IO.MemoryMappedFiles;

namespace Adap.Client.Emulators.DuckStation;

/// <summary>
/// Connects to DuckStation's official exported memory map. Current upstream
/// names it duckstation_&lt;pid&gt; and places eight megabytes of emulated RAM at
/// offset zero. A normal PlayStation game uses the first two megabytes.
/// </summary>
internal sealed class DuckStationMemory : IEmulatorMemory
{
    public const int ExportedRamSize = 8 * 1024 * 1024;

    private readonly MemoryMappedFile _mapping;
    private readonly MemoryMappedViewAccessor _ram;
    private readonly object _accessLock = new();
    private bool _disposed;

    private DuckStationMemory(
        int processId,
        string processName,
        MemoryMappedFile mapping,
        MemoryMappedViewAccessor ram)
    {
        ProcessId = processId;
        EmulatorName = $"DuckStation ({processName})";
        _mapping = mapping;
        _ram = ram;
    }

    public string EmulatorName { get; }

    public int ProcessId { get; }

    public int RamSize => ExportedRamSize;

    public static bool TryOpen(
        int processId,
        string processName,
        out DuckStationMemory? memory,
        out string? error)
    {
        memory = null;
        error = null;
        MemoryMappedFile? mapping = null;

        try
        {
            string mappingName = GetMappingName(processId);
            mapping = MemoryMappedFile.OpenExisting(mappingName, MemoryMappedFileRights.ReadWrite);
            MemoryMappedViewAccessor view = mapping.CreateViewAccessor(
                0,
                ExportedRamSize,
                MemoryMappedFileAccess.ReadWrite);

            memory = new DuckStationMemory(processId, processName, mapping, view);
            return true;
        }
        catch (FileNotFoundException)
        {
            error = "DuckStation is running, but Export Shared Memory is not enabled.";
        }
        catch (UnauthorizedAccessException ex)
        {
            error = $"The DuckStation RAM mapping could not be opened read/write: {ex.Message}";
        }
        catch (IOException ex)
        {
            error = $"The DuckStation RAM mapping could not be opened: {ex.Message}";
        }
        catch (PlatformNotSupportedException ex)
        {
            error = $"This DuckStation adapter is currently Windows-only: {ex.Message}";
        }

        mapping?.Dispose();
        return false;
    }

    internal static string GetMappingName(int processId) => $"duckstation_{processId}";

    public bool TryRead(uint psxAddress, Span<byte> destination, out string? error)
    {
        if (!TryValidateAccess(psxAddress, destination.Length, out long offset, out error))
            return false;

        try
        {
            byte[] buffer = new byte[destination.Length];
            lock (_accessLock)
            {
                int count = _ram.ReadArray(offset, buffer, 0, buffer.Length);
                if (count != buffer.Length)
                {
                    error = $"DuckStation returned {count} of {buffer.Length} requested bytes.";
                    return false;
                }
            }

            buffer.CopyTo(destination);
            error = null;
            return true;
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException)
        {
            error = $"DuckStation RAM read failed: {ex.Message}";
            return false;
        }
    }

    public bool TryWrite(uint psxAddress, ReadOnlySpan<byte> source, out string? error)
    {
        if (!TryValidateAccess(psxAddress, source.Length, out long offset, out error))
            return false;

        try
        {
            byte[] buffer = source.ToArray();
            lock (_accessLock)
            {
                _ram.WriteArray(offset, buffer, 0, buffer.Length);
            }

            error = null;
            return true;
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException)
        {
            error = $"DuckStation RAM write failed: {ex.Message}";
            return false;
        }
    }

    public void Dispose()
    {
        if (_disposed)
            return;

        lock (_accessLock)
        {
            if (_disposed)
                return;

            _ram.Dispose();
            _mapping.Dispose();
            _disposed = true;
        }
    }

    private bool TryValidateAccess(
        uint psxAddress,
        int byteCount,
        out long offset,
        out string? error)
    {
        if (_disposed)
        {
            offset = 0;
            error = "The DuckStation RAM connection is closed.";
            return false;
        }

        if (!PsxRamAddress.TryTranslate(psxAddress, byteCount, RamSize, out offset))
        {
            error = $"PS1 address 0x{psxAddress:x8} with length {byteCount} is outside exported RAM.";
            return false;
        }

        error = null;
        return true;
    }
}
