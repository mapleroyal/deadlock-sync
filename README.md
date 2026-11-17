# Deadlock Sync

Live Deadlock match syncing and automatic history recovery, with status and Quit
controls. Uses a pinned **deadlock-api-ingest** engine extension to feed Deadlock API,
whose data is used by Statlocker. The Windows app uses a small native tray
controller; the Linux version integrates with the existing Omarchy bar.

## Download

| Platform | Download | Setup |
| --- | --- | --- |
| Windows x64 | [Windows installer](https://github.com/mapleroyal/deadlock-sync/releases/download/v0.3.0/Deadlock-Sync-0.3.0-Setup.exe) | Double-click, then **Next → Install → Finish**. [Windows instructions](windows/README.md). |
| Omarchy Linux x64 | [Linux archive](https://github.com/mapleroyal/deadlock-sync/releases/download/v0.3.0/deadlock-sync-0.3.0-linux-omarchy-x64.tar.gz) | Extract, preview/install, then reload the shell. [Linux instructions](linux/README.md). |

To update Windows, run the new installer over the existing copy. Leave **Start
Deadlock Sync** checked on the Finish page to reopen the tray app. The installer
keeps your history progress, cooldowns and Windows startup choice. Starting with
Windows is checked by default on a fresh install. No terminal, administrator access,
.NET installation or SDK is needed. Everything for installation is bundled;
syncing needs internet access. This community build is unsigned, so Windows may
show a publisher warning.

See the [release page](https://github.com/mapleroyal/deadlock-sync/releases/tag/v0.3.0)
for checksums and validation notes. This release is a preview. The Linux
package requires Omarchy; it is not a generic GNOME/KDE tray application.

## Behavior

- While Deadlock runs, the reader uses `--no-gc` with memory reading enabled.
- Gameplay preempts history recovery. After game exit, eligible recovery resumes
  following a short settling delay.
- History recovery starts with each account's newest history page and works
  backward, preserving Steam's order within each page.
- The current Steam account gets priority. Switching accounts moves the new
  eligible account ahead of other history work after a short pacing delay.
- Each account has its own progress, login errors and Steam cooldown, normally
  24 hours plus a small jitter. Other eligible accounts can continue while one
  waits. An upload-service-wide limit still pauses uploads for every account.
- Temporary failures back off. Authentication, access or storage problems need
  attention. Cooldowns and attempt records survive restarts and updates.
- **Quit syncing** stops work and hides the icon. A later game or explicit launch
  enables it again. Windows also offers **Exit app** to stop its small watcher.

Sign in to Steam normally and let it remember your login. History recovery uses
that saved session to access your match history while the game is closed; it
does not ask for your password. Recovery includes all decryptable remembered
accounts, so review saved accounts on shared computers. Enable only one recovery
owner for an account across computers or a Linux host/Windows VM pair. Do not
also run upstream's default GUI watcher.

The app notices account changes from Steam's local account information. It
recovers the current account first when eligible, then other remembered accounts
one at a time. It preserves each account's cooldown across switches and restarts;
switching never resets a limit. The panel shows which account is being recovered
and which accounts are waiting or need attention.

Accepted uploads and matches visible in Statlocker are different milestones.
Remote indexing can lag, and old unavailable data means complete historical
coverage cannot be guaranteed. See [validation](VALIDATION.md) for tested
behavior and remaining checks.

To update Windows, run the newer installer. To remove it, use **Settings → Apps
→ Installed apps → Deadlock Sync → Uninstall**. Saved cooldowns and private
recovery records are retained, including across updates.

## Development

The ready-to-use installer is the normal Windows installation path. For source
changes, the [implementation guide](GUIDE.md) specifies architecture, lifecycle, retry
policy, persistence, UI and acceptance checks. [Upstream facts](UPSTREAM.md)
captures verified Windows behavior and log markers. [upstream.json](upstream.json)
pins the Windows engine executable and checksum; the Linux package has its
own pin. Source builds and tests are described in the platform instructions.

No account state, credentials, match history or private logs belong in this
repository. This is an independent community integration.
