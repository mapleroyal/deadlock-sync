# Third party components

Deadlock API Ingest is a separate upstream program. Installers fetch its pinned
official release and verify its SHA-256 before use. Its source, license and
release notices are maintained at
https://github.com/deadlock-api/deadlock-api-ingest.

Self-contained Windows builds include Microsoft's .NET runtime and Windows
Forms libraries, distributed under their own licenses. Their notices are
included in the Windows archive as `DOTNET-LICENSE.txt` and
`DOTNET-THIRD-PARTY-NOTICES.txt`.

The Linux UI uses the target's installed Omarchy shell and Quickshell. Those
programs are prerequisites and are not included in this archive. The stable
plugin identifier is retained for compatibility with existing installations.

This is an independent integration, not an official Valve, Steam, Statlocker
or Deadlock API client release.
