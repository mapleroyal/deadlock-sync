# Deadlock Sync for Windows

A small tray app that supervises Deadlock API Ingest. Live memory syncing runs
while Deadlock is open; history recovery runs between games and waits when Steam
throttles it. The tray shows progress and the next planned attempt.

## Install

1. Download [Deadlock-Sync-0.3.0-Setup.exe](https://github.com/mapleroyal/deadlock-sync/releases/download/v0.3.0/Deadlock-Sync-0.3.0-Setup.exe).
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
form and does not collect or store your password. If the current account needs a new saved session,
complete any sign-in Steam asks for, then choose **Try again** in the status
window. If another remembered account needs access,
choose it in Steam when you want to recover its history. Live memory syncing can
still work without that saved session.

Recovery prioritizes the current Steam account, then works through other usable
remembered accounts one at a time. Switching accounts updates that priority
automatically. Review saved accounts in Steam on a shared computer. Keep your Steam launch
options unchanged. Do not also run the upstream GUI's always-running watcher or
run recovery on two computers for the same Steam account at once.

## Use

Click the gamepad tray icon to see one row per Steam account. With multiple
accounts, **Current** marks the one selected in Steam. Backfill progress appears
on the working row. Expand an account to see its last backfill and confirmed
uploads. Right-click the tray icon for its menu. If the icon is in the hidden-icons area, drag it beside the clock to keep
it visible.

- **Enable syncing** resumes when policy permits; it never clears a cooldown.
- **Quit syncing** stops workers and hides the icon. A small watcher remains so
  the next distinct game launch can enable syncing again.
- **Exit app** closes that watcher too, until manual launch or the next login
  if starting with Windows is enabled.

Game exit stops the live reader. Enabled history recovery stays on, with at least
a 60-second settling period before starting. A Steam cooldown pauses only that
account; another eligible account can continue. The scheduled retry is normally
24 hours plus a small jitter after throttling; it is not a guaranteed Steam reset
time. An upload-service-wide limit pauses uploads for all accounts. The panel
shows account progress and waiting/attention status. Switching workers retains
at least 20 seconds of request spacing.

Each account's history starts with its newest page and works backward. An upgrade
from an older version preserves any cooldown whose account cannot be established
as a temporary shared wait; it never guesses or resets that deadline.

Processed matches and accepted uploads are separate counters. Old unavailable
matches and remote indexing delays mean complete Statlocker coverage cannot be
guaranteed. Missing Steam sessions, private history and storage failures appear
as attention states rather than repeated automatic retries.

## Update

1. Open the [latest release](https://github.com/mapleroyal/deadlock-sync/releases/latest)
   and download the Windows file ending in **-Setup.exe**. Starting in version
   0.3.0, you can reach that page by right-clicking the tray icon and choosing
   **Download updates…**.
2. Double-click the installer and choose **Next → Install → Finish**. Keep
   **Start Deadlock Sync** checked to reopen the tray app.

Install over your existing copy, using the same Windows user. Setup closes the
old app and keeps your progress, cooldowns, match-attempt records and startup
preference. If you had paused syncing, the updated status window lets you resume. Updates are installed manually.

## Remove

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

Builds run locally; this repository has no CI/CD build pipeline. GitHub hosts
the source and finished downloads. Use a headless Windows environment for native
automation; do not wake a physical monitor or start a GPU passthrough gaming VM
for unattended app checks.

From the repository root:

```text
dotnet run --project windows/DeadlockSync.Tests -c Release
dotnet publish windows/DeadlockSync -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:PublishTrimmed=false -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=None -p:DebugSymbols=false -o publish/windows-x64
python tools/build-setup.py --windows-publish publish/windows-x64 --dotnet-notices path/to/dotnet-notices --output dist
python tools/package.py --version 0.3.0 --windows-installer dist/Deadlock-Sync-0.3.0-Setup.exe --output dist
```

`--dotnet-notices` names a directory containing the matching Microsoft .NET
`LICENSE.txt` and `ThirdPartyNotices.txt`. All three build-setup arguments shown
above are required. The builder downloads the pinned ingest extension executable
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
$accounts = Join-Path $env:TEMP 'deadlock-sync-account-smoke.json'
Start-Process $app -ArgumentList "--account-smoke-test `"$accounts`"" -Wait
Get-Content -LiteralPath $accounts
```

The account smoke test uses isolated fake accounts/workers and takes about one
minute. It checks current-account priority, safe switch preemption, separate Steam
limits, global upload limits and worker cleanup, without real Steam calls.

The smoke checks are separate from installer, gameplay and Statlocker acceptance.
See [VALIDATION.md](https://github.com/mapleroyal/deadlock-sync/blob/main/VALIDATION.md)
for what has actually been verified.
