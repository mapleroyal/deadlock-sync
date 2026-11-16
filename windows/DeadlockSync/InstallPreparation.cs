using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;

namespace DeadlockSync;

/// <summary>Bounded, current-user setup coordination; never terminates a process by name.</summary>
internal static class InstallPreparation
{
    internal const string SetupMutexName = @"Local\DeadlockSync.Setup";

    internal static bool SetupRunning()
    {
        try
        {
            if (!Mutex.TryOpenExisting(SetupMutexName, out var gate)) return false;
            gate.Dispose();
            return true;
        }
        catch (UnauthorizedAccessException) { return true; }
    }

    internal static int Run(bool installing, string testRoot, string? reportPath, bool showErrors)
    {
        int code = 0;
        string error = "";
        try
        {
            // Verify the staged payload before asking a working older app to exit.
            if (installing) VerifyEngine(Path.Combine(AppContext.BaseDirectory, "ingest", "deadlock-api-ingest.exe"));
            using var instance = new SingleInstance(testRoot);
            if (!instance.OwnsMutex) StopController(instance);
            if (installing && testRoot.Length == 0)
            {
                try { SyncApplication.CheckOtherIngest(null); }
                catch (InvalidDataException e) { throw new PreparationException(3, e.Message); }
            }
        }
        catch (PreparationException e) { code = e.Code; error = e.Message; }
        catch (Exception e)
        {
            code = 1;
            error = "Setup could not prepare Deadlock Sync. No account history was removed. " + e.Message;
        }
        if (reportPath is not null)
        {
            try { File.WriteAllText(reportPath, JsonSerializer.Serialize(new { ok = code == 0, code, error })); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or ArgumentException)
            { if (code == 0) { code = 1; error = "Setup could not save its check result. " + e.Message; } }
        }
        if (code != 0 && showErrors)
            MessageBox.Show(error, "Deadlock Sync Setup", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        return code;
    }

    internal static void VerifyEngine(string path)
    {
        using var file = File.OpenRead(path);
        if (Convert.ToHexString(SHA256.HashData(file)).ToLowerInvariant() != SyncApplication.IngestHash)
            throw new InvalidDataException("The included sync engine could not be verified. Download the installer again from the project release page.");
    }

    private static void StopController(SingleInstance instance)
    {
        const string retry = "Deadlock Sync could not close completely. Use Exit app from its tray menu, then try setup again.";
        string? status = instance.SendAsync("status").GetAwaiter().GetResult();
        Process? process = null;
        try
        {
            if (status is null) throw new PreparationException(2, retry);
            using var json = JsonDocument.Parse(status);
            if (json.RootElement.TryGetProperty("controllerPid", out var property) && property.TryGetInt32(out int pid)
                && pid > 0 && pid != Environment.ProcessId)
            {
                try
                {
                    process = Process.GetProcessById(pid);
                    // Open and retain its handle before requesting exit, avoiding PID reuse.
                    _ = process.Handle;
                    if (process.SessionId != Process.GetCurrentProcess().SessionId)
                        throw new PreparationException(2, retry);
                }
                catch (ArgumentException) { process?.Dispose(); process = null; }
            }
            else if (!json.RootElement.TryGetProperty("running", out var running) || running.ValueKind != JsonValueKind.False)
                throw new PreparationException(2, retry);

            if (process is not null)
            {
                string? response = instance.SendAsync("exit").GetAwaiter().GetResult();
                if (response is null && !process.HasExited) throw new PreparationException(2, retry);
                if (!process.WaitForExit(15_000)) throw new PreparationException(2, retry);
            }
            if (!instance.WaitForOwnership(5000)) throw new PreparationException(2, retry);
        }
        finally { process?.Dispose(); }
    }

    private sealed class PreparationException(int code, string message) : Exception(message)
    {
        internal int Code { get; } = code;
    }
}
