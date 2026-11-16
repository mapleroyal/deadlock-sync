using System.Collections.Concurrent;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace DeadlockSync;

internal sealed class NativeWorker : IDisposable
{
    private const uint StillActive = 259;
    private readonly SafeFileHandle job;
    private readonly SafeFileHandle process;
    private readonly Task stdout;
    private readonly Task stderr;
    private readonly ConcurrentQueue<(string Line, long At)> lines = new();
    private int queued;
    private bool disposed;
    public int Pid { get; }
    public string RunId { get; } = Guid.NewGuid().ToString("N");
    public string Mode { get; }
    public long StartedAt { get; } = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
    public bool OutputTruncated { get; private set; }
    public bool DrainTimedOut { get; private set; }
    public bool HasExited => !GetExitCodeProcess(process, out uint code) || code != StillActive;
    public int ExitCode => GetExitCodeProcess(process, out uint code) ? unchecked((int)code) : -1;

    public NativeWorker(string executable, IReadOnlyList<string> args, string mode)
    {
        Mode = mode;
        job = CreateJobObjectW(IntPtr.Zero, null);
        if (job.IsInvalid) throw new Win32Exception();
        var limits = new JobExtendedLimits();
        limits.BasicLimitInformation.LimitFlags = 0x2000; // KILL_ON_JOB_CLOSE
        int size = Marshal.SizeOf<JobExtendedLimits>();
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, buffer, false);
            if (!SetInformationJobObject(job, 9, buffer, (uint)size)) throw new Win32Exception();
        }
        catch { job.Dispose(); throw; }
        finally { Marshal.FreeHGlobal(buffer); }

        var sa = new SecurityAttributes { Length = Marshal.SizeOf<SecurityAttributes>(), InheritHandle = true };
        SafeFileHandle? outputRead = null, outputWrite = null, errorRead = null, errorWrite = null, input = null;
        ProcessInformation pi = default;
        try
        {
            if (!CreatePipe(out outputRead, out outputWrite, ref sa, 0) ||
                !CreatePipe(out errorRead, out errorWrite, ref sa, 0)) throw new Win32Exception();
            if (!SetHandleInformation(outputRead, 1, 0) || !SetHandleInformation(errorRead, 1, 0)) throw new Win32Exception();
            input = CreateFileW("NUL", 0x80000000, 3, ref sa, 3, 0, IntPtr.Zero);
            if (input.IsInvalid) throw new Win32Exception();
            var startup = new StartupInfo
            {
                Size = Marshal.SizeOf<StartupInfo>(), Flags = 0x100,
                StdInput = input.DangerousGetHandle(), StdOutput = outputWrite.DangerousGetHandle(),
                StdError = errorWrite.DangerousGetHandle()
            };
            var command = new StringBuilder(Quote(executable) + " " + string.Join(" ", args.Select(Quote)));
            if (!CreateProcessW(executable, command, IntPtr.Zero, IntPtr.Zero, true,
                    0x08000000 | 0x00000004, IntPtr.Zero, Path.GetDirectoryName(executable), ref startup, out pi))
                throw new Win32Exception(); // CREATE_NO_WINDOW | CREATE_SUSPENDED
            process = new SafeFileHandle(pi.Process, true);
            using var thread = new SafeFileHandle(pi.Thread, true);
            if (!AssignProcessToJobObject(job, process))
            {
                TerminateProcess(process, 1);
                process.Dispose();
                throw new Win32Exception();
            }
            Pid = (int)pi.ProcessId;
            SetPriorityClass(process, 0x4000); // BELOW_NORMAL_PRIORITY_CLASS
            outputWrite.Dispose(); outputWrite = null;
            errorWrite.Dispose(); errorWrite = null;
            var output = outputRead; outputRead = null;
            var error = errorRead; errorRead = null;
            stdout = Task.Run(() => ReadPipe(output));
            stderr = Task.Run(() => ReadPipe(error));
            if (ResumeThread(thread) == uint.MaxValue)
            {
                TerminateJobObject(job, 1);
                process.Dispose();
                throw new Win32Exception();
            }
        }
        catch { job.Dispose(); throw; }
        finally
        {
            outputRead?.Dispose(); outputWrite?.Dispose(); errorRead?.Dispose(); errorWrite?.Dispose(); input?.Dispose();
        }
    }

    private void ReadPipe(SafeFileHandle handle)
    {
        try
        {
            using var stream = new FileStream(handle, FileAccess.Read, 4096, false);
            using var reader = new StreamReader(stream, new UTF8Encoding(false, false));
            var chunk = new char[2048];
            var line = new StringBuilder();
            bool oversized = false;
            int count;
            while ((count = reader.Read(chunk, 0, chunk.Length)) > 0)
                for (int i = 0; i < count; i++)
                {
                    char c = chunk[i];
                    if (c == '\n')
                    {
                        if (!oversized) Enqueue(line.ToString().TrimEnd('\r'));
                        line.Clear(); oversized = false;
                    }
                    else if (line.Length < 4096 && !oversized) line.Append(c);
                    else { oversized = true; OutputTruncated = true; }
                }
            if (line.Length > 0 && !oversized) Enqueue(line.ToString());
        }
        catch (IOException) { OutputTruncated = true; }
        catch (ObjectDisposedException) { OutputTruncated = true; }
    }

    private void Enqueue(string line)
    {
        if (Interlocked.Increment(ref queued) <= 3000)
            lines.Enqueue((line, DateTimeOffset.UtcNow.ToUnixTimeSeconds()));
        else { Interlocked.Decrement(ref queued); OutputTruncated = true; }
    }

    public IEnumerable<(string Line, long At)> Drain()
    {
        while (lines.TryDequeue(out var line)) { Interlocked.Decrement(ref queued); yield return line; }
    }

    public void Stop()
    {
        try { if (!disposed && !HasExited) TerminateJobObject(job, 1); }
        catch (ObjectDisposedException) { } // Session teardown can race disposal.
    }

    public async Task DrainAfterExitAsync()
    {
        var complete = Task.WhenAll(stdout, stderr);
        if (await Task.WhenAny(complete, Task.Delay(3000)) != complete)
        {
            DrainTimedOut = true;
            OutputTruncated = true;
        }
    }

    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        job.Dispose(); // Also terminates descendants if controller exits unexpectedly.
        process.Dispose();
    }

    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') result.Append('\\', slashes * 2 + 1).Append(c);
            else result.Append('\\', slashes).Append(c);
            slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }

    [StructLayout(LayoutKind.Sequential)] private struct SecurityAttributes { public int Length; public IntPtr Descriptor; [MarshalAs(UnmanagedType.Bool)] public bool InheritHandle; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] private struct StartupInfo
    {
        public int Size; public string? Reserved; public string? Desktop; public string? Title;
        public uint X, Y, XSize, YSize, XCountChars, YCountChars, FillAttribute, Flags;
        public ushort ShowWindow, Reserved2; public IntPtr ReservedPointer, StdInput, StdOutput, StdError;
    }
    [StructLayout(LayoutKind.Sequential)] private struct ProcessInformation { public IntPtr Process, Thread; public uint ProcessId, ThreadId; }
    [StructLayout(LayoutKind.Sequential)] private struct JobBasicLimits
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags; public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount, ReadTransferCount, WriteTransferCount, OtherTransferCount; }
    [StructLayout(LayoutKind.Sequential)] private struct JobExtendedLimits
    {
        public JobBasicLimits BasicLimitInformation; public IoCounters IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] private static extern SafeFileHandle CreateJobObjectW(IntPtr attributes, string? name);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetInformationJobObject(SafeFileHandle job, int infoClass, IntPtr info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool AssignProcessToJobObject(SafeFileHandle job, SafeFileHandle process);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateJobObject(SafeFileHandle job, uint code);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateProcess(SafeFileHandle process, uint code);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(SafeFileHandle process, out uint code);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetPriorityClass(SafeFileHandle process, uint priority);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern uint ResumeThread(SafeFileHandle thread);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool CreatePipe(out SafeFileHandle read, out SafeFileHandle write, ref SecurityAttributes attributes, uint size);
    [DllImport("kernel32.dll", SetLastError = true)] private static extern bool SetHandleInformation(SafeFileHandle handle, uint mask, uint flags);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, ref SecurityAttributes attributes, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)] private static extern bool CreateProcessW(string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment, string? directory, ref StartupInfo startup, out ProcessInformation process);
}
