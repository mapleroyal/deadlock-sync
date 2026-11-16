[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'DeadlockSync'
$app = Join-Path $root 'app'
$exe = Join-Path $app 'DeadlockSync.exe'
if (Test-Path $exe) {
    Start-Process -FilePath $exe -ArgumentList '--exit' -Wait
    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    do {
        $owned = @(Get-Process -Name DeadlockSync -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe })
        if ($owned.Count -eq 0) { break }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($owned.Count -gt 0) { throw 'Close this installation of Deadlock Sync before uninstalling.' }
}
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$value = (Get-ItemProperty -Path $key -Name 'Deadlock Sync' -ErrorAction SilentlyContinue).'Deadlock Sync'
if ($value -eq ('"' + $exe + '" --background')) { Remove-ItemProperty -Path $key -Name 'Deadlock Sync' }
$link = Join-Path ([Environment]::GetFolderPath('Programs')) 'Deadlock Sync.lnk'
if (Test-Path $link) {
    $shell = New-Object -ComObject WScript.Shell
    if ($shell.CreateShortcut($link).TargetPath -eq $exe) { Remove-Item -LiteralPath $link }
}
if (Test-Path $app) { Remove-Item -LiteralPath $app -Recurse -Force }
Write-Output 'Deadlock Sync was removed. Recovery cooldowns and upstream Steam attempt records were preserved.'
Write-Output "Local state remains at $root\state. Steam data and %APPDATA%\deadlock-api-ingest were not removed."
