namespace DeadlockSync.Core;

public sealed record AccountView(string Phase, string AccountId, string Reason, long NextAtUtc,
    ProgressSnapshot Progress, int Skipped);

public sealed record AccountRowView(string Id, string Name, bool IsCurrent, string Phase, string Reason,
    long NextAtUtc, int Skipped, ProgressSnapshot? ActiveProgress, ProgressSnapshot? LastPass);

/// <summary>One selected account per worker; only upload-service restrictions are shared.</summary>
public static class AccountPolicy
{
    public const int AccountLimit = 64;
    // Integer Unix seconds round down; 21 guarantees at least 20 real seconds.
    public const int WorkerSpacing = 21;
    public static bool ValidId(string id) => uint.TryParse(id, out uint value) && value != 0
        && value.ToString(System.Globalization.CultureInfo.InvariantCulture) == id;

    public static bool ObserveAccounts(SyncState state, IReadOnlyCollection<string> available, string current)
    {
        if (available.Count > AccountLimit || available.Any(id => !ValidId(id)))
            throw new InvalidDataException("Steam account metadata needs review.");
        foreach (string id in available)
        {
            if (state.Accounts.ContainsKey(id)) continue;
            if (state.Accounts.Count >= AccountLimit)
                throw new InvalidDataException("Saved account history reached its safety limit; no records were removed.");
            state.Accounts.Add(id, new RecoveryState());
        }
        current = available.Contains(current) ? current : "";
        if (state.CurrentAccountId == current) return false;
        state.CurrentAccountId = current;
        if (current.Length != 0)
        {
            var account = state.Accounts[current];
            account.Pending = true;
            // A fresh sign-in can repair authentication, never a known Steam cooldown.
            if (account.Reason == "auth") { account.Reason = ""; account.NextAtUtc = account.HardUntilUtc; }
        }
        return true;
    }

    public static void Enable(SyncState state, long now)
    {
        RecoveryPolicy.Enable(state, now);
        foreach (var account in state.Accounts.Values)
            if (RecoveryPolicy.IsBlocked(account.Reason) && !account.Corrupt)
            { account.Reason = ""; account.Pending = true; account.NextAtUtc = Math.Max(now, account.HardUntilUtc); }
    }

    public static void RetryCurrent(SyncState state, long now)
    {
        bool retryStorage = state.Reason == "state_store";
        bool retryService = state.Reason == "unavailable";
        RecoveryPolicy.Enable(state, now);
        if (state.Corrupt) return;
        foreach (var (id, account) in state.Accounts)
        {
            bool retrySelected = id == state.CurrentAccountId && (account.Reason == "auth" ||
                retryService && account.Reason == "unavailable");
            if (account.Corrupt || !(retrySelected || retryStorage && account.Reason == "state_store")) continue;
            account.Pending = true;
            account.Reason = "";
            account.NextAtUtc = Math.Max(now, Math.Max(account.HardUntilUtc, account.UploadHardUntilUtc));
        }
    }

    public static GameTransition ObserveGame(SyncState state, string identity, long now)
    {
        if (identity == state.LastGameIdentity) return GameTransition.None;
        state.LastGameIdentity = identity; state.SuppressedGameIdentity = "";
        if (identity.Length != 0)
        {
            state.Enabled = true;
            if (state.Accounts.TryGetValue(state.CurrentAccountId, out var current)) current.Pending = true;
            return GameTransition.Started;
        }
        if (state.Enabled) state.SettlingUntilUtc = Math.Max(state.SettlingUntilUtc, now + RecoveryPolicy.Grace);
        return GameTransition.Ended;
    }

    public static long SharedDeadline(SyncState state) => Math.Max(Math.Max(state.HardUntilUtc,
        state.UploadHardUntilUtc), Math.Max(state.SettlingUntilUtc, state.NextWorkerUtc));
    public static bool GlobalBlocked(SyncState state) => state.Corrupt || state.Reason is "state_store" or "unavailable";
    private static bool Due(RecoveryState account, long now) => account.Pending && !account.Corrupt
        && !RecoveryPolicy.IsBlocked(account.Reason) && RecoveryPolicy.Deadline(account) <= now;

    public static string Select(SyncState state, IReadOnlyCollection<string> available, long now)
    {
        if (!state.Enabled || GlobalBlocked(state) || SharedDeadline(state) > now) return "";
        return Ordered(state, available).FirstOrDefault(id => Due(state.Accounts[id], now)) ?? "";
    }

    public static bool ShouldPreempt(SyncState state, string runningAccount, IReadOnlyCollection<string> available, long now)
    {
        if (!available.Contains(runningAccount)) return true;
        return state.CurrentAccountId != runningAccount && available.Contains(state.CurrentAccountId)
            && state.Accounts.TryGetValue(state.CurrentAccountId, out var current) && Due(current, now)
            && state.HardUntilUtc <= now && state.UploadHardUntilUtc <= now && !GlobalBlocked(state);
    }

    private static IEnumerable<string> Ordered(SyncState state, IReadOnlyCollection<string> available) =>
        available.Where(state.Accounts.ContainsKey)
            .OrderBy(id => id == state.CurrentAccountId ? 0 : 1)
            .ThenBy(id => state.Accounts[id].LastServedUtc).ThenBy(id => uint.Parse(id));

    public static void Reserve(SyncState state, string accountId, long now)
    {
        var account = state.Accounts[accountId];
        RecoveryPolicy.Reserve(account, now); account.LastServedUtc = now;
        state.NextWorkerUtc = Math.Max(state.NextWorkerUtc, now + WorkerSpacing);
        state.ActiveAccountId = accountId;
    }

    public static void RecoverInterrupted(SyncState state, long now)
    {
        if (state.ActiveAccountId.Length == 0) return;
        // A process could have sent its last request immediately before controller exit.
        // Its Job Object closes with the old controller; wait a full interval afresh.
        state.NextWorkerUtc = Math.Max(state.NextWorkerUtc, now + WorkerSpacing);
        if (state.Accounts.TryGetValue(state.ActiveAccountId, out var account)) account.Pending = true;
        state.ActiveAccountId = "";
    }

    public static void ObserveEvidence(SyncState state, string accountId, RunEvidence evidence, long now,
        Func<int, int, int>? random = null)
    {
        RecoveryState account = accountId.Length != 0 ? state.Accounts[accountId] : state;
        RecoveryPolicy.ObserveEvidence(account, evidence, now, random);
        state.UploadHardUntilUtc = Math.Max(state.UploadHardUntilUtc, account.UploadHardUntilUtc);
        if (evidence.StorageFailure) state.Reason = "state_store";
        else if (evidence.EndpointAccessDenied) state.Reason = "unavailable";
    }

    public static void Finish(SyncState state, string accountId, string runId, int exitCode, RunEvidence evidence,
        long now, bool deliberate = false, Func<int, int, int>? random = null)
    {
        var account = state.Accounts[accountId];
        ObserveEvidence(state, accountId, evidence, now, random);
        if (!RecoveryPolicy.Finish(account, runId, exitCode, evidence, now, deliberate, random)) return;
        state.UploadHardUntilUtc = Math.Max(state.UploadHardUntilUtc, account.UploadHardUntilUtc);
        state.NextWorkerUtc = Math.Max(state.NextWorkerUtc, now + WorkerSpacing);
        state.ActiveAccountId = "";
        // A confirmed, intentional stop releases the crash reservation but never hard limits.
        if (deliberate && !evidence.RateLimited && !RecoveryPolicy.IsBlocked(account.Reason))
        { account.NextAtUtc = Math.Max(account.HardUntilUtc, now + WorkerSpacing); account.Reason = "settling"; }
    }

    public static AccountView View(SyncState state, IReadOnlyCollection<string> available, string game,
        long now, string mode = "", string runningAccount = "")
    {
        string id = mode == "backfill" ? runningAccount : Select(state, available, now);
        if (id.Length == 0)
            id = Ordered(state, available).Where(x => state.Accounts[x].Pending && !RecoveryPolicy.IsBlocked(state.Accounts[x].Reason))
                .OrderBy(x => RecoveryPolicy.Deadline(state.Accounts[x])).FirstOrDefault()
                ?? Ordered(state, available).FirstOrDefault(x => state.Accounts[x].Pending && (x == state.CurrentAccountId || state.Accounts[x].Reason != "auth"))
                ?? Ordered(state, available).FirstOrDefault(x => state.Accounts[x].Pending)
                ?? Ordered(state, available).FirstOrDefault() ?? "";
        RecoveryState target = state.Accounts.GetValueOrDefault(id) ?? state;
        long next = Math.Max(SharedDeadline(state), RecoveryPolicy.Deadline(target));
        string reason = target.Reason, phase;
        if (!state.Enabled) phase = "off";
        else if (game.Length > 0 || mode == "live") phase = "game";
        else if (mode == "backfill") phase = "working";
        else if (GlobalBlocked(state)) { phase = "attention"; reason = state.Reason; }
        else if (state.UploadHardUntilUtc > now) { phase = "cooldown"; reason = "upload_rate_limit"; }
        else if (state.HardUntilUtc > now) { phase = "cooldown"; reason = "legacy_cooldown"; }
        else if (available.Count == 0) { phase = "attention"; reason = "no_accounts"; }
        else if (!available.Any(x => state.Accounts[x].Pending)) phase = "caught_up";
        else if (target.Reason == "auth" && id != state.CurrentAccountId)
        {
            if (available.Contains(state.CurrentAccountId) && !state.Accounts[state.CurrentAccountId].Pending)
            {
                var current = state.Accounts[state.CurrentAccountId];
                return new("waiting_for_account", state.CurrentAccountId, "", 0, current.Progress, current.Skipped);
            }
            phase = "attention";
            reason = "choose_account";
        }
        else if (RecoveryPolicy.IsBlocked(target.Reason)) phase = "attention";
        else if (target.HardUntilUtc > now) phase = "cooldown";
        else { phase = "retry"; if (state.SettlingUntilUtc > now || state.NextWorkerUtc > now) reason = "settling"; }
        if (state.Enabled && state.UploadHardUntilUtc > now) reason = "upload_rate_limit";
        return new(phase, id, reason, next, target.Progress, target.Skipped);
    }

    public static IReadOnlyList<AccountRowView> Rows(SyncState state, SteamAccounts accounts, long now,
        string mode = "", string runningAccount = "", ProgressSnapshot? currentProgress = null)
    {
        var rows = new List<AccountRowView>(accounts.Accounts.Count);
        foreach (var account in accounts.Accounts.OrderByDescending(a => a.Id == state.CurrentAccountId))
        {
            if (!state.Accounts.TryGetValue(account.Id, out var item)) continue;
            bool current = account.Id == state.CurrentAccountId;
            string phase;
            if (mode == "backfill" && account.Id == runningAccount) phase = "working";
            else if (item.Corrupt) phase = "attention";
            else if (item.Reason == "auth") phase = current ? "auth" : "choose_account";
            else if (RecoveryPolicy.IsBlocked(item.Reason)) phase = "attention";
            else if (item.Pending && item.HardUntilUtc > now) phase = "cooldown";
            else if (mode == "live" && current) phase = "live";
            else phase = item.Pending ? "retry" : "caught_up";

            long due = RecoveryPolicy.Deadline(item);
            bool rowDeadline = phase is "cooldown" or "retry" && due > now && due != state.UploadHardUntilUtc;
            var stored = item.Progress;
            bool hasPass = stored.Mode == "backfill" && (stored.Completed.HasValue || stored.Total.HasValue ||
                stored.Uploaded.HasValue || stored.Failed.HasValue || stored.History.HasValue ||
                stored.Phase == "complete" && stored.UpdatedAtUtc > 0);
            rows.Add(new(account.Id, account.Name, current, phase, item.Reason, rowDeadline ? due : 0, item.Skipped,
                phase is "working" or "live" && currentProgress?.Mode == mode ? currentProgress : null,
                phase != "working" && hasPass ? stored : null));
        }
        return rows;
    }
}
