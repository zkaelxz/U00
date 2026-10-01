# installer/service_menu.ps1 -- the Start-menu item "Baihe Studio service"
# (docs/windows-installer-design.md, "Boot service"): a console menu over
# service.py's commands, so nobody has to type the helper's long path.
#
# It ships in the admin-only folder
# (%ProgramFiles%\Baihe Studio Services\helper\lib\installer) and the
# shortcut runs that copy. It asks for administrator rights once and runs
# elevated only from there, with the admin folder's own python: never a file
# in the per-user program folder. Elevated command lines hold only fixed
# words and checked whole numbers.
#
#   service_menu.ps1          the menu (asks for administrator rights)
#   service_menu.ps1 -Status  print `service.py status` and exit (no rights
#                             needed, no prompts)

param([switch]$Status)

$Admin = Join-Path ([Environment]::GetFolderPath("ProgramFiles")) "Baihe Studio Services"
$Python = Join-Path $Admin "helper\python\python.exe"
$Service = Join-Path $Admin "helper\lib\installer\service.py"
$Self = Join-Path $Admin "helper\lib\installer\service_menu.ps1"
$PowerShell = Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\powershell.exe"
$DefaultHouseholdPort = 8610

function Wait-ToClose {
    [void](Read-Host "Press Enter to close")
}

# A port number from 1024 to 65535 written with ASCII digits only, or $null.
function Get-CheckedPort([string]$Text) {
    if ($Text -cmatch '\A[0-9]{1,5}\z') {
        $n = [int]$Text
        if ($n -ge 1024 -and $n -le 65535) { return $n }
    }
    return $null
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# Runs service.py with fixed words and checked numbers only, shows what it
# printed and what its exit code means.
function Invoke-Service([string[]]$Arguments) {
    & $Python -I -S $Service @Arguments | Out-Host
    $code = $LASTEXITCODE
    switch ($code) {
        0 { }
        2 { Write-Host "Nothing was changed." }
        3 { Write-Host "This needs administrator rights; nothing was changed." }
        default { Write-Host "It didn't work (exit code $code). Details are in $Admin\service.log." }
    }
}

function Set-ServicePort {
    Write-Host "WARNING: changing or deleting the BAIHE_API_PORT environment variable does NOT change"
    Write-Host "the service's port. Once the service is installed its stored port wins (the Start-menu"
    Write-Host "launcher follows it too); this is the way to change it."
    $answer = "$(Read-Host 'New port for Baihe Studio (1024 to 65535; just Enter to cancel)')".Trim()
    if ($answer -eq "") { Write-Host "Nothing was changed."; return }
    $n = Get-CheckedPort $answer
    if ($null -eq $n) { Write-Host "Not a port number from 1024 to 65535. Nothing was changed."; return }
    Write-Host "Moving Baihe Studio's service to port $n (this can take a minute)..."
    Invoke-Service @("set-port", [string]$n)
}

function Enable-Remote {
    Write-Host "Remote access lets your household's devices reach Baihe Studio over HTTPS, through"
    Write-Host "Caddy. Before you turn it on (docs/household-access.md):"
    Write-Host "  - the data folder's .env sets BAIHE_GOOGLE_CLIENT_ID, BAIHE_GOOGLE_CLIENT_SECRET and"
    Write-Host "    BAIHE_PUBLIC_URL=https://your-domain (just https:// and the name, no port or path);"
    Write-Host "  - your domain name points to your home, and port 443 on your router is forwarded to"
    Write-Host "    this PC: both are your own steps;"
    Write-Host "  - the Windows Firewall rule is yours to add: the command is printed below once it's on."
    Write-Host "If a setting is missing or the port can't be used, nothing is changed."
    $answer = "$(Read-Host "Household port (1024 to 65535; just Enter for $DefaultHouseholdPort)")".Trim()
    $n = if ($answer -eq "") { $DefaultHouseholdPort } else { Get-CheckedPort $answer }
    if ($null -eq $n) { Write-Host "Not a port number from 1024 to 65535. Nothing was changed."; return }
    Write-Host "Turning remote access on (this can take a minute)..."
    Invoke-Service @("enable-remote", "--household-port", [string]$n)
}

function Open-LogFolder {
    $folder = $null
    try {
        $config = Get-Content -LiteralPath (Join-Path $Admin "helper\config.json") -Raw | ConvertFrom-Json
        if ($config.data_dir) { $folder = Join-Path $config.data_dir "library\logs\service" }
    } catch { }
    Write-Host "Setup's and this menu's service steps log to $Admin\service.log."
    if ($folder -and (Test-Path -LiteralPath $folder)) {
        Write-Host "Opening Baihe Studio's own service log folder: $folder"
        Start-Process -FilePath (Join-Path ([Environment]::GetFolderPath("Windows")) "explorer.exe") `
            -ArgumentList ('"' + $folder + '"')
    } else {
        Write-Host "Baihe Studio's own service log folder isn't there yet."
    }
}

if (-not ((Test-Path -LiteralPath $Python) -and (Test-Path -LiteralPath $Service))) {
    Write-Host "Baihe Studio's background service isn't installed. The Start-menu launcher uses"
    Write-Host "BAIHE_API_PORT, or the default port when it isn't set."
    if (-not $Status) { Wait-ToClose }
    exit 2
}

if ($Status) {
    & $Python -I -S $Service status | Out-Host
    exit $LASTEXITCODE
}

if (-not (Test-Admin)) {
    Write-Host "Baihe Studio's service menu needs administrator rights. Allow the prompt; the menu"
    Write-Host "opens in a new window."
    try {
        Start-Process -FilePath $PowerShell -Verb RunAs -ArgumentList (
            '-NoProfile -ExecutionPolicy Bypass -File "' + $Self + '"') -ErrorAction Stop
        exit 0
    } catch {
        Write-Host "The administrator prompt was declined. The status, read-only:"
        & $Python -I -S $Service status | Out-Host
        Wait-ToClose
        exit 1
    }
}

# Elevated: only the admin folder's copy runs the menu.
if (-not [string]::Equals($PSCommandPath, $Self, [StringComparison]::OrdinalIgnoreCase)) {
    Write-Host "Use the Start-menu item ""Baihe Studio service"" (it runs $Self)."
    Wait-ToClose
    exit 1
}

while ($true) {
    Write-Host ""
    Write-Host "Baihe Studio service"
    Write-Host "  1  Show status (services, ports, remote access, firewall rule)"
    Write-Host "  2  Change Baihe Studio's port"
    Write-Host "  3  Turn remote access on"
    Write-Host "  4  Turn remote access off"
    Write-Host "  5  Open the service log folder"
    Write-Host "  0  Quit"
    $choice = "$(Read-Host 'Choose')".Trim()
    Write-Host ""
    switch ($choice) {
        "1" { Invoke-Service @("status") }
        "2" { Set-ServicePort }
        "3" { Enable-Remote }
        "4" { Write-Host "Turning remote access off..."; Invoke-Service @("disable-remote") }
        "5" { Open-LogFolder }
        "0" { exit 0 }
        default { Write-Host "Type a number from 0 to 5." }
    }
}
