# Deadlock Sync for Windows

A small tray app that supervises Deadlock API Ingest. Live memory syncing runs
while Deadlock is open; history recovery runs between games and waits when Steam
throttles it. The tray shows progress and the next planned attempt.

## Install

1. Download [Deadlock-Sync-0.1.1-Setup.exe](https://github.com/mapleroyal/deadlock-sync/releases/download/v0.1.1/Deadlock-Sync-0.1.1-Setup.exe).
2. Double-click it, then choose **Next → Install → Finish**. Leave **Start
   Deadlock Sync** checked on the last page.
3. The app opens its status window and places a gamepad icon near the Windows
   clock. It may be inside the hidden-icons arrow. Play Deadlock normally;
   syncing and history recovery are automatic.

Windows 10 or newer, 64-bit, is required. Install as your ordinary Windows user.
No administrator access, terminal commands, .NET installation or SDK is needed.
The installer includes the app, its runtime and the verified ingestion engine,
so installation works offline; syncing requires internet access. This community
build is unsigned, so Windows may display a publisher warning.

**Start Deadlock Sync when I sign in to Windows** is checked by default on a
fresh install. Updates retain your previous startup choice. You can change the
checkbox during setup. If you leave it off, open **Deadlock Sync** from the Start
menu when you want the app to run.

Sign in to Steam normally and let it remember your login; complete Steam Guard
inside Steam if asked. “Remembered login” means Steam has saved a reusable login
session on this Windows account. The bundled ingestion engine uses that session
to recover match history while Deadlock is closed. Deadlock Sync has no password
form and does not collect or store your password. If Steam has not saved a usable
session, the app shows an attention message; sign in again in Steam, then choose
**Enable syncing**. Live memory syncing can still work without that saved session.

Recovery uses every decryptable remembered Steam account, so review saved
accounts in Steam on a shared or multi-account computer. Keep your Steam launch
options unchanged. Do not also run the upstream GUI's always-running watcher or
run recovery on two computers for the same Steam account at once.

## Use

Click the gamepad tray icon to open status and progress. Right-click for its
menu. If the icon is in the hidden-icons area, drag it beside the clock to keep
it visible.

- **Enable syncing** resumes when policy permits; it never clears a cooldown.
- **Quit syncing** stops workers and hides the icon. A small watcher remains so
  the next distinct game launch can enable syncing again.
- **Exit app** closes that watcher too, until manual launch or the next login
  if starting with Windows is enabled.

Game exit stops the live reader. Enabled history recovery stays on, with at least
a 60-second settling period before starting. During a Steam cooldown, the icon
remains and the uploader stops. The scheduled retry is normally 24 hours plus a
small jitter after throttling; it is not a guaranteed Steam reset time.

Processed matches and accepted uploads are separate counters. Old unavailable
matches and remote indexing delays mean complete Statlocker coverage cannot be
guaranteed. Missing Steam sessions, private history and storage failures appear
as attention states rather than repeated automatic retries.

## Remove or update

Run the newer release's installer to update. It closes the running app safely,
preserves your recovery records and startup choice, and starts the updated tray
app when you click Finish with **Start Deadlock Sync** checked.

To remove it, open **Settings → Apps → Installed apps → Deadlock Sync →
Uninstall**. On Windows 10, the page is called **Apps & features**. Follow the
normal uninstall wizard. It removes the app, Start Menu shortcut and its startup
entry. Your private scheduling state and upstream match-attempt records are
retained so a reinstall can resume safely. Deleting those records is not a
supported retry method.

App files are installed under `%LOCALAPPDATA%\DeadlockSync\app`. Scheduling state
is under `%LOCALAPPDATA%\DeadlockSync\state`. Upstream stores its own attempts and
rolling logs under `%APPDATA%\deadlock-api-ingest`. Keep both state locations
private.

## Developer build and validation

These steps are for maintaining the app, not installing it on a gaming PC.
Read the [implementation guide](https://github.com/mapleroyal/deadlock-sync/blob/main/GUIDE.md)
and [upstream facts](https://github.com/mapleroyal/deadlock-sync/blob/main/UPSTREAM.md)
before changing behavior. Build on a development host with a .NET 10 SDK,
Python 3.11 or newer, and NSIS with its standard includes and plugins. The
Windows-targeted build also works on Linux. Only the resulting setup executable
needs to reach the gaming PC.

From the repository root:

```text
dotnet run --project windows/DeadlockSync.Tests -c Release
dotnet publish windows/DeadlockSync -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:PublishTrimmed=false -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=None -p:DebugSymbols=false -o publish/windows-x64
python tools/build-setup.py --windows-publish publish/windows-x64 --dotnet-notices path/to/dotnet-notices --output dist
python tools/package.py --version 0.1.1 --windows-installer dist/Deadlock-Sync-0.1.1-Setup.exe --output dist
```

`--dotnet-notices` names a directory containing the matching Microsoft .NET
`LICENSE.txt` and `ThirdPartyNotices.txt`. All three build-setup arguments shown
above are required. The builder downloads the pinned official ingest executable
and MIT license by default and verifies their hashes. For cached inputs, add
`--ingest path/to/deadlock-api-ingest.exe` and
`--ingest-license path/to/INGEST-LICENSE.txt`; the same verification still applies.
For portable NSIS, add `--makensis path/to/makensis` and `--nsis-dir path/to/nsis`.
The build includes the engine license, .NET notices and NSIS notices in the app
payload. Version comes from `windows/DeadlockSync/DeadlockSync.csproj`.

`tools/package.py` accepts only the three shown release inputs. It packages the
Windows setup executable and Omarchy Linux archive, then writes `SHA256SUMS.txt`.
It does not package credentials, runtime state or downloaded build tools.

For maintainer automation, setup accepts `/S` for silent installation,
`/NOAUTOSTART` to leave login startup disabled, and `/NOSTART` to leave immediate
launch disabled. Silent installation does not open the Finish page or launch the
app. These flags require no administrative privileges. Normal users should use
the wizard above.

For unattended removal, the exit code from the original `Uninstall.exe /S`
launcher does not prove completion: NSIS starts a temporary copy without
propagating its result. Verify that the app and uninstall registration were
removed. To capture the actual exit code, run your own temporary copy of the
uninstaller with `/S _?=<install root>` (the `_?=` argument last and unquoted),
wait for it, then delete that temporary copy. See the
[NSIS uninstaller options](https://nsis.sourceforge.io/Docs/Chapter3.html).

For an isolated native smoke check on Windows after installation (no Steam
access or real uploads):

```powershell
$report = Join-Path $env:TEMP 'deadlock-sync-smoke.json'
$app = Join-Path $env:LOCALAPPDATA 'DeadlockSync\app\DeadlockSync.exe'
Start-Process $app -ArgumentList "--smoke-test `"$report`"" -Wait
Get-Content -LiteralPath $report
```

The smoke checks are separate from installer, gameplay and Statlocker acceptance.
See [VALIDATION.md](https://github.com/mapleroyal/deadlock-sync/blob/main/VALIDATION.md)
for what has actually been verified.
