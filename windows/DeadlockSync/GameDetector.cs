using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace DeadlockSync;

internal static class GameDetector
{
    public static string Find()
    {
        string? user = WindowsIdentity.GetCurrent().User?.Value;
        int session = Process.GetCurrentProcess().SessionId;
        var identities = new List<string>();
        foreach (string name in new[] { "deadlock", "project8" })
            foreach (var process in Process.GetProcessesByName(name))
                using (process)
                {
                    try
                    {
                        if (process.SessionId != session) continue;
                        using var handle = OpenProcess(0x1000, false, process.Id); // QUERY_LIMITED_INFORMATION
                        if (handle.IsInvalid || !OpenProcessToken(handle, 8, out var token))
                        {
                            identities.Add($"unreadable:{process.Id}");
                            continue; // A visible game candidate must still prevent history recovery.
                        }
                        using (token)
                        using (var identity = new WindowsIdentity(token.DangerousGetHandle()))
                            if (identity.User?.Value != user) continue;
                        var image = new StringBuilder(32768); uint length = (uint)image.Capacity;
                        if (!QueryFullProcessImageNameW(handle, 0, image, ref length))
                        {
                            identities.Add($"unreadable:{process.Id}"); continue;
                        }
                        string filename = image.ToString().Replace('/', '\\');
                        if (!filename.EndsWith(@"\game\bin\win64\deadlock.exe", StringComparison.OrdinalIgnoreCase) &&
                            !filename.EndsWith(@"\game\bin\win64\project8.exe", StringComparison.OrdinalIgnoreCase))
                        {
                            identities.Add($"unverified:{process.Id}"); continue;
                        }
                        identities.Add($"{process.Id}:{process.StartTime.ToUniversalTime().Ticks}");
                    }
                    catch (System.ComponentModel.Win32Exception) { identities.Add($"unreadable:{process.Id}"); }
                    catch (InvalidOperationException) { }
                }
        return string.Join(",", identities.Order(StringComparer.Ordinal));
    }

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool OpenProcessToken(SafeFileHandle process, uint access, out SafeAccessTokenHandle token);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern SafeFileHandle OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool QueryFullProcessImageNameW(SafeFileHandle process, uint flags, StringBuilder path, ref uint size);
}
