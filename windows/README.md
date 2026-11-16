# Deadlock Sync for Windows

A small tray app that supervises Deadlock API Ingest. Live memory syncing runs
while Deadlock is open; history recovery runs between games and waits when Steam
throttles it. The tray shows progress and the next planned attempt.

## Install

Use the Windows x64 ZIP from the project's GitHub release. Extract it to a folder
you can write to, then run this command in PowerShell in the extracted folder:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

The execution-policy option applies to this process only. No administrator
session or SDK is needed. The installer verifies the official ingest download,
installs under `%LOCALAPPDATA%\DeadlockSync`, and adds a Start Menu shortcut and
per-user login entry. This initial community build is unsigned.

Sign in to Steam normally and let it remember your login; complete Steam Guard
inside Steam if asked. The app does not collect credentials. Upstream recovery
uses every decryptable remembered Steam account, so on a multi-account computer
review the remembered accounts in Steam first. Live syncing does not require a
decryptable remembered session.

Launch **Deadlock Sync** to enable recovery, or launch Deadlock normally. Keep
your Steam launch options unchanged. Do not also run the upstream GUI's
always-running watcher. Do not run recovery on two computers for the same Steam
account at once.

## Use

The notification-area icon may initially be in Windows' tray overflow. Pin it
there if you want it always visible. Left click opens status and progress.

- **Enable syncing** resumes when policy permits; it never clears a cooldown.
- **Quit syncing** stops workers and hides the icon. A small watcher remains so
  the next distinct game launch can enable syncing again.
- **Exit app** closes that watcher too, until manual launch or the next login.

Game exit stops the live reader. Enabled history recovery stays on, with at least
a 60-second settling period before starting. During a Steam cooldown, the icon
remains and the uploader stops. The scheduled retry is normally 24 hours plus a
small jitter after throttling; it is not a guaranteed Steam reset time.

Processed matches and accepted uploads are separate counters. Old unavailable
matches and remote indexing delays mean complete Statlocker coverage cannot be
guaranteed. Missing Steam sessions, private history and storage failures appear
as attention states rather than repeated automatic retries.

## Remove or update

Run the new release's installer to update. To remove the app, run
`uninstall.ps1` from the extracted package in the same manner as installation.
The uninstaller retains recovery state and upstream attempt history. Updates
also preserve them; deleting those files is not a supported retry method.

The app stores its scheduling state under `%LOCALAPPDATA%\DeadlockSync\state`.
Upstream stores its own attempts and rolling logs under
`%APPDATA%\deadlock-api-ingest`. Keep both private.

## Rebuild or adapt

The source repository's [implementation guide](https://github.com/mapleroyal/deadlock-sync/blob/main/GUIDE.md)
gives the contract; [upstream facts](https://github.com/mapleroyal/deadlock-sync/blob/main/UPSTREAM.md)
pins the behavior and log markers. With a .NET 10 SDK:

```powershell
dotnet run --project windows/DeadlockSync.Tests -c Release
dotnet publish windows/DeadlockSync -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:PublishTrimmed=false -p:IncludeNativeLibrariesForSelfExtract=true -o publish/windows-x64
```

The build can also run on a Linux host with Windows targeting enabled. Build
tools stay off the gaming PC; copy only the release package there.

For an isolated Windows native smoke check from an extracted release (no Steam
access or real uploads):

```powershell
$report = Join-Path $env:TEMP 'deadlock-sync-smoke.json'
Start-Process .\app\DeadlockSync.exe -ArgumentList "--smoke-test `"$report`"" -Wait
Get-Content -LiteralPath $report
```
