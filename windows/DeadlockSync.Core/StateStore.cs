using System.Text.Json;

namespace DeadlockSync.Core;

/// <summary>Atomic per-user state; invalid input disables recovery without erasing evidence.</summary>
public sealed class StateStore(string path)
{
    public string Path { get; } = System.IO.Path.GetFullPath(path);
    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };
    private const long MaxTime = 4_102_444_800;
    public const int MaxBytes = 262_144;
    private bool migratedLegacy;
    private static readonly HashSet<string> Reasons = ["", "rate_limit", "upload_rate_limit", "transient", "account_in_use",
        "settling", "auth", "state_store", "unavailable"];

    public SyncState Load()
    {
        try
        {
            if (new FileInfo(Path).Length > MaxBytes) return Invalid();
            string json = File.ReadAllText(Path);
            using JsonDocument doc = JsonDocument.Parse(json, new JsonDocumentOptions { MaxDepth = 8 });
            JsonElement root = doc.RootElement;
            if (!Valid(root)) return Invalid(root);
            SyncState state = JsonSerializer.Deserialize<SyncState>(json, Options)!;
            if (state.Version == 1)
            {
                migratedLegacy = true;
                state.Version = 2;
                state.Accounts = new(); state.CurrentAccountId = ""; state.ActiveAccountId = ""; state.SettlingUntilUtc = 0;
                state.NextWorkerUtc = state.NextAtUtc;
                // Do not guess that a legacy throttle belongs to the account selected now.
                // The root hard deadline stays a temporary shared hold until it expires.
                if (state.Reason is not ("state_store" or "unavailable")) state.Reason = "";
            }
            if (state.Corrupt) { state.Pending = true; state.Reason = "state_store"; }
            foreach (var account in state.Accounts.Values)
            {
                if (account.Corrupt) { account.Pending = true; account.Reason = "state_store"; }
                state.UploadHardUntilUtc = Math.Max(state.UploadHardUntilUtc, account.UploadHardUntilUtc);
            }
            return state;
        }
        catch (FileNotFoundException) { return new(); }
        catch (DirectoryNotFoundException) { return new(); }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException
            or ArgumentException or NotSupportedException)
        { return Invalid(); }
    }

    private static SyncState Invalid() => new() { Enabled = true, Pending = true,
        Corrupt = true, Reason = "state_store" };
    private static SyncState Invalid(JsonElement root)
    {
        SyncState state = Invalid();
        // Retain any valid throttle evidence even when unrelated fields are damaged.
        if (root.ValueKind == JsonValueKind.Object)
        {
            if (Integer(root, "HardUntilUtc", MaxTime)) state.HardUntilUtc = root.GetProperty("HardUntilUtc").GetInt64();
            if (Integer(root, "UploadHardUntilUtc", MaxTime)) state.UploadHardUntilUtc = root.GetProperty("UploadHardUntilUtc").GetInt64();
            if (Integer(root, "NextAtUtc", MaxTime)) state.NextAtUtc = root.GetProperty("NextAtUtc").GetInt64();
            if (root.TryGetProperty("Accounts", out var accounts) && accounts.ValueKind == JsonValueKind.Object)
                foreach (var item in accounts.EnumerateObject().Take(AccountPolicy.AccountLimit))
                    if (AccountPolicy.ValidId(item.Name) && item.Value.ValueKind == JsonValueKind.Object
                        && Integer(item.Value, "HardUntilUtc", MaxTime))
                        state.Accounts[item.Name] = new() { HardUntilUtc = item.Value.GetProperty("HardUntilUtc").GetInt64(), Corrupt = true };
        }
        return state;
    }

    private static bool Integer(JsonElement root, string name, long max)
        => root.TryGetProperty(name, out JsonElement item) && item.ValueKind == JsonValueKind.Number
        && item.TryGetInt64(out long value)
        && value >= 0 && value <= max;
    private static bool Boolean(JsonElement root, string name)
        => root.TryGetProperty(name, out JsonElement item) && item.ValueKind is JsonValueKind.True or JsonValueKind.False;
    private static bool Text(JsonElement root, string name, int max)
        => root.TryGetProperty(name, out JsonElement item) && item.ValueKind == JsonValueKind.String
        && item.GetString() is { } text && text.Length <= max;
    private static bool NullableInteger(JsonElement root, string name, long max)
        => root.TryGetProperty(name, out JsonElement item) && (item.ValueKind == JsonValueKind.Null
        || item.ValueKind == JsonValueKind.Number && item.TryGetInt64(out long value) && value >= 0 && value <= max);

    private static bool Valid(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object
            || root.EnumerateObject().GroupBy(x => x.Name).Any(x => x.Count() > 1)
            || !Integer(root, nameof(SyncState.Version), 2) || root.GetProperty("Version").GetInt32() is not (1 or 2)
            || !Boolean(root, "Enabled")
            || !Text(root, "LastGameIdentity", 1024) || !Text(root, "SuppressedGameIdentity", 1024)
            || !Text(root, "LastNoticeKey", 256) || !ValidRecovery(root)) return false;
        if (root.GetProperty("Version").GetInt32() == 1) return true;
        if (!Text(root, "CurrentAccountId", 10)
            || root.GetProperty("CurrentAccountId").GetString() is not ("" or null) && !AccountPolicy.ValidId(root.GetProperty("CurrentAccountId").GetString()!)
            || !Text(root, "ActiveAccountId", 10)
            || root.GetProperty("ActiveAccountId").GetString() is not ("" or null) && !AccountPolicy.ValidId(root.GetProperty("ActiveAccountId").GetString()!)
            || !Integer(root, "NextWorkerUtc", MaxTime) || !Integer(root, "SettlingUntilUtc", MaxTime)
            || !root.TryGetProperty("Accounts", out var accounts) || accounts.ValueKind != JsonValueKind.Object)
            return false;
        var items = accounts.EnumerateObject().ToArray();
        return items.Length <= AccountPolicy.AccountLimit && items.Select(x => x.Name).Distinct().Count() == items.Length
            && items.All(x => AccountPolicy.ValidId(x.Name) && ValidRecovery(x.Value) && Integer(x.Value, "LastServedUtc", MaxTime));
    }

    private static bool ValidRecovery(JsonElement root)
    {
        if (root.ValueKind != JsonValueKind.Object
            || root.EnumerateObject().GroupBy(x => x.Name).Any(x => x.Count() > 1)
            || !Boolean(root, "Pending") || !Boolean(root, "Corrupt")
            || !Integer(root, "NextAtUtc", MaxTime) || !Integer(root, "HardUntilUtc", MaxTime)
            || !Integer(root, "UploadHardUntilUtc", MaxTime)
            || !Integer(root, "Failures", 20) || !Integer(root, "Skipped", RunEvidence.CountLimit)
            || !Text(root, "Reason", 32) || !Reasons.Contains(root.GetProperty("Reason").GetString()!)
            || !Text(root, "ProcessedRunId", 128)
            || !root.TryGetProperty("Progress", out JsonElement progress) || progress.ValueKind != JsonValueKind.Object)
            return false;
        if (progress.EnumerateObject().GroupBy(x => x.Name).Any(x => x.Count() > 1)
            || !Text(progress, "Mode", 16) || !Text(progress, "Phase", 32)
            || !new[] { "", "live", "backfill" }.Contains(progress.GetProperty("Mode").GetString())
            || !new[] { "preparing", "watching", "recovering", "reading", "waiting", "limited", "failed", "complete" }
                .Contains(progress.GetProperty("Phase").GetString())) return false;
        foreach (string field in new[] { "Completed", "Total", "Uploaded", "Failed", "History" })
            if (!NullableInteger(progress, field, RunEvidence.CountLimit)) return false;
        return NullableInteger(progress, "UpdatedAtUtc", MaxTime);
    }

    public void Save(SyncState state)
    {
        string directory = System.IO.Path.GetDirectoryName(Path)!;
        Directory.CreateDirectory(directory);
        if (state.Corrupt && File.Exists(Path) && !File.Exists(Path + ".invalid"))
            File.Copy(Path, Path + ".invalid", false);
        if (migratedLegacy && File.Exists(Path) && !File.Exists(Path + ".v1"))
            File.Copy(Path, Path + ".v1", false);
        string temporary = Path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            using (FileStream stream = new(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None,
                4096, FileOptions.WriteThrough))
            {
                JsonSerializer.Serialize(stream, state, Options);
                stream.Flush(true);
            }
            File.Move(temporary, Path, true);
        }
        finally { if (File.Exists(temporary)) File.Delete(temporary); }
    }
}
