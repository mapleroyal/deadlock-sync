using System.IO.Pipes;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;

namespace DeadlockSync;

internal sealed class SingleInstance : IDisposable
{
    private readonly Mutex mutex;
    private readonly string pipeName;
    private readonly CancellationTokenSource cancellation = new();
    public bool OwnsMutex { get; private set; }
    public SingleInstance(string testRoot)
    {
        pipeName = Name(testRoot);
        mutex = new Mutex(true, "Local\\" + pipeName, out bool first);
        OwnsMutex = first;
    }

    private static string Name(string testRoot)
    {
        string user = WindowsIdentity.GetCurrent().User?.Value ?? Environment.UserName;
        string identity = user + ":" + System.Diagnostics.Process.GetCurrentProcess().SessionId + ":" + testRoot;
        string suffix = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(identity)))[..24];
        return "DeadlockSync-" + suffix;
    }

    public bool WaitForOwnership(int timeoutMilliseconds)
    {
        if (OwnsMutex) return true;
        try { OwnsMutex = mutex.WaitOne(timeoutMilliseconds); }
        catch (AbandonedMutexException) { OwnsMutex = true; }
        return OwnsMutex;
    }

    public Task<string?> SendAsync(string command) => SendPipeAsync(pipeName, command);
    internal static Task<string?> SendToAsync(string testRoot, string command) => SendPipeAsync(Name(testRoot), command);

    private static async Task<string?> SendPipeAsync(string pipeName, string command)
    {
        try
        {
            using var client = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
            using var timeout = new CancellationTokenSource(5000);
            await client.ConnectAsync(timeout.Token);
            using var reader = new StreamReader(client, leaveOpen: true);
            using var writer = new StreamWriter(client, leaveOpen: true) { AutoFlush = true };
            await writer.WriteLineAsync(command);
            return await reader.ReadLineAsync(timeout.Token);
        }
        catch (Exception e) when (e is IOException or OperationCanceledException or TimeoutException) { return null; }
    }

    public void Listen(Func<string, Task<string>> handle)
    {
        _ = Task.Run(async () =>
        {
            while (!cancellation.IsCancellationRequested)
            {
                try
                {
                    using var server = new NamedPipeServerStream(pipeName, PipeDirection.InOut, 1,
                        PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                    await server.WaitForConnectionAsync(cancellation.Token);
                    using var reader = new StreamReader(server, leaveOpen: true);
                    using var writer = new StreamWriter(server, leaveOpen: true) { AutoFlush = true };
                    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellation.Token);
                    timeout.CancelAfter(5000);
                    string? line = await reader.ReadLineAsync(timeout.Token);
                    if (line is not null && line.Length < 100) await writer.WriteLineAsync(await handle(line));
                }
                catch (Exception e) when (e is IOException or OperationCanceledException or ObjectDisposedException) { }
            }
        });
    }

    public void Dispose()
    {
        cancellation.Cancel();
        if (OwnsMutex) mutex.ReleaseMutex();
        mutex.Dispose(); cancellation.Dispose();
    }
}
