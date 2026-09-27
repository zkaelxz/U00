# start.ps1 -- Step 10's launcher, PowerShell version (optional; start.bat
# is the primary one, since double-clicking a .ps1 often hits Windows'
# own script-execution policy ("running scripts is disabled on this
# system") for someone who hasn't already allowed it -- .bat has no such
# friction. Use this if you'd rather run it from a PowerShell prompt you
# already have open, or already allow local scripts to run.
#
#   .\start.ps1              # normal launch
#   .\start.ps1 -Portable    # also turns on portable mode for this run

param(
    [switch]$Portable
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if ($Portable) { $env:BAIHE_PORTABLE = "1" }

$Port = 8501
$VenvDir = Join-Path $PSScriptRoot "venv"
$Py = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "============================================"
Write-Host "  Baihe Subtitler"
Write-Host "============================================"
Write-Host ""

function Test-PortOpen {
    param([int]$PortNumber)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $result = $client.BeginConnect("127.0.0.1", $PortNumber, $null, $null)
        $ok = $result.AsyncWaitHandle.WaitOne(500)
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python wasn't found on PATH."
    Write-Host ""
    Write-Host "Install Python 3.9 or newer from https://python.org/downloads/"
    Write-Host "and make sure to tick 'Add python.exe to PATH' during setup,"
    Write-Host "then run this again."
    Read-Host "Press Enter to close"
    exit 1
}

if (-not (Test-Path $Py)) {
    Write-Host "Setting up a virtual environment in '$VenvDir' (first run only)..."
    python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Could not create the virtual environment. See the error above."
        Read-Host "Press Enter to close"
        exit 1
    }
}

# Checks every package requirements-core.txt actually installs, not
# just streamlit -- a stale or partially-installed venv where
# streamlit still imports fine but something else is missing used to
# make this skip the install step entirely and fail later with a much
# less clear error (Step 53, applied here in Step 63). Keep this import
# list in sync with requirements-core.txt's own packages.
& $Py -c "import streamlit, pandas, requests, bs4, anthropic" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dependencies -- this can take a few minutes the first time..."
    $constraints = if (Test-Path "constraints.lock.txt") { "constraints.lock.txt" } else { "constraints.txt" }
    & $Py -m pip install -r requirements-core.txt -c $constraints
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Installing dependencies failed -- see the error above."
        Write-Host "A common fix: $Py -m pip install --upgrade pip"
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host ""
}

& $Py check_setup.py
Write-Host ""

if (Test-PortOpen -PortNumber $Port) {
    Write-Host "Baihe Subtitler is already running on port $Port -- opening a window on it."
} else {
    Write-Host "Starting Baihe Subtitler on port $Port ..."
    Start-Process -FilePath $Py `
        -ArgumentList "-m", "streamlit", "run", "app.py", "--server.headless", "true", "--server.port", "$Port" `
        -WindowStyle Minimized

    $tries = 0
    while (-not (Test-PortOpen -PortNumber $Port)) {
        Start-Sleep -Seconds 1
        $tries++
        if ($tries -ge 60) {
            Write-Host ""
            Write-Host "The server didn't answer after 30 seconds -- something may have gone wrong."
            Read-Host "Press Enter to close"
            exit 1
        }
    }
}

$AppUrl = "http://localhost:$Port"
$EdgeCandidates = @(
    "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:LocalAppData\Microsoft\Edge\Application\msedge.exe"
)
$ChromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
)

$edge = $EdgeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge -and (Get-Command msedge -ErrorAction SilentlyContinue)) { $edge = "msedge" }

$chrome = $ChromeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome -and (Get-Command chrome -ErrorAction SilentlyContinue)) { $chrome = "chrome" }

if ($edge) {
    Start-Process -FilePath $edge -ArgumentList "--app=$AppUrl"
} elseif ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList "--app=$AppUrl"
} else {
    Write-Host "Neither Edge nor Chrome was found in the usual places -- opening your"
    Write-Host "default browser instead (it won't look like its own window there)."
    Start-Process $AppUrl
}
