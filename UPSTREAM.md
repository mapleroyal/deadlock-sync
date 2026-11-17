# Engine integration and upstream facts

The base upstream source was verified at tag `v0.4.8` on 2026-11-16, commit
`ef1882306163826809cb2768a39b4c4f8b258545` (released 2026-11-06).
Deadlock Sync 0.2.0 uses the [selected-account engine extension](https://github.com/mapleroyal/deadlock-sync-engine),
based on that tag. Exact shipped binaries, source revision and hashes are pinned
in `upstream.json` and `linux/omarchy/upstream.json`.

## Selected-account extension

- The supervisor launches `--own-matches --account-id ID32`, using a canonical
  nonzero 32-bit Steam account ID. The selector requires own-history mode and
  recovers only the selected remembered account. Missing or unusable credentials
  fail with an authentication error; there is no fallback to another account.
- Each worker owns exactly one account, so its Steam throttles, login errors,
  progress and completion can be attributed to that selected account. The
  supervisor retains separate persistent schedules and prioritizes the current
  eligible account. A separate upload-endpoint limit remains global.
- The extension retains the upstream 20-second request spacing and shared
  per-match attempt store. Supervisors additionally retain 20 seconds between
  backfill worker invocations, including account-switch preemption.
- The supervisors read nonsecret Steam account metadata, watch account changes
  and select one account before each worker launch. They do not decrypt tokens
  or maintain a second password/login UI.
- The base upstream behavior below still applies unless this section explicitly
  changes it. In particular, unselected `--own-matches` remains all-account mode,
  but the new supervisors always supply the selector.

## Original upstream binary reference

- Raw executable: `https://github.com/deadlock-api/deadlock-api-ingest/releases/download/v0.4.8/deadlock-api-ingest-windows-latest.exe`
- SHA256: `d0e2a772ffc075713cc21759cb0a3b9e378a3235e59fc25cc7ed5b5d1f884464`
- Size: `32569344` bytes.
- Architecture: `x86_64-pc-windows-gnu` (64-bit Windows).
- Rename to `deadlock-api-ingest.exe` under the custom app's private binary directory. Do not include `-setup` in the name: the same bytes named `*-setup*.exe` self-install when launched without arguments.
- The release's `deadlock-api-ingest-setup.exe` is byte-identical but takes the self-installer path. Do not use it for the custom lifecycle.

Sources: [release API](https://api.github.com/repos/deadlock-api/deadlock-api-ingest/releases/tags/v0.4.8), [release](https://github.com/deadlock-api/deadlock-api-ingest/releases/tag/v0.4.8), [build targets](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/.github/workflows/main.yml), [installer](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/install.rs).

## Commands and scope

- Live worker: `deadlock-api-ingest.exe --no-gc`. Memory reading is ON by default; omit `--no-memory`. This worker initially scans Steam HTTP caches, keeps watching them, and reads the running game. It does not exit merely because the game closes: the custom supervisor must own its lifetime.
- Own-history worker: `deadlock-api-ingest.exe --own-matches --account-id ID32` in Deadlock Sync 0.2.0. Separate one-shot process; **cannot** combine with `--no-gc`, `--once`, or a wrapped game command.
- Backfill refuses to start with Deadlock running. It also checks while processing and aborts if Deadlock launches, but the supervisor should preempt promptly rather than wait for its next check.
- `--own-matches` runs before the main watcher singleton lock is acquired. The supervisor must prevent concurrent backfills, and serialize live/backfill itself.
- `--gui` is NOT a passive status reader: if no watcher is running it sets up autostart and starts a default no-argument watcher. `--install` and the setup executable open that GUI. Avoid all three in this custom app.
- Do not replace Steam launch options with the upstream wrapper for this implementation; a user-session supervisor covers normal Steam launches while preserving existing options.

Sources: [main](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/main.rs), [GC controller](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/mod.rs), [service registration](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/service.rs).

## Windows permissions and process discovery

- Run the supervisor and workers as the signed-in Steam Windows user, with ordinary non-admin privileges. Upstream Windows reader requests only `PROCESS_VM_READ` and query rights, uses `ReadProcessMemory`, and never writes/injects into the game. There is no Linux memory helper or ptrace configuration on Windows.
- If memory access fails, show an actionable error. Check whether Steam/game was run elevated rather than silently escalating the supervisor or disabling protections. The release README says ordinary user permissions are supported; whether an unusual target configuration permits memory reads still requires target validation.
- Upstream process names are case-insensitive `deadlock.exe`, `project8.exe`, and `project8`. In .NET `Process.GetProcessesByName`, omit `.exe`: `deadlock`/`project8`. Match a known game executable path/owner when available; use PID plus process creation time as a session identity. Window title is not a reliable detector.
- Steam app ID: `1422450`.
- The memory worker waits at least five minutes after a pass, or four times the pass duration if larger. It checks for a game every 30 seconds when none is found and pauses 40 ms per 64 MiB scanned. Starting a fresh live worker at game launch allows its first pass promptly. Do not add extra memory-reader instances to increase cadence.
- Useful logs: `memory: reading the game (pid N)`, `memory: read the game in ..., salts for N matches`, `memory: ingested salts: ...`; failure: `memory: not allowed to read Deadlock's memory: ...`. Do not label live memory healthy solely because the process exists.

Sources: [Windows memory reader](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/game_memory/windows.rs), [memory cadence](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/game_memory/mod.rs), [game names](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/game_names.rs), [README](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/README.md).

## Steam credentials and local data

- Original upstream backfill uses every decryptable remembered Steam account in the primary Steam install and has no selector. The extension adds strict single-account selection; the supervisors recover eligible remembered accounts sequentially with current-account priority. Keep each account's own cooldown intact, and never switch credentials to retry a blocked account's work.
- Authentication setup: the user signs into their intended Steam account in Steam normally, enables the remembered login, and completes Steam Guard locally. Keep the credentials in Steam; do not copy `local.vdf` or implement a second login UI. If several accounts are remembered, have the user manage unwanted saved sessions in Steam rather than editing token files. Upstream verifies each recovered token identity against its corresponding account record.
- It reads Steam install `config\loginusers.vdf` and `%LOCALAPPDATA%\Steam\local.vdf`. The session token in the latter is decrypted using current-user Windows DPAPI. Steam must have remembered the login; the wrapper should never collect a password or copy/decrypt tokens itself.
- Live `--no-gc` never calls this GC authentication recovery and does not require a decryptable remembered token. Its account credit is read optionally from `loginusers.vdf` (`MostRecent` or `AutoLogin`). A backfill auth failure must not disable otherwise healthy live memory/cache sync.
- Live upload-credit identity is cached once per worker; a normal game close/account switch/relaunch starts a fresh worker. Memory reads follow the running game rather than filtering by that cached identity. The new supervisors watch metadata changes independently of worker startup and can preempt a background account for a newly current eligible account. The selected worker's identity remains fixed until it exits; account switches never reassign its completion or clear its cooldown.
- Use an interactive per-user login task or HKCU startup entry for the **supervisor only**, not a SYSTEM service, S4U/non-interactive task, or another Windows user. If using Task Scheduler: run only while the user is logged on, normal privileges.
- Upstream mutable state is `%APPDATA%\deadlock-api-ingest\` (Roaming), NOT the executable's LocalAppData install directory. It contains `gc-quota.json`, `gc-quota.lock`, `instance.lock`, `game-memory.lock`, `fetched-salts.jsonl`, and `logs\`. Daily logs retain up to seven files.
- Preserve all upstream attempt/quota state on upgrades and retries. Never clear `gc-quota.json` to make another attempt. Wrapper recovery state belongs in a separate app-owned directory.
- Upstream own-history mode logs a warning and may continue **without** its attempt store if opening fails. The wrapper should treat `store unavailable, not tracking salt attempts` / `GC store error` as an attention condition and stop the child promptly; do not allow an untracked retry loop. Validate writable storage and parseability before launch without rewriting upstream files.

Sources: [auth](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/auth.rs), [live credit identity](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/steam_user.rs), [own-history account snapshot](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/mod.rs#L508-L557), [data paths](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/paths.rs), [store](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/store.rs), [main logging](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/main.rs).

## Console and log capture

The binary uses the Windows console subsystem. For C# child launch use `UseShellExecute=false`, `CreateNoWindow=true`, `RedirectStandardOutput=true`, `RedirectStandardError=true`, and drain both asynchronously. Keep the process handle. Upstream tracing writes stdout plus its own daily rolling log; default filter includes debug messages needed to confirm successful memory reads. Strip ANSI sequences before parsing and show only selected text in the UI. Do not use `--gui`, a detached launch, or a terminal launcher.

The watcher's `hide_own_console()` calls `FreeConsole()` only when it finds exactly one attached process. Creating the child without a console and redirecting pipes avoids a throwaway terminal and preserves capture. If stdout capture proves incomplete on Windows, follow the current invocation's newly appended log offsets with identity/timestamp guards rather than reading historic outcomes. Ordinary Rust tracing logs are not guaranteed machine-readable JSON.

Sources: [console](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/console.rs), [tracing](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/main.rs), [Windows interactive principal comment](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/install-windows.ps1#L268-L271).

## Pacing and incomplete history

- Own-history mode follows Steam history pages, compares known match IDs with deadlock-api, then obtains missing salts via Steam GC and submits them to deadlock-api. IDs alone are insufficient for this local salt-recovery path.
- Recovery starts with each account's newest history page, then walks backward through older pages. `fetch_match_history` starts without a cursor, appends pages in their returned order and requires the cursor to decrease. The missing/given-up filters preserve that order. This is newest-pages-first, not an explicit timestamp sort within a page; within-page order comes from Steam. The supervisors choose the current eligible account first; they do not globally merge histories from several accounts. Preserve this ordering when updating the engine and recheck pagination plus filtering together. See [pagination](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/client.rs#L273-L397) and [own-history filtering/processing](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/mod.rs#L600-L645).
- The upstream `gc_backoff_until` account field is maintained by background community recovery, not own-history mode. It cannot attribute an old supervisor-wide own-history cooldown to an account. Migration must use trustworthy invocation-specific throttle evidence or retain the unknown deadline as a temporary shared hold.
- Each metadata request starts at least 20 seconds after the preceding metadata request. This is upstream code; do not patch it or parallelize children.
- The default background GC has local caps of 40 salt requests and 20 friend-history requests per account per rolling 24 hours. **Those are not own-history mode's daily allowance.** Own-history mode intentionally bypasses that local 40-request cap and continues until done or Steam explicitly rate-limits it.
- Steam does not supply a documented fixed daily reset time here. A wrapper wait of 24 hours plus positive jitter after confirmed throttling is a conservative policy inspired by upstream's 24-hour GC backoff, not a claim of Valve's reset time. Persist the deadline across quit, restart, sleep, login, and reinstall.
- Partial history pagination can be rate-limited: the tool may process the pages already retrieved, then returns a throttled outcome. Do not report all history complete just because its current batch had no missing salts.
- Per-match attempt limit is three. Successful salt submissions and per-match fetch failures count; rate limiting and failed HTTP posts do not. Matches still absent from the API after the limit are skipped forever by the store. Keep these visible as unavailable rather than promising complete historic coverage.
- A successful salt POST means the API accepted salts, not that Statlocker has finished ingesting/displaying the match. Remote processing may lag and Valve may no longer serve older metadata.
- v0.4.8 internally retries non-400 HTTP failures up to ten times, three seconds apart; this is an upstream limitation. Do not add wrapper-level immediate retries. A visible HTTP 429 should become cooldown; permanent HTTP/auth failures should become attention. The pinned release has no CLI to tune HTTP retry behavior.

Sources: [GC recovery and constants](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/mod.rs), [history pagination](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/client.rs), [attempt storage](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/store.rs), [HTTP submission](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/utils.rs).

## Stable log strings for the parser

Use the pinned version and test against synthetic fixtures, stripping ANSI escapes. Attribute lines only to the current process/run; never scan all old rolling logs to infer a new outcome.

| Meaning | v0.4.8 text/pattern |
| --- | --- |
| Batch discovered | `gc: account ACCOUNT has H match(es) in history, M missing salts (~20s each)` |
| Accepted upload and position | `gc: ingested match MATCH (I/M)` |
| Permanently skipped | `gc: account ACCOUNT skips N match(es) given up on after 3 attempts` |
| Match failure | `gc: salt fetch failed for MATCH: ...` |
| HTTP post failure | `gc: salt POST failed for MATCH: ...` |
| Steam throttled | `Steam GC rate-limited the request`; `gc: account ACCOUNT was rate-limited by Steam`; `Steam rate limit reached. Run this again on another day to fetch the remaining matches.` |
| Partial history throttled | `gc: match history rate-limited, continuing with N match(es)` |
| No remembered session | `gc: no usable Steam account (is Steam logged in with 'remember me'?): ...` |
| Account playing another game | `the Steam account is playing a game` |
| GC outage/handshake | `Steam GC unavailable: ...`; `Steam GC did not answer: ...` |
| Game arrived | `Deadlock was launched`; `Deadlock is running, close it and run this again` |
| All attempted | `Done, all own matches processed.` |
| Incomplete | `N match(es)/account(s) could not be processed. Run this again to retry.` |

Exit code 0 is returned only when no rate limit or failed match/account was reported; 1 means incomplete. Skipped-after-three matches can coexist with exit 0, so success does not prove all historic matches reached Statlocker. `I/M` is a position, not the successful upload count (failures can produce gaps); aggregate separate successful and failed counters. Multiple accounts emit multiple batch headings; totals may grow.

Source: [GC controller](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/mod.rs), [GC errors](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/gc/error.rs), [main exit status](https://github.com/deadlock-api/deadlock-api-ingest/blob/v0.4.8/src/main.rs).
