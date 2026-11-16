using System.Diagnostics;
using System.Text.Json;

namespace DeadlockSync;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Contains("--fake-worker")) return FakeWorker(args);
        string? smoke = Option(args, "--smoke-test");
        if (smoke is not null) return Smoke.RunAsync(smoke).GetAwaiter().GetResult();
        string testRoot = Option(args, "--test-root") is { } path ? Path.GetFullPath(path) : "";
        string command = args.Contains("--exit") ? "exit" : args.Contains("--quit-sync") ? "quit"
            : args.Contains("--enable") ? "enable" : args.Contains("--show") ? "show"
            : args.Contains("--status") ? "status" : "background";
        string? statusFile = Option(args, "--status");
        try
        {
            using var instance = new SingleInstance(testRoot);
            if (!instance.OwnsMutex)
            {
                string? result = instance.SendAsync(command).GetAwaiter().GetResult();
                if (statusFile is not null) File.WriteAllText(statusFile, result ?? "{\"running\":false,\"error\":\"IPC unavailable\"}");
                return result is null ? 2 : 0;
            }
            if (command is "exit" or "quit" or "status")
            {
                if (statusFile is not null) File.WriteAllText(statusFile, "{\"running\":false}");
                return 0;
            }
            ApplicationConfiguration.Initialize();
            Environment.SetEnvironmentVariable("RUST_LOG", "info,deadlock_api_ingest::game_memory=debug");
            using var app = new SyncApplication(instance, testRoot, command);
            Application.Run(app);
            return 0;
        }
        catch (Exception e)
        {
            if (statusFile is not null) File.WriteAllText(statusFile, JsonSerializer.Serialize(new { running = false, error = e.Message }));
            else MessageBox.Show("Deadlock Sync could not start.\n\n" + e.Message, "Deadlock Sync", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    internal static string? Option(string[] args, string name)
    {
        int i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length && !args[i + 1].StartsWith("--") ? args[i + 1] : null;
    }

    private static int FakeWorker(string[] args)
    {
        // Only invoked by explicit --smoke-test / --test-root. Never touches Steam.
        string mode = Option(args, "--mode") ?? "backfill";
        string fixture = Option(args, "--fixture") ?? "hold";
        if (mode == "live") Console.WriteLine("memory: reading the game (pid 123)");
        else
        {
            Console.WriteLine("gc: account 123 has 10 match(es) in history, 3 missing salts (~20s each)");
            Console.WriteLine("gc: ingested match 456 (1/3)");
        }
        Console.Out.Flush();
        if (fixture == "success")
        {
            Console.WriteLine("gc: ingested match 457 (2/3)");
            Console.WriteLine("gc: ingested match 458 (3/3)");
            Console.WriteLine("Done, all own matches processed.");
            return 0;
        }
        if (fixture == "throttle")
        {
            Console.Error.WriteLine("gc: account 123 was rate-limited by Steam");
            Console.Error.WriteLine("Steam rate limit reached. Run this again on another day to fetch the remaining matches.");
            return 1;
        }
        if (fixture == "store")
        {
            Console.Error.WriteLine("gc: store unavailable, not tracking salt attempts: synthetic failure");
            Console.Error.Flush();
        }
        Thread.Sleep(TimeSpan.FromMinutes(10));
        return 0;
    }
}
