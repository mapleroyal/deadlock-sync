#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
dotnet_command="${DEADLOCK_DOTNET:-dotnet}"
publish_directory="${1:-$project_root/publish/windows-x64}"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
"$dotnet_command" run --project "$project_root/windows/DeadlockSync.Tests" --configuration Release
"$dotnet_command" publish "$project_root/windows/DeadlockSync" --configuration Release \
  --runtime win-x64 --self-contained true --output "$publish_directory" \
  -p:PublishSingleFile=true -p:PublishTrimmed=false -p:IncludeNativeLibrariesForSelfExtract=true
