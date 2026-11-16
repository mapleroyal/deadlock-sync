# Implementation handoff

For installation, read README.md and the relevant platform README, then use the
release installer. Do not rebuild or re-research unnecessarily. For code work,
read GUIDE.md, UPSTREAM.md and upstream.json. These are agreed product decisions,
not a request to compare frameworks. Inspect more upstream source only if the
pinned release differs or a target-specific problem appears. VALIDATION.md
distinguishes tests from end-to-end acceptance; preserve that distinction.

The Windows implementation is C# .NET 10 Windows Forms using the official ingest
binary. Keep policy/log parsing independent of Windows APIs for cheap tests.
Build on a development host and validate in the target Windows account; keep
SDKs off gaming-only machines. Never run the Linux installer on Windows.
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
