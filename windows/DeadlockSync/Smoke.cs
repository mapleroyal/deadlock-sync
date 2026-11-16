using System.Diagnostics;
using System.Text.Json;
using DeadlockSync.Core;

namespace DeadlockSync;

internal static class Smoke
{
    public static async Task<int> RunAsync(string report)
    {
        var checks = new List<object>();
        bool ok = true;
        try
        {
            bool routing = Program.ResolveCommand(["--enable", "--show"]) == "enable-show"
                && Program.ResolveCommand(["--show", "--enable"]) == "enable-show"
                && Program.ResolveCommand([]) == "enable-show"
                && Program.ResolveCommand(["--background"]) == "background"
                && Program.ResolveCommand(["--prepare-install", "--show-errors"]) == "prepare-install"
                && Program.ResolveCommand(["--prepare-uninstall"]) == "prepare-uninstall";
            checks.Add(new { name = "setup and visible launch command routing", passed = routing }); ok &= routing;
            string altered = Path.Combine(Path.GetTempPath(), "deadlock-sync-invalid-engine-" + Guid.NewGuid().ToString("N"));
            bool rejected = false;
            try
            {
                File.WriteAllText(altered, "Synthetic altered engine");
                try { InstallPreparation.VerifyEngine(altered); }
                catch (InvalidDataException) { rejected = true; }
            }
            finally { File.Delete(altered); }
            checks.Add(new { name = "setup rejects an unverified engine", passed = rejected }); ok &= rejected;
            bool missingFile = SyncApplication.OptionalScheduledTask(() => throw new FileNotFoundException("Synthetic absent optional task")) is null;
            bool missingCom = SyncApplication.OptionalScheduledTask(() => throw new System.Runtime.InteropServices.COMException("Synthetic absent optional task", unchecked((int)0x80070002))) is null;
            bool deniedPropagated = false;
            try { SyncApplication.OptionalScheduledTask(() => throw new System.Runtime.InteropServices.COMException("Synthetic access denied", unchecked((int)0x80070005))); }
            catch (System.Runtime.InteropServices.COMException e) when (e.HResult == unchecked((int)0x80070005)) { deniedPropagated = true; }
            bool taskRegression = missingFile && missingCom && deniedPropagated;
            checks.Add(new { name = "optional scheduled task absence and access distinction", passed = taskRegression }); ok &= taskRegression;
            bool taskEnabled = SyncApplication.UpstreamScheduledTaskEnabled();
            checks.Add(new { name = "native scheduled task discovery", passed = true, upstreamTaskEnabled = taskEnabled });
            using (var child = new NativeWorker(Environment.ProcessPath!, ["--fake-worker", "--fixture", "success"], "backfill"))
            {
                await Wait(child);
                var evidence = await Evidence(child);
                bool passed = child.ExitCode == 0 && evidence.Complete && evidence.Progress.Uploaded == 3;
                checks.Add(new { name = "hidden worker stdout capture", passed, child.Pid }); ok &= passed;
            }
            using (var child = new NativeWorker(Environment.ProcessPath!, ["--fake-worker", "--fixture", "throttle"], "backfill"))
            {
                await Wait(child);
                var evidence = await Evidence(child);
                bool passed = child.ExitCode != 0 && evidence.RateLimited;
                checks.Add(new { name = "trailing stderr throttle capture", passed }); ok &= passed;
            }
            int pid;
            using (var child = new NativeWorker(Environment.ProcessPath!, ["--fake-worker", "--fixture", "hold"], "live"))
            {
                pid = child.Pid;
                await Task.Delay(300);
            }
            await Task.Delay(500);
            bool stopped;
            try { using var p = Process.GetProcessById(pid); stopped = p.HasExited; }
            catch (ArgumentException) { stopped = true; }
            checks.Add(new { name = "job close terminates owned worker", passed = stopped }); ok &= stopped;
            using var icon = new NotifyIcon { Icon = SystemIcons.Application, Text = "Deadlock Sync test", Visible = true };
            Application.DoEvents(); icon.Visible = false;
            checks.Add(new { name = "tray icon creation and dismissal", passed = true });
        }
        catch (Exception e) { ok = false; checks.Add(new { name = "exception", passed = false, error = e.ToString() }); }
        File.WriteAllText(report, JsonSerializer.Serialize(new { passed = ok, checks }, new JsonSerializerOptions { WriteIndented = true }));
        return ok ? 0 : 1;
    }

    private static async Task Wait(NativeWorker worker)
    {
        var elapsed = Stopwatch.StartNew();
        while (!worker.HasExited && elapsed.Elapsed < TimeSpan.FromSeconds(10)) await Task.Delay(30);
        if (!worker.HasExited) throw new TimeoutException("Synthetic worker did not finish.");
    }

    private static async Task<RunEvidence> Evidence(NativeWorker worker)
    {
        await worker.DrainAfterExitAsync();
        var evidence = new RunEvidence(worker.Mode);
        foreach (var (line, at) in worker.Drain()) evidence.AddLine(line, at);
        return evidence;
    }
}
