using DeadlockSync.Core;
using Microsoft.Win32;
using System.Text;

namespace DeadlockSync;

internal sealed class SteamAccountReader(string testRoot)
{
    private string cachedPath = "";
    private DateTime modified;
    private long length;
    private SteamAccounts cached = SteamAccounts.Empty;

    public SteamAccounts Read()
    {
        var candidates = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        if (testRoot.Length != 0) candidates.Add(Path.Combine(testRoot, "loginusers.vdf"));
        else
        {
            // Match the engine's pinned steamlocate Windows registry precedence.
            using var machine = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);
            using var wow = machine.OpenSubKey(@"SOFTWARE\Wow6432Node\Valve\Steam");
            using var normal = machine.OpenSubKey(@"SOFTWARE\Valve\Steam");
            Add(wow?.GetValue("InstallPath") as string ?? normal?.GetValue("InstallPath") as string);
        }
        var file = candidates.Select(p => new FileInfo(p)).Where(f => f.Exists)
            .OrderByDescending(f => f.LastWriteTimeUtc).FirstOrDefault();
        if (file is null) { cachedPath = ""; cached = SteamAccounts.Empty; return cached; }
        if (file.Length > 1_048_576) throw new InvalidDataException("Steam account metadata needs review.");
        if (file.FullName != cachedPath || file.LastWriteTimeUtc != modified || file.Length != length)
        {
            SteamAccounts result = SteamAccounts.Parse(ReadBounded(file.FullName));
            cached = result; cachedPath = file.FullName; modified = file.LastWriteTimeUtc; length = file.Length;
        }
        if (testRoot.Length != 0) return cached;
        using var active = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam\ActiveProcess");
        if (active?.GetValue("ActiveUser") is int account)
        {
            string id = unchecked((uint)account).ToString(System.Globalization.CultureInfo.InvariantCulture);
            return cached with { CurrentId = cached.Accounts.Any(x => x.Id == id) ? id : "" };
        }
        return cached;

        void Add(string? directory)
        {
            if (string.IsNullOrWhiteSpace(directory)) return;
            candidates.Add(Path.GetFullPath(Path.Combine(directory, "config", "loginusers.vdf")));
        }
    }

    private static string ReadBounded(string path)
    {
        const int limit = 1_048_576;
        using var file = new FileStream(path, FileMode.Open, FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);
        byte[] bytes = new byte[limit + 1];
        int length = 0, read;
        while (length < bytes.Length && (read = file.Read(bytes, length, bytes.Length - length)) != 0) length += read;
        if (length > limit) throw new InvalidDataException("Steam account metadata needs review.");
        int offset = length >= 3 && bytes[0] == 0xef && bytes[1] == 0xbb && bytes[2] == 0xbf ? 3 : 0;
        return new UTF8Encoding(false, true).GetString(bytes, offset, length - offset);
    }
}
