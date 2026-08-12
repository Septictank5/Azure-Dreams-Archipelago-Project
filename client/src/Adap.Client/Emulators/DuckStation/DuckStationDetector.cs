using System.Diagnostics;

namespace Adap.Client.Emulators.DuckStation;

internal static class DuckStationDetector
{
    /// <summary>
    /// Best guess at an installed DuckStation, used only to prefill the field
    /// the first time. A running instance is authoritative; otherwise the
    /// usual install locations are checked.
    /// </summary>
    public static string? FindInstalledExecutable()
    {
        try
        {
            foreach (Process process in Process.GetProcesses())
            {
                using (process)
                {
                    if (!process.ProcessName.StartsWith("duckstation", StringComparison.OrdinalIgnoreCase))
                        continue;
                    string? path = process.MainModule?.FileName;
                    if (!string.IsNullOrEmpty(path) && File.Exists(path))
                        return path;
                }
            }
        }
        catch (Exception)
        {
            // MainModule throws for processes of a different bitness or
            // privilege level; fall through to the well-known locations.
        }

        foreach (string root in new[]
        {
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
        })
        {
            if (string.IsNullOrEmpty(root))
                continue;
            foreach (string candidate in new[]
            {
                Path.Combine(root, "DuckStation", "duckstation-qt-x64-ReleaseLTCG.exe"),
                Path.Combine(root, "DuckStation", "duckstation-qt-x64.exe"),
                Path.Combine(root, "Programs", "DuckStation", "duckstation-qt-x64-ReleaseLTCG.exe"),
            })
            {
                if (File.Exists(candidate))
                    return candidate;
            }
        }
        return null;
    }

    public static IReadOnlyList<DuckStationCandidate> FindCandidates()
    {
        var candidates = new List<DuckStationCandidate>();

        foreach (Process process in Process.GetProcesses())
        {
            using (process)
            {
                string processName;
                int processId;

                try
                {
                    processName = process.ProcessName;
                    processId = process.Id;
                }
                catch (InvalidOperationException)
                {
                    continue;
                }

                if (!processName.StartsWith("duckstation", StringComparison.OrdinalIgnoreCase))
                    continue;

                if (DuckStationMemory.TryOpen(processId, processName, out DuckStationMemory? memory, out string? error))
                {
                    candidates.Add(new DuckStationCandidate(processId, processName, memory, null));
                }
                else
                {
                    candidates.Add(new DuckStationCandidate(processId, processName, null, error));
                }
            }
        }

        return candidates;
    }
}

internal sealed record DuckStationCandidate(
    int ProcessId,
    string ProcessName,
    DuckStationMemory? Memory,
    string? Error) : IDisposable
{
    public void Dispose() => Memory?.Dispose();
}
