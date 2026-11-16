using System.Text.Json;
using System.Text.Json.Nodes;
using DeadlockSync.Core;

int passed = 0, failed = 0;
const long Now = 1_800_000_000;
Func<int, int, int> zero = (_, _) => 0;
Func<int, int, int> max = (_, high) => high;
void Check(bool condition, string detail = "assertion failed")
{
    if (!condition) throw new Exception(detail);
}
void Equal<T>(T expected, T actual) => Check(EqualityComparer<T>.Default.Equals(expected, actual),
    $"expected {expected}, got {actual}");
void Test(string name, Action run)
{
    try { run(); passed++; Console.WriteLine("PASS " + name); }
    catch (Exception ex) { failed++; Console.Error.WriteLine("FAIL " + name + ": " + ex.Message); }
}
RunEvidence Evidence(string line = "", long at = Now)
{
    RunEvidence value = new();
    if (line != "") value.AddLine(line, at);
    return value;
}
void StoreTest(Action<StateStore, string> test)
{
    string directory = Path.Combine(Path.GetTempPath(), "DeadlockSync-tests-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(directory);
    try { test(new StateStore(Path.Combine(directory, "state.json")), directory); }
    finally { Directory.Delete(directory, true); }
}

Test("fresh disabled with history pending", () =>
{
    SyncState state = new();
    Check(!state.Enabled && state.Pending && !state.Corrupt);
    Check(!RecoveryPolicy.CanBackfill(state, "", Now));
});
Test("enable makes pending history eligible", () =>
{
    SyncState state = new(); RecoveryPolicy.Enable(state, Now);
    Check(RecoveryPolicy.CanBackfill(state, "", Now));
});
Test("game starts live and prevents history", () =>
{
    SyncState state = new();
    Equal(GameTransition.Started, RecoveryPolicy.ObserveGame(state, "123:456", Now));
    Check(RecoveryPolicy.CanLive(state, "123:456"));
    Check(!RecoveryPolicy.CanBackfill(state, "123:456", Now));
    Equal(GameTransition.None, RecoveryPolicy.ObserveGame(state, "123:456", Now));
});
Test("game exit stops live and waits grace", () =>
{
    SyncState state = new(); RecoveryPolicy.ObserveGame(state, "1:2", Now);
    Equal(GameTransition.Ended, RecoveryPolicy.ObserveGame(state, "", Now + 10));
    Check(!RecoveryPolicy.CanLive(state, ""));
    Equal(Now + 70, state.NextAtUtc);
    Check(!RecoveryPolicy.CanBackfill(state, "", Now + 69));
    Check(RecoveryPolicy.CanBackfill(state, "", Now + 70));
});
Test("manual quit suppresses same game; new identity reenables", () =>
{
    SyncState state = new(); RecoveryPolicy.ObserveGame(state, "1:2", Now);
    RecoveryPolicy.Quit(state, "1:2");
    RecoveryPolicy.ObserveGame(state, "1:2", Now + 1);
    Check(!RecoveryPolicy.CanLive(state, "1:2"));
    RecoveryPolicy.ObserveGame(state, "", Now + 2);
    Check(!state.Enabled);
    RecoveryPolicy.ObserveGame(state, "1:3", Now + 3);
    Check(RecoveryPolicy.CanLive(state, "1:3"));
});
Test("explicit enable clears same-game suppression", () =>
{
    SyncState state = new(); RecoveryPolicy.Quit(state, "1:2");
    RecoveryPolicy.Enable(state, Now);
    Check(RecoveryPolicy.CanLive(state, "1:2"));
});
Test("rate limit uses observed time with zero jitter", () =>
{
    SyncState state = new();
    RecoveryPolicy.Finish(state, "run1", 1, Evidence("Steam rate limit reached.", Now - 100), Now, random: zero);
    Equal(Now - 100 + 86_400, state.HardUntilUtc);
    Equal(state.HardUntilUtc, state.NextAtUtc); Equal("rate_limit", state.Reason);
});
Test("positive jitter inclusive upper bound", () =>
{
    SyncState state = new();
    RecoveryPolicy.Finish(state, "run1", 1, Evidence("Steam rate limit reached."), Now, random: max);
    Equal(Now + 86_700, state.HardUntilUtc);
});
Test("future log time clamped", () =>
{
    SyncState state = new();
    RecoveryPolicy.Finish(state, "run1", 1, Evidence("Steam rate limit reached.", Now + 999), Now, random: zero);
    Equal(Now + 86_400, state.HardUntilUtc);
});
Test("cooldown persists through quit enable and game", () =>
{
    SyncState state = new();
    RecoveryPolicy.Finish(state, "r", 1, Evidence("Steam rate limit reached."), Now, random: zero);
    long deadline = state.HardUntilUtc;
    RecoveryPolicy.Quit(state, ""); RecoveryPolicy.Enable(state, Now + 1);
    RecoveryPolicy.ObserveGame(state, "1:2", Now + 2); RecoveryPolicy.ObserveGame(state, "", Now + 3);
    Equal(deadline, state.HardUntilUtc); Equal(deadline, state.NextAtUtc);
});
Test("transient retry doubles to one-hour cap", () =>
{
    SyncState state = new();
    foreach (int delay in new[] { 300, 600, 1200, 2400, 3600, 3600 })
    {
        RecoveryPolicy.Retry(state, Now, random: zero);
        Equal(Now + delay, state.NextAtUtc);
    }
});
Test("account busy starts at 15 minutes", () =>
{
    SyncState state = new();
    foreach (int delay in new[] { 900, 1800, 3600, 3600 })
    {
        RecoveryPolicy.Retry(state, Now, "account_in_use", zero);
        Equal(Now + delay, state.NextAtUtc);
    }
});
Test("retry never shortens hard cooldown", () =>
{
    SyncState state = new() { HardUntilUtc = Now + 80_000 };
    RecoveryPolicy.Retry(state, Now, random: max);
    Equal(state.HardUntilUtc, state.NextAtUtc);
});
Test("known auth blocks but live remains possible", () =>
{
    SyncState state = new() { Enabled = true };
    RecoveryPolicy.Finish(state, "r", 1, Evidence("Steam logon failed: InvalidPassword"), Now, random: zero);
    Equal("auth", state.Reason); Check(!RecoveryPolicy.CanBackfill(state, "", Now));
    RecoveryPolicy.ObserveGame(state, "1:2", Now); Check(RecoveryPolicy.CanLive(state, "1:2"));
    RecoveryPolicy.Enable(state, Now); Equal("", state.Reason);
});
Test("corrupt state cannot be cleared with enable", () =>
{
    SyncState state = new() { Corrupt = true, Reason = "state_store" };
    RecoveryPolicy.Enable(state, Now); Check(!RecoveryPolicy.CanBackfill(state, "", Now));
    RecoveryPolicy.Finish(state, "r", 0, Evidence(), Now);
    Equal("state_store", state.Reason); Check(state.Pending);
});
Test("success waits for subsequent gameplay", () =>
{
    SyncState state = new() { Enabled = true };
    RecoveryPolicy.Finish(state, "r", 0, Evidence(), Now);
    Check(!state.Pending && !RecoveryPolicy.CanBackfill(state, "", Now + 100_000));
    Equal("caught_up", RecoveryPolicy.Phase(state, "", Now));
    RecoveryPolicy.ObserveGame(state, "1:2", Now + 1); Check(state.Pending);
});
Test("skipped old matches do not block future history", () =>
{
    SyncState state = new() { Enabled = true };
    RecoveryPolicy.Finish(state, "r", 0,
        Evidence("gc: account 123 skips 4 match(es) given up on after 3 attempts"), Now);
    Equal(4, state.Skipped); Check(!state.Pending);
    RecoveryPolicy.ObserveGame(state, "1:2", Now); Check(state.Pending);
});
Test("deliberate takeover gets grace, no failure increment", () =>
{
    SyncState state = new();
    RecoveryPolicy.Finish(state, "r", -1, Evidence(), Now, deliberate: true, random: zero);
    Equal(0, state.Failures); Equal(Now + 60, state.NextAtUtc); Equal("settling", state.Reason);
});
Test("deliberate stop still honors throttle", () =>
{
    SyncState state = new();
    RecoveryPolicy.Finish(state, "r", -1, Evidence("Steam rate limit reached."), Now, true, zero);
    Equal(Now + 86_400, state.HardUntilUtc); Equal("rate_limit", state.Reason);
});
Test("completion consumed once", () =>
{
    SyncState state = new(); RunEvidence evidence = Evidence("Steam rate limit reached.");
    Check(RecoveryPolicy.Finish(state, "r", 1, evidence, Now, random: zero));
    Check(!RecoveryPolicy.Finish(state, "r", 1, evidence, Now + 1000, random: max));
    Equal(Now + 86_400, state.HardUntilUtc);
});
Test("reservation prevents immediate spawn retry", () =>
{
    SyncState state = new() { Enabled = true };
    RecoveryPolicy.Reserve(state, Now);
    Check(!RecoveryPolicy.CanBackfill(state, "", Now));
    Check(RecoveryPolicy.CanBackfill(state, "", Now + 300));
});
Test("late throttle survives stronger store failure", () =>
{
    RunEvidence evidence = Evidence("GC store error");
    evidence.AddLine("Steam rate limit reached.", Now + 1);
    Equal("state_store", evidence.Cause);
    SyncState state = new();
    RecoveryPolicy.Finish(state, "r", 1, evidence, Now + 2, random: zero);
    Equal("state_store", state.Reason); Equal(Now + 1 + 86_400, state.HardUntilUtc);
});
Test("elapsed cooldown phase becomes retry", () =>
{
    SyncState state = new() { Enabled = true, Reason = "rate_limit", HardUntilUtc = Now };
    Equal("retry", RecoveryPolicy.Phase(state, "", Now));
    Equal("cooldown", RecoveryPolicy.Phase(state, "", Now - 1));
});
Test("processed index differs from uploads", () =>
{
    RunEvidence evidence = Evidence("gc: account 123 has 50 match(es) in history, 10 missing salts");
    evidence.AddLine("gc: salt fetch failed for 456: timeout", Now);
    evidence.AddLine("gc: ingested match 789 (2/10)", Now);
    Equal<int?>(2, evidence.Progress.Completed); Equal<int?>(1, evidence.Progress.Uploaded);
    Equal<int?>(1, evidence.Progress.Failed); Equal<int?>(50, evidence.Progress.History);
});
Test("missing baseline retains unknown upload total", () =>
{
    RunEvidence evidence = Evidence("gc: ingested match 789 (8/10)");
    Equal<int?>(8, evidence.Progress.Completed);
    Check(evidence.Progress.Uploaded is null && evidence.Progress.Failed is null);
});
Test("multiple account summaries aggregate current pass", () =>
{
    RunEvidence evidence = Evidence("gc: account 123 has 50 match(es) in history, 10 missing salts");
    evidence.AddLine("gc: ingested match 789 (1/10)", Now);
    evidence.AddLine("gc: account 321 has 20 match(es) in history, 2 missing salts", Now);
    Equal<int?>(1, evidence.Progress.Uploaded); Equal<int?>(12, evidence.Progress.Total);
    Equal<int?>(1, evidence.Progress.Completed); Equal<int?>(70, evidence.Progress.History);
    evidence.AddLine("gc: ingested match 987 (1/2)", Now);
    Equal<int?>(2, evidence.Progress.Completed); Equal<int?>(2, evidence.Progress.Uploaded);
});
Test("ANSI handled and arbitrary log ignored", () =>
{
    RunEvidence evidence = Evidence("\u001b[31mSteam rate limit reached.\u001b[0m");
    Equal("rate_limit", evidence.Cause);
    evidence.AddLine(new string('x', 5000), Now);
    Equal("rate_limit", evidence.Cause);
});
Test("impossible numeric logs ignored", () =>
{
    RunEvidence evidence = Evidence("gc: account 1 has 1 match(es) in history, 10 missing salts");
    Check(evidence.Progress.Total is null);
    evidence.AddLine("gc: ingested match 3 (999999999999999999999999/10)", Now);
    Check(evidence.Progress.Completed is null);
});
Test("live phases have no fictitious percent", () =>
{
    RunEvidence evidence = new("live");
    evidence.AddLine("memory: reading the game (pid 123)", Now);
    Equal("reading", evidence.Progress.Phase); Check(evidence.Progress.Total is null);
    evidence.AddLine("memory: ingested salts: 2", Now); Equal("waiting", evidence.Progress.Phase);
    evidence.AddLine("memory: not allowed to read Deadlock's memory: denied", Now);
    Equal("failed", evidence.Progress.Phase);
});
Test("completion marker is separate from exit code", () =>
{
    RunEvidence evidence = Evidence("Done, all own matches processed.");
    Check(evidence.Complete);
    SyncState state = new();
    RecoveryPolicy.Finish(state, "r", 1, evidence, Now, random: zero);
    Equal("transient", state.Reason);
});
Test("known account failure despite zero exit retries", () =>
{
    SyncState state = new();
    RecoveryPolicy.Finish(state, "r", 0, Evidence("gc: account 123 failed: network timeout"), Now, random: zero);
    Check(state.Pending); Equal("transient", state.Reason);
});
Test("missing state is disabled without writes", () => StoreTest((store, _) =>
{
    Check(!store.Load().Enabled); Check(!File.Exists(store.Path));
}));
Test("state round trip preserves deadline and suppression", () => StoreTest((store, _) =>
{
    SyncState state = new() { Enabled = false, HardUntilUtc = Now + 100,
        NextAtUtc = Now + 100, LastGameIdentity = "1:2", SuppressedGameIdentity = "1:2" };
    store.Save(state); SyncState loaded = store.Load();
    Check(!loaded.Corrupt); Equal(state.HardUntilUtc, loaded.HardUntilUtc);
    Equal("1:2", loaded.SuppressedGameIdentity); Equal("1:2", loaded.LastGameIdentity);
}));
Test("invalid JSON fails closed and preserves original", () => StoreTest((store, _) =>
{
    File.WriteAllText(store.Path, "{damaged");
    SyncState loaded = store.Load(); Check(loaded.Corrupt && loaded.Enabled);
    store.Save(loaded); Equal("{damaged", File.ReadAllText(store.Path + ".invalid"));
    Check(store.Load().Corrupt);
}));
Test("missing mandatory deadline fails closed", () => StoreTest((store, _) =>
{
    JsonObject json = JsonSerializer.SerializeToNode(new SyncState())!.AsObject(); json.Remove("HardUntilUtc");
    File.WriteAllText(store.Path, json.ToJsonString()); Check(store.Load().Corrupt);
}));
Test("invalid field retains valid known cooldown", () => StoreTest((store, _) =>
{
    JsonObject json = JsonSerializer.SerializeToNode(new SyncState { HardUntilUtc = Now + 100 })!.AsObject();
    json["Enabled"] = "yes"; File.WriteAllText(store.Path, json.ToJsonString());
    SyncState loaded = store.Load(); Check(loaded.Corrupt); Equal(Now + 100, loaded.HardUntilUtc);
}));
Test("wrong numeric types fail closed without throwing", () => StoreTest((store, _) =>
{
    JsonObject json = JsonSerializer.SerializeToNode(new SyncState())!.AsObject();
    json["NextAtUtc"] = "tomorrow"; File.WriteAllText(store.Path, json.ToJsonString());
    Check(store.Load().Corrupt);
}));
Test("duplicate fields rejected", () => StoreTest((store, _) =>
{
    string json = JsonSerializer.Serialize(new SyncState());
    File.WriteAllText(store.Path, json.Insert(1, "\"HardUntilUtc\":0,"));
    Check(store.Load().Corrupt);
}));
Test("oversized state rejected", () => StoreTest((store, _) =>
{
    File.WriteAllText(store.Path, new string(' ', 16_385)); Check(store.Load().Corrupt);
}));
Test("atomic save leaves no temporary files", () => StoreTest((store, directory) =>
{
    store.Save(new()); store.Save(new() { Enabled = true });
    Check(store.Load().Enabled); Equal(1, Directory.GetFiles(directory).Length);
}));
Test("sleep resume deadline produces one eligible reservation", () =>
{
    SyncState state = new() { Enabled = true, NextAtUtc = Now };
    Check(RecoveryPolicy.CanBackfill(state, "", Now + 10_000));
    RecoveryPolicy.Reserve(state, Now + 10_000);
    Check(!RecoveryPolicy.CanBackfill(state, "", Now + 10_000));
});
Test("exact upstream and HTTP throttles recognized", () =>
{
    foreach (string line in new[] { "Steam GC rate-limited the request",
        "gc: match history rate-limited, continuing with 100 match(es)",
        "HTTP 429 Too Many Requests", "request failed: status code: 429",
        "server HTTP/1.1 429 response" })
        Equal("rate_limit", Evidence(line).Cause);
    Equal("", Evidence("gc: ingested match 429 (1/10)").Cause);
});
Test("truncation discards only uncertain counters", () =>
{
    RunEvidence evidence = Evidence("gc: account 1 has 50 match(es) in history, 10 missing salts");
    evidence.AddLine("gc: ingested match 2 (1/10)", Now);
    evidence.MarkTruncated();
    evidence.AddLine("gc: ingested match 3 (5/10)", Now);
    Equal<int?>(5, evidence.Progress.Completed);
    Check(evidence.Progress.Uploaded is null && evidence.Progress.Failed is null);
});
Test("mid-run throttle survives crash state reload", () => StoreTest((store, _) =>
{
    SyncState state = new() { Enabled = true };
    RunEvidence evidence = Evidence("Steam GC rate-limited the request");
    RecoveryPolicy.ObserveEvidence(state, evidence, Now, zero); store.Save(state);
    SyncState restored = store.Load();
    Equal(Now + 86_400, restored.HardUntilUtc);
    Check(!RecoveryPolicy.CanBackfill(restored, "", Now + 301));
}));
Test("mid-run store failure blocks after reload", () => StoreTest((store, _) =>
{
    SyncState state = new() { Enabled = true };
    RecoveryPolicy.ObserveEvidence(state, Evidence("GC store error"), Now, zero); store.Save(state);
    Check(!RecoveryPolicy.CanBackfill(store.Load(), "", Now + 100_000));
}));
Test("explicit enable retries repaired private history without clearing deadline", () =>
{
    SyncState state = new() { Enabled = true, Reason = "unavailable", HardUntilUtc = Now + 100 };
    RecoveryPolicy.Enable(state, Now);
    Equal("", state.Reason); Equal(Now + 100, state.NextAtUtc);
    Check(!RecoveryPolicy.CanBackfill(state, "", Now));
});
Test("later account cannot restore truncated pass upload counters", () =>
{
    RunEvidence evidence = Evidence("gc: ingested match 1 (2/3)");
    evidence.AddLine("gc: account 321 has 20 match(es) in history, 2 missing salts", Now);
    evidence.AddLine("gc: ingested match 987 (1/2)", Now);
    Equal<int?>(3, evidence.Progress.Completed); Equal<int?>(5, evidence.Progress.Total);
    Check(evidence.Progress.Uploaded is null && evidence.Progress.History is null);
});
Test("Steam GC throttle permits live but endpoint throttle blocks both", () =>
{
    SyncState state = new() { Enabled = true };
    RecoveryPolicy.ObserveEvidence(state, Evidence("Steam GC rate-limited the request"), Now, zero);
    Check(RecoveryPolicy.CanLive(state, "1:2", Now));
    Check(!RecoveryPolicy.CanBackfill(state, "", Now));
    RunEvidence http = Evidence("POST failed: HTTP 429 Too Many Requests");
    Check(http.EndpointRateLimited);
    RecoveryPolicy.ObserveEvidence(state, http, Now, zero);
    Check(!RecoveryPolicy.CanLive(state, "1:2", Now));
    Equal(Now + 86_400, state.UploadHardUntilUtc);
    Equal("upload_rate_limit", state.Reason);
    Check(RecoveryPolicy.CanLive(state, "1:2", Now + 86_400));
});
Test("upload cooldown survives toggle and reload", () => StoreTest((store, _) =>
{
    SyncState state = new() { Enabled = true };
    RecoveryPolicy.ObserveEvidence(state, Evidence("HTTP status: 429"), Now, zero);
    RecoveryPolicy.Quit(state, "1:2"); RecoveryPolicy.Enable(state, Now);
    RecoveryPolicy.ObserveGame(state, "1:3", Now); store.Save(state);
    state = store.Load(); Check(!state.Corrupt);
    Check(!RecoveryPolicy.CanLive(state, "1:3", Now + 300));
    Check(!RecoveryPolicy.CanBackfill(state, "", Now + 300));
    Equal(Now + 86_400, state.UploadHardUntilUtc);
}));
Test("endpoint evidence retained even with stronger storage failure", () =>
{
    SyncState state = new() { Enabled = true };
    RunEvidence evidence = Evidence("GC store error");
    evidence.AddLine("HTTP 429 Too Many Requests", Now);
    RecoveryPolicy.Finish(state, "r", 1, evidence, Now, random: zero);
    Equal("state_store", state.Reason);
    Check(!RecoveryPolicy.CanLive(state, "1:2", Now));
});
Test("missing upload deadline fails closed", () => StoreTest((store, _) =>
{
    JsonObject json = JsonSerializer.SerializeToNode(new SyncState())!.AsObject(); json.Remove("UploadHardUntilUtc");
    File.WriteAllText(store.Path, json.ToJsonString()); Check(store.Load().Corrupt);
}));
Test("drain timeout cannot report caught up on zero exit", () =>
{
    RunEvidence evidence = Evidence("Done, all own matches processed.");
    evidence.MarkIncomplete();
    SyncState state = new() { Enabled = true };
    RecoveryPolicy.Finish(state, "r", 0, evidence, Now, random: zero);
    Check(state.Pending); Equal("transient", state.Reason); Check(!evidence.Complete);
});
Test("incomplete output cannot erase known rate limit", () =>
{
    RunEvidence evidence = Evidence("HTTP 429 Too Many Requests");
    evidence.MarkIncomplete();
    SyncState state = new() { Enabled = true };
    RecoveryPolicy.Finish(state, "r", 0, evidence, Now, random: zero);
    Check(!RecoveryPolicy.CanLive(state, "1:2", Now));
    Equal("upload_rate_limit", state.Reason);
});
Test("explicit HTTP access failures need attention", () =>
{
    foreach (string line in new[] { "HTTP 401", "HTTP/1.1 403 Forbidden",
        "request failed: HTTP status: 403", "client error (401 Unauthorized)" })
    {
        RunEvidence evidence = Evidence(line);
        Check(evidence.EndpointAccessDenied); Equal("unavailable", evidence.Cause);
        SyncState state = new() { Enabled = true };
        RecoveryPolicy.Finish(state, "r", 1, evidence, Now, random: zero);
        Check(!RecoveryPolicy.CanBackfill(state, "", Now + 86_400));
    }
});
Test("account numbers and memory errors are not HTTP access failures", () =>
{
    foreach (string line in new[] { "gc: ingested match 403 (1/10)",
        "memory: failed with error 401", "memory: permission denied, status: 403" })
        Check(!Evidence(line).EndpointAccessDenied);
    RunEvidence steam = Evidence("Steam logon failed: AccessDenied");
    Check(!steam.EndpointAccessDenied); Equal("auth", steam.Cause);
});

Console.WriteLine($"{passed} passed; {failed} failed.");
return failed == 0 ? 0 : 1;
