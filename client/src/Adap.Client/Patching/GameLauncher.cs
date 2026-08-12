using System.Diagnostics;

namespace Adap.Client.Patching;

/// <summary>How a launched emulator session ended.</summary>
internal enum GameSessionOutcome
{
    /// <summary>Still running, or never launched by this client.</summary>
    Running,

    /// <summary>Exit code 0: the player closed the emulator deliberately.</summary>
    ClosedByPlayer,

    /// <summary>A fault code: a genuine crash, and forgivable.</summary>
    Crashed,

    /// <summary>
    /// Killed from outside, most often Task Manager. Windows reports the
    /// killer's chosen code, so this cannot be told apart from a user ending a
    /// hung process. Treated as unknown rather than guessed at.
    /// </summary>
    Terminated,
}

internal readonly record struct GameSessionResult(
    GameSessionOutcome Outcome,
    int ExitCode,
    string Description);

/// <summary>
/// Owns the emulator process for a session: builds the patched disc if it is
/// not already beside the patch, starts DuckStation on it, and reports how the
/// session ended.
///
/// Owning the process is what makes a deliberate quit distinguishable from a
/// crash at all. It is deterrence, not enforcement: anyone willing to end the
/// process from Task Manager lands in <see cref="GameSessionOutcome.Terminated"/>,
/// which this deliberately refuses to guess about.
/// </summary>
internal sealed class GameLauncher : IDisposable
{
    private Process? _process;

    public bool IsRunning => _process is { HasExited: false };

    public int? ProcessId
    {
        get
        {
            try
            {
                return _process is { HasExited: false } ? _process.Id : null;
            }
            catch (InvalidOperationException)
            {
                return null;
            }
        }
    }

    /// <summary>
    /// Builds the patched disc unless one is already beside the patch, and
    /// returns the path to boot. Reusing an existing disc is what keeps a
    /// second launch instant instead of rebuilding 300 MB.
    /// </summary>
    public static async Task<string> EnsurePatchedDiscAsync(
        string patchPath,
        string originalRomPath,
        IProgress<PatchProgress>? progress,
        Action<string>? status,
        CancellationToken cancellationToken,
        Func<string, Task<bool>>? confirmUnverifiedOriginal = null)
    {
        (string binPath, string cuePath) =
            PpfPatchService.GetOutputPaths(patchPath, originalRomPath);

        if (File.Exists(binPath))
        {
            // Silent: reusing the disc built last time is the ordinary case,
            // and saying so every launch is noise. Only the BUILD is worth a
            // line, because that one takes time.
            return File.Exists(cuePath) ? cuePath : binPath;
        }

        status?.Invoke($"Building {Path.GetFileName(binPath)} from the original disc...");
        PatchResult result = await PpfPatchService.ApplyAsync(
            patchPath,
            originalRomPath,
            overwrite: false,
            progress,
            cancellationToken,
            confirmUnverifiedOriginal);
        status?.Invoke(
            $"Patched {result.RecordCount} records into {Path.GetFileName(result.BinPath)}.");
        return File.Exists(result.CuePath) ? result.CuePath : result.BinPath;
    }

    /// <summary>Starts DuckStation on a disc and takes ownership of it.</summary>
    public bool TryLaunch(string emulatorPath, string discPath, out string message)
    {
        if (IsRunning)
        {
            message = "The game is already running.";
            return false;
        }
        if (!File.Exists(emulatorPath))
        {
            message = "Choose your DuckStation executable before launching.";
            return false;
        }
        if (!File.Exists(discPath))
        {
            message = $"The disc to boot is missing: {discPath}";
            return false;
        }

        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = emulatorPath,
                UseShellExecute = false,
                WorkingDirectory =
                    Path.GetDirectoryName(emulatorPath) ?? Environment.CurrentDirectory,
            };
            startInfo.ArgumentList.Add(discPath);

            _process = Process.Start(startInfo);
            if (_process is null)
            {
                message = "Windows did not start DuckStation.";
                return false;
            }
            _process.EnableRaisingEvents = true;
            // No launch line: the window's own "Game live. Connected." state is
            // the signal a player needs, and the PID was a diagnostic.
            message = string.Empty;
            return true;
        }
        catch (Exception ex)
        {
            message = $"Could not start DuckStation: {ex.Message}";
            return false;
        }
    }

    /// <summary>
    /// Classifies a finished session. Returns Running while it is still alive.
    /// </summary>
    public GameSessionResult Poll()
    {
        if (_process is null)
            return new GameSessionResult(GameSessionOutcome.Running, 0, string.Empty);

        try
        {
            if (!_process.HasExited)
                return new GameSessionResult(GameSessionOutcome.Running, 0, string.Empty);

            int code = _process.ExitCode;
            return Classify(code);
        }
        catch (InvalidOperationException)
        {
            return new GameSessionResult(GameSessionOutcome.Running, 0, string.Empty);
        }
    }

    /// <summary>
    /// Exit code 0 is a clean shutdown, which for an emulator means the player
    /// chose to close it. Anything with the high bit set is a Windows fault
    /// code, so a genuine crash. Small nonzero codes are what a killer supplies
    /// and cannot be attributed either way.
    /// </summary>
    public static GameSessionResult Classify(int exitCode)
    {
        if (exitCode == 0)
        {
            return new GameSessionResult(
                GameSessionOutcome.ClosedByPlayer,
                exitCode,
                "DuckStation was closed normally.");
        }

        uint unsigned = unchecked((uint)exitCode);
        if (unsigned >= 0x8000_0000)
        {
            return new GameSessionResult(
                GameSessionOutcome.Crashed,
                exitCode,
                $"DuckStation crashed (0x{unsigned:X8}).");
        }

        return new GameSessionResult(
            GameSessionOutcome.Terminated,
            exitCode,
            $"DuckStation was ended from outside (exit code {exitCode}).");
    }

    public void Dispose()
    {
        _process?.Dispose();
        _process = null;
    }
}
