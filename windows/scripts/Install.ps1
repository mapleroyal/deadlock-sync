[CmdletBinding()]
param(
    [string]$PayloadPath = (Join-Path $PSScriptRoot 'app'),
    [string]$ManifestPath = (Join-Path $PSScriptRoot 'upstream.json'),
    [switch]$DoNotStart,
    [switch]$NoAutoStart,
    [switch]$EnableSync,
    [long]$RecoveryNotBefore = 0
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not [Environment]::Is64BitOperatingSystem) { throw 'Deadlock Sync requires 64-bit Windows.' }
if (-not (Test-Path (Join-Path $PayloadPath 'DeadlockSync.exe'))) { throw 'Extract the entire release ZIP before running Install.ps1.' }
$sessionId = [Diagnostics.Process]::GetCurrentProcess().SessionId
$installedExe = Join-Path $env:LOCALAPPDATA 'DeadlockSync\app\DeadlockSync.exe'
$ourControllers = @(Get-Process -Name DeadlockSync -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $installedExe } | Select-Object -ExpandProperty Id)
$otherIngest = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $sessionId -and $_.ProcessName -like 'deadlock-api-ingest*' } | Where-Object {
    $entry = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id) -ErrorAction SilentlyContinue
    -not $entry -or $entry.ParentProcessId -notin $ourControllers
})
if ($otherIngest.Count -gt 0) { throw 'Another Deadlock API Ingest process is running. Close its own GUI or stop its own startup entry before installing. No unrelated processes were stopped.' }
$oldRun = Get-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue
if ($oldRun) {
    foreach ($name in $oldRun.GetValueNames()) {
        if ([string]$oldRun.GetValue($name) -match 'deadlock-api-ingest') { throw 'An upstream ingest Run entry is enabled. Disable that entry before installing Deadlock Sync. It has been preserved.' }
    }
}
$oldTask = Get-ScheduledTask -TaskName 'deadlock-api-ingest' -ErrorAction SilentlyContinue
if ($oldTask -and $oldTask.State -ne 'Disabled') { throw 'The upstream deadlock-api-ingest scheduled task is enabled. Disable it before installing Deadlock Sync. It has been preserved.' }
$pin = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($pin.version -ne 'v0.4.8' -or $pin.asset.sha256 -ne 'd0e2a772ffc075713cc21759cb0a3b9e378a3235e59fc25cc7ed5b5d1f884464') {
    throw 'The release manifest does not match this app build.'
}
$root = Join-Path $env:LOCALAPPDATA 'DeadlockSync'
$app = Join-Path $root 'app'
$exe = Join-Path $app 'DeadlockSync.exe'
$stateDir = Join-Path $root 'state'
$stage = Join-Path $root ('install-staging-' + [Guid]::NewGuid().ToString('N'))
$backup = Join-Path $root 'app.previous'
New-Item -ItemType Directory -Path $root,$stateDir,$stage -Force | Out-Null
try {
    Copy-Item -Path (Join-Path $PayloadPath '*') -Destination $stage -Recurse -Force
    $ingestDir = Join-Path $stage 'ingest'
    New-Item -ItemType Directory -Path $ingestDir -Force | Out-Null
    $ingest = Join-Path $ingestDir 'deadlock-api-ingest.exe'
    $currentIngest = Join-Path $app 'ingest\deadlock-api-ingest.exe'
    if (-not (Test-Path $ingest)) {
        if ((Test-Path $currentIngest) -and ((Get-FileHash -LiteralPath $currentIngest -Algorithm SHA256).Hash.ToLowerInvariant() -eq $pin.asset.sha256)) {
            Copy-Item -LiteralPath $currentIngest -Destination $ingest
        } else {
            Invoke-WebRequest -Uri $pin.asset.url -OutFile $ingest -UseBasicParsing
        }
    }
    if ((Get-Item -LiteralPath $ingest).Length -ne $pin.asset.size -or (Get-FileHash -LiteralPath $ingest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pin.asset.sha256) {
        throw 'The official ingest download failed SHA-256 verification. Installation stopped.'
    }
    if (Test-Path $exe) {
        Start-Process -FilePath $exe -ArgumentList '--exit' -Wait
        $deadline = [DateTime]::UtcNow.AddSeconds(12)
        do {
            $owned = @(Get-Process -Name DeadlockSync -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe })
            if ($owned.Count -eq 0) { break }
            Start-Sleep -Milliseconds 200
        } while ([DateTime]::UtcNow -lt $deadline)
        if ($owned.Count -gt 0) { throw 'Close this installation of Deadlock Sync before upgrading.' }
    }
    if (Test-Path $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
    if (Test-Path $app) { Move-Item -LiteralPath $app -Destination $backup }
    try { Move-Item -LiteralPath $stage -Destination $app }
    catch { if (Test-Path $backup) { Move-Item -LiteralPath $backup -Destination $app }; throw }
    if (Test-Path $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Uninstall.ps1') -Destination (Join-Path $root 'Uninstall.ps1') -Force
    if ($RecoveryNotBefore -gt 0) {
        if ($RecoveryNotBefore -gt 4102444800) { throw 'RecoveryNotBefore is outside the supported UTC Unix time range.' }
        $stateFile = Join-Path $stateDir 'recovery.json'
        if (Test-Path $stateFile) {
            $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
            if ($state.Version -ne 1 -or $state.Corrupt) { throw 'Existing recovery state needs repair; it has not been reset.' }
        } else {
            $state = [pscustomobject]@{ Version=1; Enabled=$false; Pending=$true; NextAtUtc=0L; HardUntilUtc=0L; UploadHardUntilUtc=0L; Reason=''; Failures=0;
                LastGameIdentity=''; SuppressedGameIdentity=''; ProcessedRunId='';
                Progress=[pscustomobject]@{ Mode=''; Phase='preparing'; Completed=$null; Total=$null; Uploaded=$null; Failed=$null; History=$null; UpdatedAtUtc=$null };
                Skipped=0; Corrupt=$false; LastNoticeKey='' }
        }
        $state.HardUntilUtc = [Math]::Max([long]$state.HardUntilUtc, $RecoveryNotBefore)
        $state.NextAtUtc = [Math]::Max([long]$state.NextAtUtc, [long]$state.HardUntilUtc)
        $state.Pending = $true
        if ($state.Reason -notin @('auth','state_store','unavailable')) { $state.Reason = 'rate_limit' }
        $temporary = $stateFile + '.install.tmp'
        [IO.File]::WriteAllText($temporary, ($state | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $stateFile -Force
    }
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    if (-not $NoAutoStart) {
        New-Item -Path $runKey -Force | Out-Null
        New-ItemProperty -Path $runKey -Name 'Deadlock Sync' -Value ('"' + $exe + '" --background') -PropertyType String -Force | Out-Null
    } else {
        $existingItem = Get-ItemProperty -Path $runKey -Name 'Deadlock Sync' -ErrorAction SilentlyContinue
        $existing = if ($existingItem) { $existingItem.'Deadlock Sync' } else { '' }
        if ($existing -eq ('"' + $exe + '" --background')) { Remove-ItemProperty -Path $runKey -Name 'Deadlock Sync' }
    }
    $startMenu = [Environment]::GetFolderPath('Programs')
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path $startMenu 'Deadlock Sync.lnk'))
    $link.TargetPath = $exe; $link.Arguments = '--enable'; $link.WorkingDirectory = $app
    $link.Description = 'Live Deadlock sync and automatic own-history recovery'; $link.Save()
    if (-not $DoNotStart) { Start-Process -FilePath $exe -ArgumentList $(if ($EnableSync) { '--enable' } else { '--background' }) }
    Write-Output "Installed Deadlock Sync to $app"
    Write-Output 'Start Menu: Deadlock Sync. Click its tray icon for status; right-click for Quit syncing or Exit app.'
    Write-Output 'Own-history recovery uses all remembered Steam accounts available to this Windows user.'
} finally {
    if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
}
