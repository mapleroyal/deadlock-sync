# Release validation

Version 0.1.1, November 16, 2026. Conventional Windows installer preview.

## Windows installer 0.1.1

The Windows download is now one offline NSIS setup executable containing the
self-contained app, verified upstream engine and third-party notices. It installs
for the current user without elevation and registers a Start Menu shortcut,
optional login startup and a normal Windows uninstall entry. Installation and
updates preserve private recovery state and upstream attempt records.

The application compiled with zero warnings and errors; all 55 Core checks passed.
The release builder rejected an altered upstream executable before producing an
installer. App preparation also verifies the bundled engine before stopping an
existing controller. Setup uses bounded IPC shutdown and a temporary launch gate
so a login or shortcut cannot start another controller during file replacement.

The wizard was exercised as a non-elevated interactive user in the Windows 11
gaming VM. Upgrading the existing 0.1.0 installation preserved its startup
choice; selecting startup created the expected entry. Finish opened one enabled
tray controller and a visible status window, respecting the real existing
cooldown with zero ingestion workers. The Start Menu shortcut had the correct
target, arguments and installed working directory, and Installed apps contained
the native uninstall entry. Cancelling before Install left the previous app and
state unchanged. All eight native smoke checks passed, including launch-command
routing, rejection of an unverified engine and the earlier process/job checks.

Active-app upgrade, duplicate-setup rejection, background-launch suppression
during setup, fresh-install startup/launch defaults and ordinary uninstall passed.
An unrelated synthetic engine made silent setup stop without changing the payload
or opening a dialog. A deliberately locked owned file prevented full uninstall;
the remaining file and Windows uninstall retry entry were retained, along with
recovery state and upstream records. Retrying after releasing the lock completed
removal. The final setup artifact's fresh wizard, default Finish launch and
Welcome/Finish text were rechecked after the visual correction. NSIS's outer silent-uninstall launcher does
not propagate its temporary child process's exit code; the maintainer instructions
explain completion verification.

The application SHA-256 is
`5213ff8121f78ba61ed00b4acf1991af37304ef43d13f515a02b329bb9998006`.
The setup executable and Linux archive have separate checksums in `SHA256SUMS.txt`.

This community build is unsigned. A browser-downloaded copy can receive Windows
reputation/publisher prompts; the VM transfer does not test that download path.

## Windows application acceptance from 0.1.0

Built on Linux with .NET SDK 10.0.404, targeting Windows x64 with .NET 10.0.15
and Windows Forms included. The gaming VM required no SDK or runtime install.
The application compiled with zero warnings and errors.

The portable Core suite passed 55 deterministic checks: backoff/jitter, separate
Steam and upload-endpoint cooldowns, state persistence/corruption, same-game
suppression, game priority, aggregate progress, partial output and HTTP access
failures. Tests use synthetic data and time; they do not cause real throttling.

Native validation ran as a non-elevated interactive user in a Windows 11 x64 VM:

- Hidden child stdout/stderr capture, including trailing throttle evidence.
- Optional Task Scheduler discovery, including absent-task handling and preservation
  of access errors; six native smoke checks passed on the final build.
- Job Object cleanup, including stopping an owned child when its job closes.
- Tray icon creation/dismissal and status-panel visual inspection.
- Synthetic recovery preempted by a game, followed by live sync.
- UI **Quit syncing** hid the icon, stopped the worker and suppressed that game.
- A later game re-enabled syncing; game exit retained the settling/retry deadline.
- Cooldown survived Quit, Enable and controller restart with zero idle workers.
- Exit app cleaned up the controller and worker.
- Installer first run, update, uninstall and reinstall preserved scheduling state;
  manual-only installation created no auto-start entry and started no worker.

Before the first live worker, with the panel hidden, actual process detection
enabled and no worker running, a 60-second sample used 58.9 MiB working set, 17.5 MiB private
memory and 234.375 ms CPU time (about 0.39% of one CPU core).
After running the game/worker, the controller reached about 78.6 MiB working set.
The observed live worker used about 15.4 MiB working set and 2.36 MiB private memory.
These are observations on the test VM, not fixed
resource limits. The self-contained executable is approximately 111 MiB on disk.

The tested 0.1.0 executable's SHA-256 is
`9492a117c463b85acc0110043f5d2da338ffd3cf13586ad57f0e34cdf9215819`.
The release archive has a separate checksum in `SHA256SUMS.txt`.

With the actual installed game, the controller detected the process and launched
the verified upstream executable with exactly `--no-gc`. After 60 seconds of game
warmup, the loaded 3D start screen, game modules and 4.11 GiB game working set were
confirmed. A fresh live worker completed a read-only memory pass in 4.4 seconds
without errors; it found zero salts in that session and logged no GC activity.
Normal game-window close stopped the live worker, and Quit/Exit preserved the
existing cooldown. No online match was joined.

A new played match appearing in Statlocker, a full history-recovery pass, logout/resume,
Explorer restart and broader Windows/hardware combinations remain separate
acceptance checks. Synthetic tests are not claims of completed remote indexing.

## Omarchy Linux

The packaged integration is the existing version 1.2.0 source snapshot. All 24
maintained file hashes/modes passed, along with 71 Python tests, JavaScript model
tests and an isolated offscreen QML check. Verification changed no live services
or desktop configuration.

The existing host installation has exercised native memory reading, partial
history uploads, a real Steam cooldown, progress display and manual quit/re-enable.
The current backfill is incomplete. Full historical coverage and a new match's
end-to-end Statlocker appearance are not asserted.
