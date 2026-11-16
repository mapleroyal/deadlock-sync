# Deadlock Sync

Live Deadlock match syncing and automatic history recovery, with status and Quit
controls. Uses the official **deadlock-api-ingest** engine to feed Deadlock API,
whose data is used by Statlocker. The Windows app uses a small native tray
controller; the Linux version integrates with the existing Omarchy bar.

## Download

| Platform | Download | Setup |
| --- | --- | --- |
| Windows x64 | [Windows ZIP](https://github.com/mapleroyal/deadlock-sync/releases/download/v0.1.0/deadlock-sync-0.1.0-windows-x64.zip) | Extract, then run `Install.ps1` as your ordinary Windows user. [Windows instructions](windows/README.md). |
| Omarchy Linux x64 | [Linux archive](https://github.com/mapleroyal/deadlock-sync/releases/download/v0.1.0/deadlock-sync-0.1.0-linux-omarchy-x64.tar.gz) | Extract, preview/install, then reload the shell. [Linux instructions](linux/README.md). |

See the [release page](https://github.com/mapleroyal/deadlock-sync/releases/tag/v0.1.0)
for checksums and validation notes. This initial release is a preview. The Linux
package requires Omarchy; it is not a generic GNOME/KDE tray application.

## Behavior

- While Deadlock runs, the reader uses `--no-gc` with memory reading enabled.
- Gameplay preempts history recovery. After game exit, eligible recovery resumes
  following a short settling delay.
- Throttled recovery stops its worker and waits, normally 24 hours plus a small
  jitter. The icon stays visible with progress and the next attempt time.
- Temporary failures back off. Authentication, access or storage problems need
  attention. Cooldowns and attempt records survive restarts and updates.
- **Quit syncing** stops work and hides the icon. A later game or explicit launch
  enables it again. Windows also offers **Exit app** to stop its small watcher.

Steam login remains in Steam. Recovery includes all decryptable remembered
accounts, so review saved accounts on shared computers. Enable only one recovery
owner for an account across computers or a Linux host/Windows VM pair. Do not
also run upstream's default GUI watcher.

Accepted uploads and matches visible in Statlocker are different milestones.
Remote indexing can lag, and old unavailable data means complete historical
coverage cannot be guaranteed. See [validation](VALIDATION.md) for tested
behavior and remaining checks.

## Handoff to a coding agent

Installation does not require rebuilding or researching alternatives. Give the
agent this prompt:

> Set up Deadlock Sync from https://github.com/mapleroyal/deadlock-sync on this
> computer. Read README.md and the instructions for this platform; use the
> matching release download and its installer. Keep Steam launch options and
> existing cooldown/attempt records. Verify the tray/bar and game lifecycle.
> Only ask me for Steam authentication or information you truly need. If code
> changes are necessary, follow GUIDE.md and UPSTREAM.md instead of repeating
> the research. Report any actual-game or Statlocker checks still pending.

The [implementation guide](GUIDE.md) specifies architecture, lifecycle, retry
policy, persistence, UI and acceptance checks. [Upstream facts](UPSTREAM.md)
captures verified Windows behavior and log markers. [upstream.json](upstream.json)
pins the official Windows executable and checksum; the Linux package has its
own pin. Source builds and tests are described in the platform instructions.

No account state, credentials, match history or private logs belong in this
repository. This is an independent community integration.
