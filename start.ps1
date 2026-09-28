# start.ps1 -- Step 10's launcher, PowerShell version (optional; start.bat
# is the primary one, since double-clicking a .ps1 often hits Windows'
# own script-execution policy ("running scripts is disabled on this
# system") for someone who hasn't already allowed it -- .bat has no such
# friction. Use this if you'd rather run it from a PowerShell prompt you
# already have open, or already allow local scripts to run.
#
#   .\start.ps1              # normal launch
#   .\start.ps1 -Portable    # also turns on portable mode for this run
#   .\start.ps1 -PythonVersion 3.12  # Step 79: pin the Python version
#                            used to create the venv, via the `py`
#                            launcher, for an optional dependency that
#                            needs a specific version. A PYTHON_VERSION
#                            marker file next to this script (containing
#                            just "3.12") does the same thing without the
#                            flag -- same pattern as the PORTABLE marker.

param(
    [switch]$Portable,
    [string]$PythonVersion
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if ($Portable) { $env:BAIHE_PORTABLE = "1" }

if (-not $PythonVersion) {
    $markerPath = Join-Path $PSScriptRoot "PYTHON_VERSION"
    if (Test-Path $markerPath) {
        $PythonVersion = (Get-Content $markerPath -Raw).Trim()
    }
}

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

# `Get-Command python` only confirms a file named python.exe exists
# somewhere on PATH -- Windows ships a non-functional stub at
# ...\WindowsApps\python.exe (the Microsoft Store "App execution alias")
# that satisfies that check even with no real Python installed, or with
# a real install shadowed by the stub earlier on PATH. Actually running
# it and checking its exit code is the only way to tell a real
# interpreter from the stub, which prints the Store message and exits
# non-zero.
if ($PythonVersion) {
    $PyCmd = "py"
    $PyArgs = @("-$PythonVersion")
} else {
    $PyCmd = "python"
    $PyArgs = @()
}

$pyVersionCheck = & $PyCmd @PyArgs --version 2>$null
if ($LASTEXITCODE -ne 0) {
    if ($PythonVersion) {
        Write-Host "Python $PythonVersion wasn't found via the 'py' launcher."
        Write-Host ""
        Write-Host "Install Python $PythonVersion from https://python.org/downloads/"
        Write-Host "(the 'py' launcher is installed automatically with it), then"
        Write-Host "run this again."
    } elseif (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "Python wasn't found on PATH."
        Write-Host ""
        Write-Host "Install Python 3.9 or newer from https://python.org/downloads/"
        Write-Host "and make sure to tick 'Add python.exe to PATH' during setup,"
        Write-Host "then run this again."
    } else {
        Write-Host "Python is on PATH but isn't runnable -- this is the"
        Write-Host "Microsoft Store's non-functional 'App execution aliases' stub,"
        Write-Host "not a real Python install."
        Write-Host ""
        Write-Host "Fix: Settings -> Apps -> Advanced app settings -> App execution aliases"
        Write-Host "-> turn OFF both 'App Installer python.exe' and 'App Installer"
        Write-Host "python3.exe', then run this again. If Python genuinely isn't"
        Write-Host "installed, install it from https://python.org/downloads/ first."
    }
    Read-Host "Press Enter to close"
    exit 1
}

if (-not (Test-Path $Py)) {
    Write-Host "Setting up a virtual environment in '$VenvDir' (first run only)..."
    & $PyCmd @PyArgs -m venv $VenvDir
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
