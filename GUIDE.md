# Recreate Deadlock Sync on Windows

This is the implementation contract for the Windows tray app. Read it with
[UPSTREAM.md](UPSTREAM.md) and [upstream.json](upstream.json). The architecture
and policy are already decided; commands and small implementation details can
be adapted to the target. The Linux reference is in `linux/omarchy/`.

## 1. Build the small supervisor

Use C# and .NET 10 Windows Forms, `ApplicationContext`, `NotifyIcon`, a small
status form and a context menu. Target Windows x64. Use one background controller
in the interactive Steam user's session. No web runtime, database server,
Windows service, system account or elevated scheduled task is needed. Microsoft
documents [NotifyIcon](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon?view=windowsdesktop-10.0)
and [ApplicationContext](https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.applicationcontext?view=windowsdesktop-10.0).

Keep scheduling, log parsing and state validation in a platform-independent
library. Test it using injected time, deterministic jitter and synthetic worker
output. The Windows layer owns process handles, the tray, local single-instance
IPC and installation. Publish a self-contained Windows build so the gaming PC
needs neither an SDK nor a separate runtime install.

Poll the current session's `deadlock` and `project8` processes every two seconds
inside the controller; do not launch a shell or WMI command every tick. Identify
a game session by PID and creation time, verifying its executable location when
accessible. Use process existence, not focus, fullscreen state or a window title.
Serialize all lifecycle transitions. Re-check for a game immediately before
starting history recovery.

Use a per-user/session single-instance mutex and a current-user-only named pipe.
A second launcher invocation enables the existing app instead of creating a
second uploader. Do not accept arbitrary executable paths or shell commands
through IPC.

## 2. Own exactly one ingestion worker

Download and SHA-256-check the raw EXE pinned in `upstream.json`. Save it as
`deadlock-api-ingest.exe`; never rename it to a name containing `-setup`.

| Situation | Child command |
| --- | --- |
| Deadlock running | `deadlock-api-ingest.exe --no-gc` |
| Game closed and recovery eligible | `deadlock-api-ingest.exe --own-matches` |
| Cooldown, caught up, disabled or attention | No ingestion child |

Memory reading is already enabled. Do not combine the two commands or add
`--no-memory`, `--once`, `--gui` or `--install`. Preserve Steam launch options.
Run as the ordinary Steam user. If memory access is denied, diagnose differing
privilege levels and report it; do not silently elevate or change protections.

Each child belongs to an owned Windows Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. Create suspended, assign to the job, then
resume; abort before execution if assignment fails. Do not put Steam or the game
in that job. Never kill processes by a global name match. This prevents an
uploader surviving a controller crash. See Microsoft's
[Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

Use windowless process creation and redirected stdout/stderr; drain both
asynchronously. Set `RUST_LOG=info,deadlock_api_ingest::game_memory=debug` for useful
progress without noisy general debug logs. Windowless workers do not have a
normal console to receive Ctrl+C: use bounded owned-job termination when needed,
then drain remaining output before recording the outcome. `CloseMainWindow()`
does not stop a console worker. Never claim that a terminal was opened.

## 3. Apply this lifecycle

1. Startup loads persisted state. Start only the controller; decide whether a
   worker is eligible from current game state and deadlines.
2. A new game session enables syncing and marks history pending. Stop any
   recovery child completely, consume its final output, then start live
   `--no-gc`. Game takeover is intentional interruption, not a failed attempt.
3. Game exit stops live ingestion promptly. Keep enabled recovery pending and
   wait at least 60 seconds after exit, or longer if a retry deadline applies.
   In-game Exit, Alt+F4 and a crash are all process exits.
4. Run one eligible recovery pass. A successful pass becomes **Caught up** and
   waits for subsequent gameplay before another automatic history check.
5. Steam cooldown or a backfill authentication problem does not prevent live
   memory ingestion. Do not use the game-priority rule to bypass a server's
   explicit restriction on the upload endpoint itself.
6. Enforce a six-hour maximum per recovery invocation. Handle sleep/resume by
   reconciling current time and process state once; never replay missed ticks.

The original game-only icon lifetime was superseded by continuous recovery:
the icon remains visible while recovery is enabled, including during waiting,
caught-up and attention states. The ingest process itself stops when inactive.

**Quit syncing** saves disabled intent first, stops owned workers, cancels
automatic recovery and hides the icon. Suppress reactivation for the same game
identity, including after controller restart. A later distinct game launch or
the Start Menu launcher enables it again.

**Exit app** additionally terminates the controller. Automatic game detection
then stops until manual launch or the next configured Windows login. Windows
needs the lightweight controller to detect a future game; hidden disabled mode
cannot honestly be described as zero resident processes.

## 4. Persist cooldowns before doing work

Store versioned JSON in `%LOCALAPPDATA%\DeadlockSync\state\`, separately from
upstream state. Include enabled/pending, next attempt UTC, hard cooldown UTC,
separate upload-endpoint cooldown UTC, failure count/reason, last and suppressed game identities, processed run ID,
numeric progress and notification deduplication. Use atomic replacement, validate
types/ranges/size and retain an invalid original for repair. Corrupt state blocks
backfill; never treat it as a fresh eligible schedule.

Before launching recovery, persist a five-minute reservation against rapid
crash/relaunch loops. Give each run a unique ID. Record/consume completion once,
including final lines received during termination. A newer failure, Enable,
Quit, new game, restart, login or reinstall must never shorten a known hard
cooldown. Store UTC, display local time. Clock changes and resume must not create
parallel work or a burst of retries.

| Outcome | Action |
| --- | --- |
| Confirmed Steam throttling | Stop recovery; next attempt at evidence time + 24 hours + random 0–300 seconds. Clamp future evidence timestamps to now. Preserve any later existing deadline. |
| Explicit upload-endpoint HTTP 429 | Stop either worker and persist a separate deadline blocking both modes. Use the same conservative fallback when no server delay is exposed; ordinary Steam GC throttling still permits live memory sync. |
| Temporary network/GC error or unknown nonzero exit | 5, 10, 20, 40, then 60 minutes, plus random 0–60 seconds. Respect hard cooldown. |
| Steam account playing another game | 15, 30, then 60 minutes, plus random 0–60 seconds. Respect hard cooldown. |
| Missing/expired login, private history, permission failure | Attention; no repeated automatic attempts. Explicit Enable may retry after the user repairs access, retaining cooldown. |
| Attempt-store failure or corrupt controller state | Stop recovery and show attention. Do not erase or reset attempt records. Corruption requires repair, not an Enable button reset. |
| Deliberate game takeover or Quit | Keep pending; no failure-count increment. Preserve throttle/error evidence already seen. |
| Successful exit, no stronger evidence | Caught up; retain skipped/unavailable counts and wait for another game. |

Classify storage failure before throttle, then permanent access/auth problems,
account busy and transient failure. Independently retain throttle evidence even
if a stronger attention condition wins. Honor any explicit longer server retry
instruction. Do not use parallel workers/accounts, repeated restarts, attempt
store deletion or a faster metadata cadence to increase throughput.

This 24-hour policy follows upstream's conservative GC backoff. It is **not a
known Steam daily reset time**. Own-history mode is not restricted to upstream's
40-per-day community-recovery allowance; it uses its own paced recovery until
Steam throttles or available work finishes. Details and caveats are in UPSTREAM.

## 5. Show meaningful status

Left click opens a compact panel; right click includes Enable syncing, Quit
syncing and Exit app. Show current activity, processed/total missing matches,
confirmed uploads, failures this pass, unavailable old matches, last update and
the next attempt's local time/reason. Live mode says reading/waiting/error; it
does not invent a percentage. Display unknown counts as unknown.

Parse only output from the current owned invocation using UPSTREAM's markers.
`(I/T)` is a processed position, not the number successfully uploaded. Count
successes separately from failures; keep per-account batch progress separate or
aggregate completed batches explicitly. A missing/truncated baseline cannot
justify upload totals. Strip ANSI and bound line lengths and retained logs.
Expose fixed status text and counters, not raw tokens, salts or account IDs.

Notify once on meaningful cooldown, attention or caught-up transitions. A
throttle notification points to the panel's scheduled retry and leaves the icon present.
Recreate the tray icon after Explorer restarts. Windows may place it in the tray
overflow; explain how to pin it rather than changing Explorer's global settings.

## 6. Install cleanly and enroll Steam locally

Install app files under `%LOCALAPPDATA%\DeadlockSync\app\`. The installer
verifies downloads, creates a Start Menu **Deadlock Sync** shortcut and an HKCU
Run entry for `--background`. Run as the logged-in user. Keep install/uninstall
idempotent; stop this app through IPC before replacing files. No SDK, admin
service, scheduled SYSTEM task or global execution-policy change is needed.

Backfill uses Steam's remembered sessions and current-user DPAPI. The user signs
in and completes Steam Guard in Steam itself. The wrapper never asks for, reads
or stores a password/token. Live memory sync can work even when remembered
credentials are unavailable.

Upstream processes **all decryptable remembered Steam accounts** and has no
select-account option. On a shared/multi-account Steam installation, explain
that scope and have the owner manage saved accounts in Steam or accept it.
Do not silently edit Steam token files or claim only the currently visible
account will be processed.

Detect existing upstream auto-start entries/instances and explain conflicts.
Do not run this controller alongside its default GUI watcher. Preserve all
upstream data in `%APPDATA%\deadlock-api-ingest\`, especially `gc-quota.json`.
Uninstall removes only this app, its shortcut and startup entry; retain cooldowns
and upstream attempts by default. Upgrades must also preserve those files.

When moving the same account between Linux and Windows, preserve the later
known cooldown and existing attempt history. Keep only one recovery owner
enabled for that account across computers or a host/VM pair. Do not use a clean
platform install as a fresh quota. Keep transferred account/runtime data private.

## 7. Check the result

Use fake workers and clocks for automation; never induce a real throttle.

| Check | Required result |
| --- | --- |
| Idle/cooldown | One small controller, zero uploaders; tray gives reason and next attempt. |
| Duplicate launch | One controller and at most one worker. |
| Game starts during recovery | Recovery fully stopped before live starts; pending progress retained. |
| Game closes | Live stops; recovery waits 60 seconds and respects later deadlines. |
| Quit during a game | Workers stop, icon disappears, same game stays suppressed. |
| Next game or explicit Enable | Sync re-enables without clearing cooldown. |
| Exit/controller crash | No orphan child or owned descendant. |
| Restart/login/resume | State/deadlines persist; no burst of retries. |
| Trailing throttle line | Drained and persisted even during takeover or termination. |
| Corrupt state/attempt store | Recovery blocked with explanation; original retained. |
| Partial/failed/skipped history | No false 100% or claim all historic matches reached Statlocker. |
| Explorer restart and tray clicks | Icon returns; details/quit work without terminal flashes. |

Measure idle CPU and working set on the actual machine. Aim for negligible CPU
and tens of MiB for the controller; publish observations rather than asserting
an unmeasured Windows footprint. Live memory reads must be confirmed by logs,
not process existence. Confirm one new match eventually appears in Statlocker
separately from local upload acceptance. Older metadata and remote indexing can
remain unavailable, so complete historical coverage is not guaranteed.

Keep the final README short: download/install, launch/quit, retry behavior,
uninstall and the acceptance checks still pending. The sources and deterministic
tests should let another agent maintain this without redoing the research.
