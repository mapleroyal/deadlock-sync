using System.Diagnostics;
using System.Text.Json;

namespace DeadlockSync;

/// <summary>Exercises the actual controller with synthetic accounts and owned fake workers only.</summary>
internal static class AccountSmoke
{
    public static async Task<int> RunAsync(string report)
    {
        var checks = new List<object>();
        string root = Path.Combine(Path.GetTempPath(), "DeadlockSync-accounts-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        NativeWorker? controller = null;
        var observedWorkers = new HashSet<int>();
        bool passed = true;
        try
        {
            Metadata(root, "2");
            controller = new NativeWorker(Environment.ProcessPath!, ["--test-root", root, "--enable"], "controller");
            JsonElement status = await Until(s => Text(s, "workerAccountId") == "2", 15);
            int firstPid = status.GetProperty("workerPid").GetInt32(); observedWorkers.Add(firstPid);
            checks.Add(new { name = "actual controller selects current account", passed = true });

            Metadata(root, "1");
            status = await Until(s => Text(s, "currentAccountId") == "1" && Text(s, "workerMode") == "", 12);
            if (!Stopped(firstPid) || status.GetProperty("nextAtUtc").GetInt64() < DateTimeOffset.UtcNow.ToUnixTimeSeconds() + 12)
                throw new Exception("Account takeover did not stop its old worker and retain handoff pacing.");
            checks.Add(new { name = "switch stops old account before handoff and retains spacing", passed = true });
            status = await Until(s => Text(s, "workerAccountId") == "1", 35);
            int activePid = status.GetProperty("workerPid").GetInt32(); observedWorkers.Add(activePid);
            File.WriteAllText(Path.Combine(root, "worker-event.txt"), "steam");
            status = await Until(s => AccountDeadline(s, "1") > DateTimeOffset.UtcNow.ToUnixTimeSeconds(), 12);
            File.Delete(Path.Combine(root, "worker-event.txt"));
            if (AccountDeadline(status, "2") != 0 || status.GetProperty("legacyHardUntilUtc").GetInt64() != 0)
                throw new Exception("Steam cooldown leaked from one selected account to another.");
            status = await Until(s => Text(s, "workerAccountId") == "2", 35);
            observedWorkers.Add(status.GetProperty("workerPid").GetInt32());
            if (!Stopped(activePid)) throw new Exception("Previous account left an orphan worker.");
            checks.Add(new { name = "Steam-limited current account permits other remembered account", passed = true });

            File.WriteAllText(Path.Combine(root, "worker-event.txt"), "upload");
            status = await Until(s => s.GetProperty("uploadHardUntilUtc").GetInt64() > DateTimeOffset.UtcNow.ToUnixTimeSeconds()
                && Text(s, "workerMode") == "", 12);
            File.Delete(Path.Combine(root, "worker-event.txt"));
            File.WriteAllText(Path.Combine(root, "game.txt"), "synthetic-game:1");
            status = await Until(s => s.GetProperty("gamePresent").GetBoolean(), 8);
            if (Text(status, "workerMode") != "") throw new Exception("Global upload cooldown did not block live work.");
            checks.Add(new { name = "upload endpoint cooldown blocks all accounts and live", passed = true });
            await SingleInstance.SendToAsync(root, "exit");
            await WaitExit(controller, 10);
            if (observedWorkers.Any(pid => !Stopped(pid))) throw new Exception("Controller exit left an owned worker.");
            checks.Add(new { name = "controller exit leaves no owned workers", passed = true });
        }
        catch (Exception e) { passed = false; checks.Add(new { name = "exception", passed = false, error = e.ToString() }); }
        finally
        {
            controller?.Dispose();
            await Task.Delay(300);
            try { Directory.Delete(root, true); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            { passed = false; checks.Add(new { name = "isolated test cleanup", passed = false, error = e.Message }); }
        }
        File.WriteAllText(report, JsonSerializer.Serialize(new { passed, checks }, new JsonSerializerOptions { WriteIndented = true }));
        return passed ? 0 : 1;

        async Task<JsonElement> Until(Func<JsonElement, bool> predicate, int seconds)
        {
            var watch = Stopwatch.StartNew(); string? last = null;
            while (watch.Elapsed < TimeSpan.FromSeconds(seconds))
            {
                if (controller?.HasExited == true) throw new Exception("Synthetic controller exited before its check.");
                last = await SingleInstance.SendToAsync(root, "status");
                if (last is not null)
                {
                    using var document = JsonDocument.Parse(last);
                    var value = document.RootElement;
                    if (predicate(value)) return value.Clone();
                }
                await Task.Delay(250);
            }
            throw new TimeoutException("Account lifecycle check timed out. Last synthetic status: " + last);
        }
    }

    private static string Text(JsonElement value, string key) => value.GetProperty(key).GetString() ?? "";
    private static long AccountDeadline(JsonElement value, string id) => value.GetProperty("accounts").EnumerateArray()
        .First(x => Text(x, "id") == id).GetProperty("hardUntilUtc").GetInt64();
    private static bool Stopped(int pid)
    {
        try { using var process = Process.GetProcessById(pid); return process.HasExited; }
        catch (ArgumentException) { return true; }
    }
    private static async Task WaitExit(NativeWorker worker, int seconds)
    {
        var clock = Stopwatch.StartNew();
        while (!worker.HasExited && clock.Elapsed < TimeSpan.FromSeconds(seconds)) await Task.Delay(100);
        if (!worker.HasExited) throw new TimeoutException("Synthetic controller did not exit.");
    }
    private static void Metadata(string root, string current)
    {
        string text = "\"users\" { \"76561197960265729\" { \"PersonaName\" \"Synthetic One\" \"MostRecent\" \""
            + (current == "1" ? "1" : "0") + "\" } \"76561197960265730\" { \"PersonaName\" \"Synthetic Two\" \"MostRecent\" \""
            + (current == "2" ? "1" : "0") + "\" } }";
        string path = Path.Combine(root, "loginusers.vdf"); File.WriteAllText(path + ".tmp", text); File.Move(path + ".tmp", path, true);
    }
}
