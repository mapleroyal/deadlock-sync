using System.Diagnostics;
using System.Drawing.Drawing2D;
using System.Security.Cryptography;
using System.Text.Json;
using DeadlockSync.Core;

namespace DeadlockSync;

internal sealed class SyncApplication : ApplicationContext
{
    private readonly NotifyIcon icon;
    private readonly Icon image;
    private readonly Form dispatch;
    private readonly System.Windows.Forms.Timer timer;
    private readonly StateStore store;
    private readonly SyncState state;
    private readonly string testRoot;
    private readonly string dataRoot;
    private readonly string executable;
    private readonly SemaphoreSlim gate = new(1, 1);
    private NativeWorker? worker;
    private RunEvidence? evidence;
    private Form? panel;
    private Label? details;
    private string game = "";
    private string error = "";
    private bool exiting;
    private bool verified;
    private long liveNotBefore;
    private int liveFailures;
    private string saved = "";
    private string lastPhase = "";
    private string livePhase = "watching";
    private const string IngestHash = "d0e2a772ffc075713cc21759cb0a3b9e378a3235e59fc25cc7ed5b5d1f884464";
    private const string GameIdentityWarning = "A game process was detected but its identity or access could not be verified. History recovery is paused; run Steam and the game normally, without elevation.";

    public SyncApplication(SingleInstance instance, string testRoot, string command)
    {
        this.testRoot = testRoot;
        dataRoot = testRoot.Length != 0 ? testRoot : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DeadlockSync", "state");
        Directory.CreateDirectory(dataRoot);
        store = new StateStore(Path.Combine(dataRoot, "recovery.json"));
        state = store.Load();
        executable = Path.Combine(AppContext.BaseDirectory, "ingest", "deadlock-api-ingest.exe");
        dispatch = new Form { ShowInTaskbar = false, FormBorderStyle = FormBorderStyle.FixedToolWindow };
        _ = dispatch.Handle;
        image = TrayIcon();
        var menu = new ContextMenuStrip();
        menu.Items.Add("Status and progress", null, (_, _) => ShowPanel());
        menu.Items.Add("Enable syncing", null, async (_, _) => await ActionAsync("enable"));
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Quit syncing", null, async (_, _) => await ActionAsync("quit"));
        menu.Items.Add("Exit app", null, async (_, _) => await ActionAsync("exit"));
        icon = new NotifyIcon { Icon = image, Text = "Deadlock Sync", ContextMenuStrip = menu, Visible = state.Enabled };
        icon.MouseClick += (_, e) => { if (e.Button == MouseButtons.Left) ShowPanel(); };
        icon.BalloonTipClicked += (_, _) => ShowPanel();
        timer = new System.Windows.Forms.Timer { Interval = 2000 };
        timer.Tick += async (_, _) => await TickAsync();
        instance.Listen(ReceiveAsync);
        Microsoft.Win32.SystemEvents.SessionEnding += SessionEnding;
        timer.Start();
        dispatch.BeginInvoke(async () =>
        {
            if (command is "enable" or "show") await ActionAsync(command);
            else await TickAsync();
        });
    }

    private static long Now => DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    private string FindGame()
    {
        if (testRoot.Length == 0) return GameDetector.Find();
        string path = Path.Combine(testRoot, "game.txt");
        try { string value = File.Exists(path) ? File.ReadAllText(path).Trim() : ""; return value[..Math.Min(100, value.Length)]; }
        catch (IOException) { return game; }
    }

    private Task<string> ReceiveAsync(string command)
    {
        var result = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
        if (dispatch.IsDisposed || exiting) { result.SetResult("{\"running\":false}"); return result.Task; }
        dispatch.BeginInvoke(async () =>
        {
            try
            {
                if (command == "status") result.TrySetResult(Snapshot());
                else if (command == "exit")
                {
                    result.TrySetResult("{\"ok\":true}");
                    await ActionAsync(command);
                }
                else { await ActionAsync(command); result.TrySetResult(Snapshot()); }
            }
            catch (Exception e) { result.TrySetResult(JsonSerializer.Serialize(new { error = e.Message })); }
        });
        return result.Task;
    }

    private async Task ActionAsync(string command)
    {
        await gate.WaitAsync();
        try
        {
            game = FindGame();
            if (error == GameIdentityWarning && !game.Contains("unreadable:") && !game.Contains("unverified:")) error = "";
            if (command == "quit")
            {
                RecoveryPolicy.Quit(state, game);
                Save(); // Disabled intent is durable before terminating workers.
                await StopWorkerAsync(true);
                panel?.Hide();
            }
            else if (command == "enable")
            {
                RecoveryPolicy.Enable(state, Now);
                liveFailures = 0; liveNotBefore = 0; error = "";
                Save();
            }
            else if (command == "show") ShowPanel();
            else if (command == "exit")
            {
                exiting = true; timer.Stop();
                await StopWorkerAsync(true);
                Save(); icon.Visible = false;
                ExitThread();
                return;
            }
            Render();
        }
        catch (Exception e) { Fail(e); }
        finally { gate.Release(); }
        if (!exiting) await TickAsync();
    }

    private async Task TickAsync()
    {
        if (exiting || !await gate.WaitAsync(0)) return;
        try
        {
            long now = Now;
            game = FindGame();
            if (error == GameIdentityWarning && !game.Contains("unreadable:") && !game.Contains("unverified:")) error = "";
            var transition = RecoveryPolicy.ObserveGame(state, game, now);
            if (transition == GameTransition.Started) { liveFailures = 0; liveNotBefore = 0; error = ""; }
            Save();
            Drain();
            if (worker is not null && worker.HasExited) await CompleteWorkerAsync(false);
            if (worker is not null && testRoot.Length == 0)
            {
                try { CheckRunningOtherIngest(); }
                catch (InvalidDataException conflict)
                {
                    state.Reason = "unavailable"; state.Pending = true; liveFailures = 3; error = conflict.Message;
                    Save(); await StopWorkerAsync(true);
                }
            }
            if (worker is { Mode: "live" } && evidence?.EndpointRateLimited == true)
            {
                RecoveryPolicy.ObserveEvidence(state, evidence, now); Save();
                await StopWorkerAsync(true);
            }
            if (worker is { Mode: "live" } && evidence?.EndpointAccessDenied == true)
            {
                state.Reason = "unavailable"; state.Pending = true; liveFailures = 3;
                error = "The upload service denied access. Check the service status and app version, then choose Enable syncing to retry.";
                Save(); await StopWorkerAsync(true);
            }
            if (worker is { Mode: "backfill" } && evidence is not null && (evidence.StorageFailure || evidence.RateLimited))
            {
                // Persist a hard deadline as soon as evidence is observed, including before a forced stop.
                RecoveryPolicy.ObserveEvidence(state, evidence, now);
                Save();
                await StopWorkerAsync(false);
            }
            if (worker is { Mode: "backfill" } && now - worker.StartedAt >= 21600) await StopWorkerAsync(false);
            if (!state.Enabled)
            {
                if (worker is not null) await StopWorkerAsync(true);
            }
            else if (game.Length > 0)
            {
                if (worker is { Mode: "backfill" }) await StopWorkerAsync(true);
                bool verifiedGame = testRoot.Length != 0 || (!game.Contains("unreadable:") && !game.Contains("unverified:"));
                if (!verifiedGame) error = GameIdentityWarning;
                if (worker is null && verifiedGame && RecoveryPolicy.CanLive(state, game, now) && now >= liveNotBefore && liveFailures < 3)
                    StartWorker("live");
            }
            else
            {
                if (worker is { Mode: "live" }) await StopWorkerAsync(true);
                if (worker is null && RecoveryPolicy.CanBackfill(state, game, now))
                {
                    game = FindGame(); // Catch launch before its first visible window.
                    if (game.Length == 0)
                    {
                        RecoveryPolicy.Reserve(state, now); Save();
                        StartWorker("backfill");
                    }
                }
            }
            Save(); Render();
        }
        catch (Exception e) { Fail(e); }
        finally { gate.Release(); }
    }

    private void StartWorker(string mode)
    {
        if (worker is not null) throw new InvalidOperationException("An ingest worker is already running.");
        try
        {
            if (testRoot.Length == 0)
            {
                CheckOtherIngest();
                if (!verified)
                {
                    using var file = File.OpenRead(executable);
                    if (Convert.ToHexString(SHA256.HashData(file)).ToLowerInvariant() != IngestHash)
                        throw new InvalidDataException("Ingest executable verification failed. Reinstall Deadlock Sync.");
                    verified = true;
                }
                if (mode == "backfill") CheckUpstreamStore();
                worker = new NativeWorker(executable, [mode == "live" ? "--no-gc" : "--own-matches"], mode);
            }
            else
            {
                string fixtureFile = Path.Combine(testRoot, "fixture.txt");
                string fixture = File.Exists(fixtureFile) ? File.ReadAllText(fixtureFile).Trim() : "hold";
                worker = new NativeWorker(Environment.ProcessPath!, ["--fake-worker", "--mode", mode, "--fixture", fixture], mode);
            }
            evidence = new RunEvidence(mode); livePhase = "watching";
            error = "";
        }
        catch (Exception e)
        {
            error = "Cannot start " + (mode == "live" ? "live sync" : "history recovery") + ": " + e.Message;
            if (mode == "live") { liveFailures++; liveNotBefore = Now + Math.Min(300, 10 * (1 << liveFailures)); }
            else
            {
                state.Pending = true;
                state.Reason = e is InvalidDataException or UnauthorizedAccessException ? "state_store" : "unavailable";
            }
        }
    }

    private void CheckUpstreamStore()
    {
        string upstream = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "deadlock-api-ingest");
        Directory.CreateDirectory(upstream);
        string quota = Path.Combine(upstream, "gc-quota.json");
        if (File.Exists(quota))
        {
            if (new FileInfo(quota).Length > 16 * 1024 * 1024) throw new InvalidDataException("The upstream attempt store needs review; it has been preserved.");
            using var json = JsonDocument.Parse(File.ReadAllText(quota));
        }
        string probe = Path.Combine(upstream, ".deadlock-sync-write-test-" + Guid.NewGuid().ToString("N"));
        using (var stream = new FileStream(probe, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1, FileOptions.DeleteOnClose)) { }
    }

    private void CheckOtherIngest()
    {
        CheckRunningOtherIngest();
        using var run = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");
        if (run is not null)
            foreach (string name in run.GetValueNames())
                if (run.GetValue(name) is string command && command.Contains("deadlock-api-ingest", StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("The upstream ingest auto-start entry is still enabled. Disable that entry before using Deadlock Sync; it has been preserved.");
        if (UpstreamScheduledTaskEnabled())
            throw new InvalidDataException("The upstream deadlock-api-ingest scheduled task is enabled. Disable its auto-start before using Deadlock Sync; it has been preserved.");
    }

    internal static object? OptionalScheduledTask(Func<object?> query)
    {
        try { return query(); }
        catch (Exception e) when ((e is FileNotFoundException or System.Runtime.InteropServices.COMException)
            && e.HResult == unchecked((int)0x80070002))
        {
            // .NET maps ERROR_FILE_NOT_FOUND from ITaskFolder.GetTask to FileNotFoundException.
            // Only this optional task lookup may ignore absence; access/connection failures propagate.
            return null;
        }
    }

    internal static bool UpstreamScheduledTaskEnabled()
    {
        Type? schedulerType = Type.GetTypeFromProgID("Schedule.Service");
        if (schedulerType is not null)
        {
            dynamic? scheduler = null, folder = null, task = null;
            try
            {
                scheduler = Activator.CreateInstance(schedulerType);
                scheduler!.Connect(); folder = scheduler.GetFolder("\\");
                task = OptionalScheduledTask(() => folder.GetTask("deadlock-api-ingest"));
                return task is not null && task.Enabled;
            }
            finally
            {
                if (task is not null) System.Runtime.InteropServices.Marshal.FinalReleaseComObject(task);
                if (folder is not null) System.Runtime.InteropServices.Marshal.FinalReleaseComObject(folder);
                if (scheduler is not null) System.Runtime.InteropServices.Marshal.FinalReleaseComObject(scheduler);
            }
        }
        return false;
    }

    private void CheckRunningOtherIngest()
    {
        int session = Process.GetCurrentProcess().SessionId;
        foreach (var process in Process.GetProcesses())
            using (process)
                try
                {
                    if (process.Id != worker?.Pid && process.SessionId == session &&
                        process.ProcessName.StartsWith("deadlock-api-ingest", StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("Another Deadlock API Ingest process is running. Quit its own GUI/startup entry first; this app will not stop it.");
                }
                catch (InvalidOperationException) { }
                catch (System.ComponentModel.Win32Exception) { }
    }

    private void Drain()
    {
        if (worker is null || evidence is null) return;
        foreach (var (line, at) in worker.Drain()) evidence.AddLine(line, at);
        if (worker.OutputTruncated) evidence.MarkTruncated();
        if (worker.Mode == "backfill" || evidence.EndpointRateLimited) { RecoveryPolicy.ObserveEvidence(state, evidence, Now); Save(); }
        if (worker.Mode == "live") livePhase = evidence.Progress.Phase;
    }

    private async Task StopWorkerAsync(bool deliberate)
    {
        if (worker is null) return;
        worker.Stop();
        var until = Stopwatch.StartNew();
        while (!worker.HasExited && until.Elapsed < TimeSpan.FromSeconds(5)) await Task.Delay(25);
        if (!worker.HasExited) throw new IOException("The owned ingest worker did not stop; no replacement will start.");
        await CompleteWorkerAsync(deliberate);
    }

    private async Task CompleteWorkerAsync(bool deliberate)
    {
        if (worker is null || evidence is null) return;
        await worker.DrainAfterExitAsync();
        Drain();
        if (worker.OutputTruncated || worker.DrainTimedOut) evidence.MarkIncomplete();
        if (worker.Mode == "backfill")
            RecoveryPolicy.Finish(state, worker.RunId, worker.ExitCode, evidence, Now, deliberate);
        else if (!deliberate)
        {
            liveFailures++;
            liveNotBefore = Now + Math.Min(300, 10 * (1 << Math.Min(liveFailures, 5)));
            error = "Live worker stopped. " + (liveFailures >= 3 ? "Choose Enable syncing to retry after checking Steam/game permissions." : "Retrying shortly.");
        }
        Save(); // Consume completion durably before releasing the worker and its final evidence.
        worker.Dispose(); worker = null; evidence = null;
    }

    private void Save()
    {
        string json = JsonSerializer.Serialize(state);
        if (json == saved) return;
        store.Save(state); saved = json;
    }

    private void Fail(Exception e)
    {
        error = "Sync needs attention: " + e.Message;
        state.Corrupt = true; state.Reason = "state_store";
        worker?.Stop(); // Fail closed if durable scheduling becomes unavailable.
        Render();
    }

    private string Snapshot() => JsonSerializer.Serialize(new
    {
        running = true, version = "0.1.0", testMode = testRoot.Length > 0,
        enabled = state.Enabled, phase = RecoveryPolicy.Phase(state, game, Now, worker?.Mode ?? ""),
        reason = state.Reason, pending = state.Pending, workerMode = worker?.Mode ?? "", workerPid = worker?.Pid,
        controllerPid = Environment.ProcessId, controllerMemoryBytes = Process.GetCurrentProcess().WorkingSet64,
        gamePresent = game.Length > 0, nextAtUtc = RecoveryPolicy.Deadline(state), hardUntilUtc = state.HardUntilUtc,
        uploadHardUntilUtc = state.UploadHardUntilUtc,
        iconVisible = icon.Visible, progress = evidence?.Progress ?? state.Progress, unavailable = state.Skipped,
        error, stateCorrupt = state.Corrupt
    });

    private void Render()
    {
        string phase = RecoveryPolicy.Phase(state, game, Now, worker?.Mode ?? "");
        icon.Visible = state.Enabled && !exiting;
        string title = phase switch
        {
            "game" => state.UploadHardUntilUtc > Now ? "Upload service cooldown · game running" : worker?.Mode == "live" ? "Live sync · " + (livePhase == "reading" ? "reading memory" : livePhase == "failed" ? "memory needs attention" : "watching game") : "Game running · sync waiting",
            "working" => "Recovering match history",
            "cooldown" => "Waiting for Steam cooldown",
            "attention" => "History recovery needs attention",
            "caught_up" => "Available history processed",
            "retry" => state.Reason == "settling" ? "Waiting after game exit" : "Waiting to retry history recovery",
            _ => "Syncing stopped"
        };
        icon.Text = ("Deadlock Sync — " + title)[..Math.Min(63, ("Deadlock Sync — " + title).Length)];
        if (details is not null)
        {
            var p = evidence?.Progress ?? state.Progress;
            string next = RecoveryPolicy.Deadline(state) > Now
                ? DateTimeOffset.FromUnixTimeSeconds(RecoveryPolicy.Deadline(state)).ToLocalTime().ToString("f") : "When eligible";
            if (worker?.Mode == "backfill") next = "Running now";
            else if (game.Length > 0) next = RecoveryPolicy.Deadline(state) > Now
                ? "After game exit, no earlier than " + next : "After game exit and a short settling delay";
            details.Text = title + "\n\n" +
                $"Processed: {Count(p.Completed)} / {Count(p.Total)} missing matches\n" +
                $"Confirmed uploads this pass: {Count(p.Uploaded)}\nFailed this pass: {Count(p.Failed)}\n" +
                $"Unavailable older matches: {state.Skipped}\n" +
                $"Last update: {(p.UpdatedAtUtc is long stamp ? DateTimeOffset.FromUnixTimeSeconds(stamp).ToLocalTime().ToString("g") : "Waiting for activity")}\n" +
                $"Next history attempt: {(state.Pending ? next : "After your next game")}\n" +
                $"Reason: {Reason(state.Reason)}\n\n" +
                (error.Length > 0 ? error + "\n\n" : "") +
                "Live gameplay takes priority. Recovery uses Steam's remembered accounts.\n" +
                "Quit syncing hides the icon and retains a small next-game watcher. Exit app stops everything.\n\n" +
                $"Controller memory: {Process.GetCurrentProcess().WorkingSet64 / 1024 / 1024} MB";
        }
        string noticePhase = state.UploadHardUntilUtc > Now ? "cooldown" : phase;
        string noticeKey = noticePhase + ":" + state.ProcessedRunId + ":" + state.HardUntilUtc + ":" + state.UploadHardUntilUtc;
        if (state.Enabled && noticePhase != lastPhase && noticePhase is "cooldown" or "attention" or "caught_up" && noticeKey != state.LastNoticeKey)
        {
            state.LastNoticeKey = noticeKey;
            icon.ShowBalloonTip(4000, "Deadlock Sync", title + ". Click the icon for details.", ToolTipIcon.Info);
            try { Save(); } catch { }
        }
        lastPhase = noticePhase;
    }

    private static string Count(int? count) => count?.ToString("N0") ?? "Unknown";
    private static string Reason(string reason) => reason switch
    {
        "rate_limit" => "Steam throttled requests; the scheduled retry is not a guaranteed reset time.",
        "upload_rate_limit" => "The upload service throttled requests; both live uploads and history recovery are waiting.",
        "transient" => "Temporary connection or service failure.",
        "account_in_use" => "Steam account is busy playing a game.",
        "auth" => "Sign in to Steam with remembered login, finish Steam Guard, then choose Enable syncing.",
        "state_store" => "Local recovery or attempt storage needs repair. Existing records are preserved.",
        "unavailable" => "History permission or installation needs attention.",
        "settling" => "A short delay lets the game and Steam finish closing.",
        _ => "None"
    };

    private void ShowPanel()
    {
        if (panel is null || panel.IsDisposed)
        {
            panel = new Form { Text = "Deadlock Sync", ClientSize = new Size(570, 510), MinimumSize = new Size(500, 440),
                StartPosition = FormStartPosition.CenterScreen, ShowInTaskbar = true, Font = new Font("Segoe UI", 10) };
            var scroll = new Panel { Dock = DockStyle.Fill, AutoScroll = true };
            details = new Label { Dock = DockStyle.Top, Padding = new Padding(18), AutoSize = true,
                MaximumSize = new Size(panel.ClientSize.Width - 22, 0) };
            scroll.Controls.Add(details);
            scroll.Resize += (_, _) => details.MaximumSize = new Size(Math.Max(200, scroll.ClientSize.Width - 22), 0);
            var buttons = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 48, Padding = new Padding(10), FlowDirection = FlowDirection.RightToLeft };
            var close = new Button { Text = "Close", AutoSize = true };
            close.Click += (_, _) => panel.Hide();
            var enable = new Button { Text = "Enable syncing", AutoSize = true };
            enable.Click += async (_, _) => await ActionAsync("enable");
            var quit = new Button { Text = "Quit syncing", AutoSize = true };
            quit.Click += async (_, _) => await ActionAsync("quit");
            buttons.Controls.AddRange([close, enable, quit]);
            panel.Controls.Add(scroll); panel.Controls.Add(buttons);
            panel.FormClosing += (_, e) => { if (!exiting) { e.Cancel = true; panel.Hide(); } };
        }
        Render(); panel.Show(); panel.Activate();
    }

    private static Icon TrayIcon()
    {
        using var bitmap = new Bitmap(32, 32);
        using (var g = Graphics.FromImage(bitmap))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);
            using var fill = new SolidBrush(Color.FromArgb(68, 151, 238));
            g.FillRoundedRectangle(fill, new RectangleF(2, 7, 28, 19), new SizeF(6, 6));
            using var white = new Pen(Color.White, 3);
            g.DrawLine(white, 6, 16, 14, 16); g.DrawLine(white, 10, 12, 10, 20);
            g.FillEllipse(Brushes.White, 21, 12, 4, 4); g.FillEllipse(Brushes.White, 24, 18, 4, 4);
        }
        IntPtr handle = bitmap.GetHicon();
        try { using var temporary = Icon.FromHandle(handle); return (Icon)temporary.Clone(); }
        finally { DestroyIcon(handle); }
    }

    private void SessionEnding(object? sender, Microsoft.Win32.SessionEndingEventArgs e)
    {
        worker?.Stop(); // Job ownership also handles abrupt session teardown.
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            exiting = true; timer.Stop(); timer.Dispose();
            Microsoft.Win32.SystemEvents.SessionEnding -= SessionEnding;
            worker?.Dispose(); icon.Visible = false; icon.Dispose(); image.Dispose(); panel?.Dispose(); dispatch.Dispose(); gate.Dispose();
        }
        base.Dispose(disposing);
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")] private static extern bool DestroyIcon(IntPtr handle);
}
