namespace DeadlockSync.Core;

public sealed class ProgressSnapshot
{
    public string Mode { get; set; } = "";
    public string Phase { get; set; } = "preparing";
    public int? Completed { get; set; }
    public int? Total { get; set; }
    public int? Uploaded { get; set; }
    public int? Failed { get; set; }
    public int? History { get; set; }
    public long? UpdatedAtUtc { get; set; }
    public ProgressSnapshot Copy() => (ProgressSnapshot)MemberwiseClone();
}

public class RecoveryState
{
    public bool Pending { get; set; } = true;
    // All persistent times are UTC Unix seconds; format in local time only in UI.
    public long NextAtUtc { get; set; }
    public long HardUntilUtc { get; set; }
    public long UploadHardUntilUtc { get; set; }
    public string Reason { get; set; } = "";
    public int Failures { get; set; }
    public string ProcessedRunId { get; set; } = "";
    public ProgressSnapshot Progress { get; set; } = new();
    public int Skipped { get; set; }
    public bool Corrupt { get; set; }
    public long LastServedUtc { get; set; }
}

public sealed class SyncState : RecoveryState
{
    public int Version { get; set; } = 2;
    public bool Enabled { get; set; }
    public string LastGameIdentity { get; set; } = "";
    public string SuppressedGameIdentity { get; set; } = "";
    public string LastNoticeKey { get; set; } = "";
    public string CurrentAccountId { get; set; } = "";
    public string ActiveAccountId { get; set; } = "";
    public long NextWorkerUtc { get; set; }
    public long SettlingUntilUtc { get; set; }
    // Root HardUntilUtc retains only an unattributed legacy Steam cooldown.
    public Dictionary<string, RecoveryState> Accounts { get; set; } = new();
}

public enum GameTransition { None, Started, Ended }
