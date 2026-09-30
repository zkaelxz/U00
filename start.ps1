# start.ps1 -- Step 10's launcher, PowerShell version (optional; start.bat
# is the primary one, since double-clicking a .ps1 often hits Windows'
# own script-execution policy ("running scripts is disabled on this
# system") for someone who hasn't already allowed it -- .bat has no such
# friction. Use this if you'd rather run it from a PowerShell prompt you
# already have open, or already allow local scripts to run.
#
# Same flow as start.bat: venv, dependencies, check_setup.py, then the
# app server (`python -m api`, loopback 127.0.0.1:8600 only -- no login
# yet, see docs/remote-access-decision.md) and its own window once
# /api/health answers. Needs the prebuilt frontend in frontend\dist
# (docs/RELEASE.md).
#
#   .\start.ps1              # normal launch
#   .\start.ps1 -Portable    # also turns on portable mode for this run
#   .\start.ps1 -BuildFrontend  # developers: build frontend\dist with npm
#                            if it's missing (needs Node.js)
#   .\start.ps1 -PythonVersion 3.12  # Step 79: pin the Python version
#                            used to create the venv, via the `py`
#                            launcher, for an optional dependency that
#                            needs a specific version. A PYTHON_VERSION
#                            marker file next to this script (containing
#                            just "3.12") does the same thing without the
#                            flag -- same pattern as the PORTABLE marker.

param(
    [switch]$Portable,
    [switch]$BuildFrontend,
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

# Loopback only until the API has authentication -- never 0.0.0.0.
$env:BAIHE_API_HOST = "127.0.0.1"
# Turn on the PC-only API-key form in Settings (decided 2026-09-29).
# Key writes are still refused unless the request comes from this PC
# itself (loopback peer and Host, no proxy headers, and Origin, if sent,
# is loopback;
# api/routers/settings_routes.py:_require_local_admin). Keys go to .env
# and their values are never returned. Set BAIHE_API_ALLOW_KEY_WRITES=0
# before running this script to opt out.
if (-not $env:BAIHE_API_ALLOW_KEY_WRITES) { $env:BAIHE_API_ALLOW_KEY_WRITES = "1" }
if (-not $env:BAIHE_API_PORT) { $env:BAIHE_API_PORT = "8600" }
$Port = [int]$env:BAIHE_API_PORT
$AppUrl = "http://127.0.0.1:$Port/"
$VenvDir = Join-Path $PSScriptRoot "venv"
$Py = Join-Path $VenvDir "Scripts\python.exe"

Write-Host "============================================"
Write-Host "  Baihe Studio"
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

function Test-ApiHealth {
    try {
        $r = Invoke-WebRequest -Uri "${AppUrl}api/health" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
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
        Write-Host "Install Python 3.10 or newer from https://python.org/downloads/"
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
# just one of them -- a stale or partially-installed venv where one
# package still imports fine but something else is missing used to
# make this skip the install step entirely and fail later with a much
# less clear error (Step 53, applied here in Step 63). Keep this import
# list in sync with requirements-core.txt's own packages.
& $Py -c "import requests, urllib3, bs4, anthropic, fastapi, multipart, uvicorn; assert tuple(int(x) for x in urllib3.__version__.split('.')[:2]) >= (2, 6)" 2>$null
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

# The frontend ships prebuilt (docs/RELEASE.md); npm only runs with -BuildFrontend.
if (-not (Test-Path "frontend\dist\index.html")) {
    if ($BuildFrontend) {
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            Write-Host "-BuildFrontend needs Node.js and npm, and they weren't found on PATH."
            Write-Host "Install Node.js 22 from https://nodejs.org/ or use the prebuilt release zip (docs\RELEASE.md)."
            Read-Host "Press Enter to close"
            exit 1
        }
        Write-Host "Building the React app (npm ci, npm run build) -- a few minutes the first time..."
        Push-Location frontend
        & npm ci
        if ($LASTEXITCODE -eq 0) { & npm run build }
        $buildExit = $LASTEXITCODE
        Pop-Location
        if ($buildExit -ne 0 -or -not (Test-Path "frontend\dist\index.html")) {
            Write-Host ""
            Write-Host "Building the React app failed -- see the npm error above."
            Read-Host "Press Enter to close"
            exit 1
        }
    } else {
        Write-Host "The app's screens (frontend\dist) aren't installed yet."
        Write-Host ""
        Write-Host "Either:"
        Write-Host "  1. Download baihe-frontend-<version>.zip from the project's GitHub"
        Write-Host "     Releases page and unzip it into this folder, so that this file exists:"
        Write-Host "       $PSScriptRoot\frontend\dist\index.html"
        Write-Host "  or"
        Write-Host "  2. (developers, needs Node.js 22) build it here:  .\start.ps1 -BuildFrontend"
        Write-Host ""
        Write-Host "Then run this again. See docs\RELEASE.md for details."
        Read-Host "Press Enter to close"
        exit 1
    }
}

if (Test-ApiHealth) {
    Write-Host "Baihe Studio is already running at $AppUrl -- opening a window on it."
} elseif (Test-PortOpen -PortNumber $Port) {
    Write-Host "Something else is already using port $Port, and it isn't Baihe Studio"
    Write-Host "(${AppUrl}api/health doesn't answer). Close that program, or choose"
    Write-Host "another port first, e.g.:  `$env:BAIHE_API_PORT = 8601"
    Read-Host "Press Enter to close"
    exit 1
} else {
    Write-Host "Starting Baihe Studio at $AppUrl ..."
    Start-Process -FilePath $Py -ArgumentList "-m", "api" -WindowStyle Minimized

    $tries = 0
    while (-not (Test-ApiHealth)) {
        Start-Sleep -Seconds 1
        $tries++
        if ($tries -ge 60) {
            Write-Host ""
            Write-Host "The server didn't answer at ${AppUrl}api/health after about a minute --"
            Write-Host "something went wrong while starting it. Check the minimized server window."
            Read-Host "Press Enter to close"
            exit 1
        }
    }
}

$EdgeCandidates = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:LocalAppData\Microsoft\Edge\Application\msedge.exe"
)
$ChromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
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
