# Third party components

Deadlock API Ingest is a separate upstream program distributed under the MIT
license. Deadlock Sync uses a small, separately published
[engine extension](https://github.com/mapleroyal/deadlock-sync-engine) based on
upstream v0.4.8 that adds strict single-account history selection. It preserves
the upstream license and request pacing. The Windows installer bundles this
pinned engine executable and
`INGEST-LICENSE.txt`; the build verifies the pinned executable and license
hashes, and the app verifies the executable before use. The Linux installer
fetches its pinned executable and verifies its SHA-256. Upstream source and
notices are maintained at
https://github.com/deadlock-api/deadlock-api-ingest.

Self-contained Windows builds include Microsoft's .NET runtime and Windows
Forms libraries, distributed under their own licenses. Their notices are
included in the installed Windows app directory as `DOTNET-LICENSE.txt` and
`DOTNET-THIRD-PARTY-NOTICES.txt`.

The Windows setup and uninstaller use NSIS. `NSIS-NOTICES.txt`, included beside
the installed app and sourced from the official Debian NSIS 3.13 distribution's
copyright file, contains the runtime, plugin and compression notices, including
the LZMA exception. Its source copy is in `windows/installer/NSIS-NOTICES.txt`.

The Linux UI uses the target's installed Omarchy shell and Quickshell. Those
programs are prerequisites and are not included in this archive. The stable
plugin identifier is retained for compatibility with existing installations.

This is an independent integration, not an official Valve, Steam, Statlocker
or Deadlock API client release.
