using System.Text.RegularExpressions;

namespace DeadlockSync.Core;

/// <summary>Only fixed strings and counts survive parsing. No account IDs or salt values are retained.</summary>
public sealed class RunEvidence
{
    public const int CountLimit = 10_000_000;
    private static readonly Regex Ansi = new(@"\x1b\[[0-?]*[ -/]*[@-~]", RegexOptions.Compiled,
        TimeSpan.FromMilliseconds(100));
    private static readonly Regex History = new(@"gc: account \d+ has (\d+) match\(es\) in history, (\d+) missing salts",
        RegexOptions.Compiled, TimeSpan.FromMilliseconds(100));
    private static readonly Regex Ingested = new(@"gc: ingested match \d+ \((\d+)/(\d+)\)",
        RegexOptions.Compiled, TimeSpan.FromMilliseconds(100));
    private static readonly Regex Abandoned = new(@"gc: account \d+ skips (\d+) match\(es\) given up on after \d+ attempts",
        RegexOptions.Compiled, TimeSpan.FromMilliseconds(100));
    private static readonly Regex HttpThrottle = new(
        @"\b(?:HTTP(?:/\d(?:\.\d)?)?(?:\s+(?:status|error|response))?|status(?:\s+code)?)\s*[:=]?\s*429\b|\b429\s+Too Many Requests\b",
        RegexOptions.Compiled | RegexOptions.IgnoreCase, TimeSpan.FromMilliseconds(100));
    private static readonly Regex HttpAccessDenied = new(
        @"\bHTTP(?:/\d(?:\.\d)?)?(?:\s+(?:status|error|response))?\s*[:=]?\s*(?:401|403)\b|\b401\s+Unauthorized\b|\b403\s+Forbidden\b",
        RegexOptions.Compiled | RegexOptions.IgnoreCase, TimeSpan.FromMilliseconds(100));
    private bool baseline;
    private bool hasBatch;
    private bool truncated;
    private int batchTotal;
    private int completedBeforeBatch;
    private int totalBeforeBatch;
    public string Cause { get; private set; } = "";
    public long LimitedAtUtc { get; private set; }
    public long EndpointLimitedAtUtc { get; private set; }
    public bool EndpointRateLimited { get; private set; }
    public bool EndpointAccessDenied { get; private set; }
    public int Skipped { get; private set; }
    public ProgressSnapshot Progress { get; }
    public bool Complete { get; private set; }
    public bool StorageFailure => Cause == "state_store";
    public bool RateLimited => LimitedAtUtc > 0 || EndpointRateLimited || Cause == "rate_limit";

    public void MarkTruncated()
    {
        truncated = true;
        baseline = false;
        Progress.Uploaded = null;
        Progress.Failed = null;
        Progress.History = null;
    }

    public void MarkIncomplete()
    {
        MarkTruncated();
        if (Priority(Cause) < Priority("transient")) Cause = "transient";
        Complete = false;
        Progress.Phase = "failed";
    }

    public RunEvidence(string mode = "backfill") =>
        Progress = new() { Mode = mode == "live" ? "live" : "backfill",
            Phase = mode == "live" ? "watching" : "preparing" };

    private static int Priority(string cause) => cause switch
    {
        "state_store" => 6, "rate_limit" => 5, "unavailable" => 4, "auth" => 3,
        "account_in_use" => 2, "transient" => 1, _ => 0
    };
    private static bool Number(Group group, out int number) =>
        int.TryParse(group.Value, out number) && number >= 0 && number <= CountLimit;

    public void AddLine(string? line, long observedAtUtc)
    {
        if (line is null || line.Length > 4096) return;
        try { AddSanitized(Ansi.Replace(line, ""), observedAtUtc); }
        catch (RegexMatchTimeoutException) { /* Untrusted log input cannot stop lifecycle control. */ }
    }

    private void AddSanitized(string line, long now)
    {
        bool recognized = false;
        string cause = "";
        string lower = line.ToLowerInvariant();
        bool endpointLimit = HttpThrottle.IsMatch(line);
        bool endpointDenied = HttpAccessDenied.IsMatch(line);
        if (endpointDenied) EndpointAccessDenied = true;
        bool steamLimit = line.Contains("was rate-limited by Steam") || line.Contains("Steam rate limit reached.")
            || line.Contains("Steam GC rate-limited the request") || line.Contains("gc: match history rate-limited");
        if (endpointLimit)
        {
            EndpointRateLimited = true;
            EndpointLimitedAtUtc = Math.Max(EndpointLimitedAtUtc, now);
        }
        if (steamLimit) LimitedAtUtc = Math.Max(LimitedAtUtc, now);
        if (line.Contains("gc: store unavailable, not tracking salt attempts:") || line.Contains("GC store error"))
            cause = "state_store";
        else if (steamLimit || endpointLimit)
            cause = "rate_limit";
        else if (endpointDenied)
            cause = "unavailable";
        else if (line.Contains("gc: no usable Steam account") || line.Contains("Steam session unavailable:")
            || line.Contains("Steam logon failed") || new[] { "invalidpassword", "invalid password",
                "accessdenied", "invalid refresh token", "expired refresh token" }.Any(lower.Contains))
            cause = "auth";
        else if (lower.Contains("history is private") || lower.Contains("invalid permission"))
            cause = "unavailable";
        else if (line.Contains("the Steam account is playing a game") || line.Contains("Deadlock was launched")
            || line.Contains("Deadlock is running, close it"))
            cause = "account_in_use";
        else if (line.Contains("could not be processed. Run this again to retry.")
            || (line.Contains("gc: account ") && line.Contains(" failed:")))
            cause = "transient";
        if (cause != "")
        {
            if (Priority(cause) >= Priority(Cause)) Cause = cause;
            recognized = true;
        }
        Match skipped = Abandoned.Match(line);
        if (skipped.Success && Number(skipped.Groups[1], out int skip))
        {
            Skipped = Math.Min(CountLimit, Skipped + skip); recognized = true;
        }
        if (Progress.Mode == "backfill")
        {
            Match history = History.Match(line), ingested = Ingested.Match(line);
            if (history.Success && Number(history.Groups[1], out int count)
                && Number(history.Groups[2], out int total) && total <= count)
            {
                // Own-history mode may visit several remembered accounts. Keep completed
                // batches in the displayed pass, without retaining account identifiers.
                completedBeforeBatch = Progress.Completed ?? 0;
                totalBeforeBatch = Progress.Total ?? 0;
                batchTotal = total; hasBatch = true;
                Progress.Phase = "recovering"; Progress.Completed = completedBeforeBatch;
                Progress.Total = Math.Min(CountLimit, totalBeforeBatch + total);
                if (!truncated)
                {
                    Progress.Uploaded ??= 0; Progress.Failed ??= 0;
                    Progress.History = Math.Min(CountLimit, (Progress.History ?? 0) + count);
                }
                baseline = !truncated; recognized = true;
            }
            else if (ingested.Success && Number(ingested.Groups[1], out int done)
                && Number(ingested.Groups[2], out int all) && done > 0 && done <= all)
            {
                if (!hasBatch || batchTotal != all)
                {
                    if (hasBatch)
                    {
                        completedBeforeBatch = Progress.Completed ?? 0;
                        totalBeforeBatch = Progress.Total ?? 0;
                    }
                    batchTotal = all; hasBatch = true;
                    MarkTruncated();
                }
                Progress.Phase = "recovering";
                Progress.Completed = Math.Min(CountLimit, completedBeforeBatch + done);
                Progress.Total = Math.Min(CountLimit, totalBeforeBatch + all);
                if (baseline) Progress.Uploaded = Math.Min(CountLimit, (Progress.Uploaded ?? 0) + 1);
                recognized = true;
            }
            else if (line.Contains("gc: salt fetch failed for ") || line.Contains("gc: salt POST failed for ")
                || line.Contains("gc: salt POST task panicked:"))
            {
                if (baseline) Progress.Failed = Math.Min(CountLimit, (Progress.Failed ?? 0) + 1);
                if (Progress.Completed.HasValue && Progress.Total.HasValue)
                    Progress.Completed = Math.Min(Progress.Total.Value, Progress.Completed.Value + 1);
                recognized = true;
            }
            if (cause == "rate_limit") Progress.Phase = "limited";
            else if (cause is "auth" or "unavailable" or "state_store" or "transient") Progress.Phase = "failed";
            if (line.Contains("Done, all own matches processed."))
            {
                Complete = true; Progress.Phase = "complete"; recognized = true;
            }
        }
        else
        {
            if (line.Contains("memory: reading the game (pid "))
            { Progress.Phase = "reading"; recognized = true; }
            else if (line.Contains("memory: read the game in ") || line.Contains("memory: ingested salts:"))
            { Progress.Phase = "waiting"; recognized = true; }
            else if (line.Contains("memory: failed") || line.Contains("memory: cannot")
                || line.Contains("memory: not allowed to read Deadlock's memory:"))
            { Progress.Phase = "failed"; recognized = true; }
        }
        if (recognized && now > 0 && now <= 4_102_444_800) Progress.UpdatedAtUtc = now;
    }
}
