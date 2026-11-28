# installer/install_port_watchdog.ps1 -- installs or removes the scheduled task that runs
# installer/port_watchdog.py once a minute (docs/runbook.md, "Port watchdog").
#
# Run from an elevated PowerShell:
#   .\install_port_watchdog.ps1               install (or refresh) the task
#   .\install_port_watchdog.ps1 -DryRun       install it so it only logs what it would do
#   .\install_port_watchdog.ps1 -Remove       remove the task and its folder
#
# The script is copied into the admin-only services folder and the task runs that copy as
# SYSTEM, so nothing a normal user can edit ever runs with those rights.

param([switch]$Remove, [switch]$DryRun, [string]$DataDir)

$ErrorActionPreference = "Stop"
$TaskName = "Baihe Studio port watchdog"
$Admin = Join-Path ([Environment]::GetFolderPath("ProgramFiles")) "Baihe Studio Services"
$Folder = Join-Path $Admin "watchdog"
$Python = Join-Path $Admin "helper\python\python.exe"
$Config = Join-Path $Admin "helper\config.json"

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this from an elevated (Administrator) PowerShell."
}

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path $Folder) { Remove-Item $Folder -Recurse -Force }
    Write-Host "Removed the task and $Folder."
    return
}

if (-not (Test-Path $Python)) { throw "Not found: $Python. Install Baihe Studio's service first." }
if (-not $DataDir) {
    if (-not (Test-Path $Config)) { throw "Not found: $Config. Pass -DataDir." }
    $DataDir = (Get-Content $Config -Raw | ConvertFrom-Json).data_dir
}
if (-not (Test-Path (Join-Path $DataDir "library"))) { throw "No library folder in $DataDir; pass -DataDir." }

New-Item -ItemType Directory -Force $Folder | Out-Null
Copy-Item (Join-Path $PSScriptRoot "port_watchdog.py") (Join-Path $Folder "port_watchdog.py") -Force

$argLine = "-I `"$(Join-Path $Folder 'port_watchdog.py')`" --data-dir `"$DataDir`" --state-dir `"$Folder`""
if ($DryRun) { $argLine += " --dry-run" }
$action = New-ScheduledTaskAction -Execute $Python -Argument $argLine
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Installed '$TaskName' (every minute, as SYSTEM). Log: $(Join-Path $Folder 'watchdog.log')"
if ($DryRun) { Write-Host "Dry run: it only logs. Install again without -DryRun to let it restart the service." }
