namespace DeadlockSync.Core;

/// <summary>Recovery outcomes and legacy lifecycle primitives. Multi-account scheduling uses AccountPolicy.</summary>
public static class RecoveryPolicy
{
    public const long Day = 86_400;
    public const long Grace = 60;
    public static bool IsBlocked(string reason) => reason is "auth" or "state_store" or "unavailable";
    private static long HardDeadline(RecoveryState state) => Math.Max(state.HardUntilUtc, state.UploadHardUntilUtc);
    public static long Deadline(RecoveryState state) => Math.Max(state.NextAtUtc, HardDeadline(state));
    private static int Jitter(Func<int, int, int>? random, int maximum) =>
        Math.Clamp(random?.Invoke(0, maximum) ?? Random.Shared.Next(maximum + 1), 0, maximum);

    public static void Enable(SyncState state, long now)
    {
        state.Enabled = true;
        state.SuppressedGameIdentity = "";
        if (IsBlocked(state.Reason) && !state.Corrupt)
        {
            state.Reason = "";
            state.Pending = true;
            state.NextAtUtc = Math.Max(now, HardDeadline(state));
        }
        if (state.Corrupt) { state.Reason = "state_store"; state.Pending = true; }
    }

    public static void Quit(SyncState state, string gameIdentity)
    {
        state.Enabled = false;
        state.LastGameIdentity = gameIdentity;
        state.SuppressedGameIdentity = gameIdentity;
    }

    public static GameTransition ObserveGame(SyncState state, string identity, long now)
    {
        if (identity == state.LastGameIdentity) return GameTransition.None;
        bool started = identity.Length != 0;
        state.LastGameIdentity = identity;
        state.SuppressedGameIdentity = "";
        if (started)
        {
            state.Enabled = true;
            state.Pending = true;
            return GameTransition.Started;
        }
        if (state.Enabled && !IsBlocked(state.Reason))
        {
            state.NextAtUtc = Math.Max(Deadline(state), now + Grace);
            if (HardDeadline(state) <= now) state.Reason = "settling";
        }
        return GameTransition.Ended;
    }

    public static bool CanLive(SyncState state, string gameIdentity) =>
        CanLive(state, gameIdentity, DateTimeOffset.UtcNow.ToUnixTimeSeconds());

    public static bool CanLive(SyncState state, string gameIdentity, long now) =>
        state.Enabled && gameIdentity.Length != 0 && state.SuppressedGameIdentity != gameIdentity
        && now >= state.UploadHardUntilUtc;

    public static bool CanBackfill(SyncState state, string gameIdentity, long now) =>
        state.Enabled && state.Pending && !state.Corrupt && !IsBlocked(state.Reason)
        && gameIdentity.Length == 0 && now >= Deadline(state);

    public static void Reserve(RecoveryState state, long now)
    {
        state.NextAtUtc = Math.Max(HardDeadline(state), now + 300);
    }

    private static void PreserveThrottle(RecoveryState state, RunEvidence evidence, long now,
        Func<int, int, int>? random)
    {
        if (evidence.LimitedAtUtc > 0 || evidence.Cause == "rate_limit" && !evidence.EndpointRateLimited)
        {
            long at = evidence.LimitedAtUtc > 0 ? Math.Min(now, evidence.LimitedAtUtc) : now;
            state.HardUntilUtc = Math.Max(state.HardUntilUtc, at + Day + Jitter(random, 300));
        }
        if (evidence.EndpointRateLimited)
        {
            long at = evidence.EndpointLimitedAtUtc > 0 ? Math.Min(now, evidence.EndpointLimitedAtUtc) : now;
            state.UploadHardUntilUtc = Math.Max(state.UploadHardUntilUtc, at + Day + Jitter(random, 300));
        }
    }

    /// <summary>Persist after each log batch, so a crash cannot erase an observed throttle or storage failure.</summary>
    public static void ObserveEvidence(RecoveryState state, RunEvidence evidence, long now,
        Func<int, int, int>? random = null)
    {
        state.Progress = evidence.Progress.Copy();
        state.Skipped = evidence.Skipped;
        if (evidence.RateLimited)
        {
            PreserveThrottle(state, evidence, now, random);
            state.NextAtUtc = Deadline(state);
            state.Pending = true;
            state.Reason = evidence.EndpointRateLimited ? "upload_rate_limit" : "rate_limit";
        }
        if (IsBlocked(evidence.Cause))
        {
            state.Reason = evidence.Cause;
            state.Pending = true;
        }
        if (state.Corrupt) { state.Reason = "state_store"; state.Pending = true; }
    }

    public static void Retry(RecoveryState state, long now, string reason = "transient",
        Func<int, int, int>? random = null)
    {
        state.Pending = true;
        state.Failures = Math.Min(20, state.Failures + 1);
        int basis = reason == "account_in_use" ? 900 : 300;
        long delay = Math.Min(3600, basis * (1L << Math.Min(state.Failures - 1, 4)));
        state.NextAtUtc = Math.Max(HardDeadline(state), now + delay + Jitter(random, 60));
        state.Reason = reason;
        if (state.Corrupt) { state.Reason = "state_store"; state.NextAtUtc = 0; }
    }

    public static bool Finish(RecoveryState state, string runId, int exitCode, RunEvidence evidence,
        long now, bool deliberate = false, Func<int, int, int>? random = null)
    {
        if (string.IsNullOrWhiteSpace(runId) || runId.Length > 128 || runId == state.ProcessedRunId)
            return false;
        state.ProcessedRunId = runId;
        state.Progress = evidence.Progress.Copy();
        state.Skipped = evidence.Skipped;
        string cause = evidence.Cause;
        if (evidence.RateLimited) PreserveThrottle(state, evidence, now, random);
        if (cause == "rate_limit")
        {
            state.Pending = true; state.Reason = evidence.EndpointRateLimited ? "upload_rate_limit" : cause;
            state.NextAtUtc = Deadline(state); state.Failures = 0;
        }
        else if (IsBlocked(cause))
        {
            state.Pending = true; state.Reason = cause; state.NextAtUtc = 0;
        }
        else if (deliberate)
        {
            state.Pending = true;
            state.NextAtUtc = Math.Max(Deadline(state), now + Grace);
            if (!IsBlocked(state.Reason) && HardDeadline(state) <= now) state.Reason = "settling";
        }
        else if (cause == "account_in_use") Retry(state, now, cause, random);
        else if (exitCode == 0 && cause != "transient")
        {
            state.Pending = false; state.Reason = ""; state.NextAtUtc = 0; state.Failures = 0;
        }
        else Retry(state, now, "transient", random);
        if (state.Corrupt)
        {
            state.Pending = true; state.Reason = "state_store"; state.NextAtUtc = 0;
        }
        return true;
    }

    public static string Phase(SyncState state, string gameIdentity, string workerMode = "")
    {
        if (!state.Enabled) return "off";
        if (gameIdentity.Length != 0 || workerMode == "live") return "game";
        if (workerMode == "backfill") return "working";
        if (state.Corrupt || IsBlocked(state.Reason)) return "attention";
        if (!state.Pending) return "caught_up";
        // Phase without a clock preserves the cause; prefer the overload for display.
        return state.Reason is "rate_limit" or "upload_rate_limit" ? "cooldown" : "retry";
    }

    public static string Phase(SyncState state, string gameIdentity, long now, string workerMode = "")
    {
        string phase = Phase(state, gameIdentity, workerMode);
        if (phase is "retry" or "cooldown")
            return HardDeadline(state) > now ? "cooldown" : "retry";
        return phase;
    }
}
