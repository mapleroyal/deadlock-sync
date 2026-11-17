# Implementation handoff

For installation, read README.md and the relevant platform README, then use the
release installer. On Windows, the normal user path is the conventional
`Deadlock-Sync-0.3.0-Setup.exe` wizard: Next, Install, Finish. Do not substitute
PowerShell scripts, a source build or an agentic setup flow. It bundles the app,
runtime and verified ingest engine, runs per user without elevation, and opens
the enabled tray app at Finish. Fresh installs default to starting at login;
updates preserve the previous startup choice. Do not rebuild or re-research
unnecessarily. For code work,
read GUIDE.md, UPSTREAM.md and upstream.json. These are agreed product decisions,
not a request to compare frameworks. Inspect more upstream source only if the
pinned release differs or a target-specific problem appears. VALIDATION.md
distinguishes tests from end-to-end acceptance; preserve that distinction.

The Windows implementation is C# .NET 10 Windows Forms using the pinned selected-
account ingest extension from mapleroyal/deadlock-sync-engine. Keep policy/log
parsing independent of Windows APIs for cheap tests. Backfill always supplies
`--account-id` and never falls back to all-account mode. Keep Steam cooldowns,
progress and account login errors separate, while upload-endpoint limits remain
global. Preserve current-account priority, game priority and cross-worker pacing.
Build on a development host and validate in the target Windows account; keep
SDKs off gaming-only machines. Never run the Linux installer on Windows.
Build locally. GitHub hosts source and finished release assets only; do not add
CI/CD workflows or remote build pipelines. Keep GitHub Actions disabled.
Windows validation must be headless while the owner is away or using Screens
Off. Do not start the GPU gaming VM, wake a physical monitor or change GPU
lighting to test this app. Prefer an isolated virtual-display environment.
The native NSIS installer lives in `windows/installer/`; build it with
`tools/build-setup.py` and package releases with `tools/package.py`. See the
Windows README for the exact inputs. Keep installation offline-capable, use
HKCU's 64-bit registry view for Installed apps, and preserve the setup mutex,
IPC shutdown checks and transactional app-directory replacement. Native
uninstall retains private state and upstream attempt records. Silent installer
flags are for maintenance/testing, not the foreground user instructions.
The Linux integration requires Omarchy. Do not use upstream GUI/installer
auto-start alongside either controller.

Do not commit credentials, logs, match history, recovery state, downloaded
binaries or generated builds. Keep upstream retry records intact. Do not test by
causing real throttling. Respect existing game launch options and unrelated
startup entries. No global input, cursor, graphics, security or network changes
are needed. Windows-specific privilege failures need a targeted diagnosis.

Deliver build/install/uninstall commands, a working launch shortcut, measured
idle resource use, test results and a short list of any real-game checks still
pending. Keep documentation updated around the final implementation.
