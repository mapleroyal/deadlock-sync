# Deadlock Sync for Omarchy Linux

This download contains the existing Omarchy bar integration and its installer.
It requires the current Omarchy shell/Quickshell, Python 3, systemd user services
and x86-64 Linux. It is not a generic GNOME/KDE tray package.

From this directory:

```sh
python omarchy/install.py
python omarchy/install.py --apply --memory-helper
omarchy restart shell
```

The first command is a preview. Installation downloads the hash-pinned selected-
account engine and original upstream memory helper. Installing the helper asks for system authentication;
only that root-owned helper receives `cap_sys_ptrace`. Leave system-wide ptrace
restrictions unchanged. Installation itself does not start a history pass.

Launch **Deadlock Match Backfill** or run `deadlock-backfill` to enable automatic
recovery. Starting a new Deadlock session also enables it. Live memory sync uses
`--no-gc`; history recovery runs only while the game is closed. Keep Steam launch
options unchanged.

The current Steam account gets backfill priority. Switching accounts updates
that priority automatically; another account's work yields to the new eligible
current account, retaining at least 20 seconds between workers. Each account has
its own Steam cooldown, progress and login status, so other eligible accounts can
continue while one waits. An upload-service-wide limit still pauses all uploads.
Each account's history starts with its newest page and works backward.

You can keep Steam set to ask which account to use at startup. If Steam
declines a saved login for an account you are not using, the panel names that
account and keeps showing whether your current account is caught up. The next
time you choose the affected account in Steam, recovery retries automatically
once the game is closed and its cooldown has passed. Follow any sign-in prompt
Steam shows for that account.

Click the gamepad icon for activity, counts and the next retry time. Right-click
**Quit syncing** to stop both jobs, disable recovery and hide the icon. A new
game session or the launcher enables it again. Enabled recovery keeps the icon
visible after game exit, including during cooldown and caught-up states.

There is no extra resident watcher: the existing shell schedules the work. Steam
throttling waits 24 hours plus a little jitter; this is a retry policy, not a
claimed daily reset time. Account login errors pause that account; shared storage
problems require attention before any history work continues. Legacy cooldowns
are assigned to an account only when reliable old invocation evidence exists;
otherwise the old deadline remains a temporary shared hold. Logs and attempt
records stay local. Do not use upstream's GUI
setup alongside this integration or delete its attempt records.

To validate the package without changing the desktop:

```sh
python -B omarchy/verify.py
python -B omarchy/verify.py --qml
```

The second command uses an isolated offscreen UI harness. An actual game launch,
memory-read confirmation and later Statlocker appearance are separate checks.

The stable plugin ID `user1.deadlock-ingest` is an identifier, not an account
name to replace. Installation merges its bar entry and preserves other entries,
settings and recovery deadlines. Its backups live under
`~/.local/state/deadlock-api-ingest/restore-backups/`.

For removal, Quit syncing first. Remove only this plugin's layout entry and
`~/.config/omarchy/plugins/user1.deadlock-ingest/`, its two
`deadlock-api-ingest*.service` user units, the `deadlock-backfill` launcher and its
desktop entry. Reload the user service manager and restart the shell. Keep
upstream data and `~/.local/state/deadlock-api-ingest/` so reinstalling cannot
reset cooldowns or attempts. The root-owned memory helper may be retained or
removed separately after confirming no other ingest installation uses it.

Install and restart the shell only while the desktop is awake and Screens Off
has fully exited. Plugin reloads also unload the Screens Off controller, which
can wake displays. Preparing an archive or running isolated tests is safe while
Screens Off is active; live activation must wait.
