@echo off
setlocal enabledelayedexpansion
REM start.bat -- the one-command Windows launcher (Step 10; M0-b).
REM
REM Double-click this (or the desktop shortcut make_shortcut.bat creates)
REM to set up and run Baihe Studio with no typed commands: it creates the
REM venv, installs dependencies, runs check_setup.py, starts the app
REM server (`python -m api`) on http://127.0.0.1:8600/ and opens it in its
REM own window once /api/health answers. The server also serves the
REM prebuilt React app (frontend\dist), so no Node.js is needed to run it.
REM Safe to run more than once: if the app is already running, this just
REM opens a window pointed at it instead of starting a second copy.
REM
REM Loopback only (127.0.0.1) on purpose: the API has no login yet, so it
REM must not be reachable from other devices (docs/remote-access-decision.md).
REM This script forces BAIHE_API_HOST=127.0.0.1 even if it is set elsewhere.
REM
REM   start.bat            -- normal launch
REM   start.bat --portable -- also turns on portable mode for this run
REM                            (see portable.py; a PORTABLE marker file
REM                            next to this script does the same thing
REM                            without needing the flag every time)
REM   start.bat --ci       -- Step 10b: non-interactive. Runs the exact
REM                            same bootstrap (venv, deps, dependency
REM                            check, wait for /api/health to answer), but
REM                            skips opening a browser window and never
REM                            calls `pause` -- so it can run unattended
REM                            on a CI runner. Exits 0 once /api/health
REM                            answers (the server keeps running), non-zero
REM                            if it never does. A missing frontend\dist is
REM                            only a warning here (API-only). Same effect
REM                            as setting the BAIHE_CI environment variable.
REM   start.bat --server-only -- same browser-skip/no-pause behavior as
REM                            --ci, for running the server on its own and
REM                            opening http://127.0.0.1:8600/ yourself.
REM                            Still loopback only (no LAN access until the
REM                            API has authentication). Same effect as
REM                            setting BAIHE_SERVER_ONLY.
REM   start.bat --build-frontend -- developers only: if frontend\dist is
REM                            missing, build it (npm ci, npm run build in
REM                            frontend\). Needs Node.js. End users unzip
REM                            the prebuilt release zip instead (docs/RELEASE.md).
REM   start.bat --python-version 3.12 -- Step 79: pin the Python version
REM                            used to create the venv, via the `py`
REM                            launcher (`py -3.12`), for an optional
REM                            dependency that needs a specific version
REM                            (e.g. qwen-asr recommends a clean 3.12 env).
REM                            A PYTHON_VERSION marker file next to this
REM                            script (containing just "3.12") does the
REM                            same thing without needing the flag every
REM                            time -- same pattern as the PORTABLE marker.
REM                            Falls back to plain `python` when neither
REM                            is set, so nothing changes by default.

cd /d "%~dp0"

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--portable" set BAIHE_PORTABLE=1
if /i "%~1"=="--ci" set BAIHE_CI=1
if /i "%~1"=="--server-only" set BAIHE_SERVER_ONLY=1
if /i "%~1"=="--build-frontend" set BAIHE_BUILD_FRONTEND=1
if /i "%~1"=="--python-version" (
    set PYTHON_VERSION=%~2
    shift
)
shift
goto :parse_args
:args_done

if not defined PYTHON_VERSION if exist PYTHON_VERSION (
    set /p PYTHON_VERSION=<PYTHON_VERSION
)

REM Loopback only until the API has authentication -- never 0.0.0.0.
set BAIHE_API_HOST=127.0.0.1
REM Turn on the PC-only API-key form in Settings (decided 2026-09-29).
REM Key writes are still refused unless the request comes from this PC
REM itself (loopback peer and Host, no proxy headers, and Origin, if sent,
REM is loopback;
REM api/routers/settings_routes.py:_require_local_admin). Keys go to .env
REM and their values are never returned. Set BAIHE_API_ALLOW_KEY_WRITES=0
REM before running this script to opt out.
if not defined BAIHE_API_ALLOW_KEY_WRITES set BAIHE_API_ALLOW_KEY_WRITES=1
if not defined BAIHE_API_PORT set BAIHE_API_PORT=8600
set PORT=%BAIHE_API_PORT%
set "APP_URL=http://127.0.0.1:%PORT%/"
set VENV_DIR=venv
set PY="%VENV_DIR%\Scripts\python.exe"

echo ============================================
echo   Baihe Studio
echo ============================================
echo.

REM --- Python check -----------------------------------------------------
REM `where python` only confirms a file named python.exe exists somewhere
REM on PATH -- Windows ships a non-functional stub at
REM ...\WindowsApps\python.exe (the Microsoft Store "App execution
REM alias") that satisfies that check even with no real Python installed,
REM or with a real install shadowed by the stub earlier on PATH. Actually
REM running it and checking its exit code/output is the only way to tell
REM a real interpreter from the stub, which prints the Store message and
REM exits non-zero.
if defined PYTHON_VERSION (
    set PYCMD=py -%PYTHON_VERSION%
) else (
    set PYCMD=python
)
%PYCMD% --version >nul 2>nul
if errorlevel 1 (
    if defined PYTHON_VERSION (
        echo Python %PYTHON_VERSION% wasn't found via the "py" launcher.
        echo.
        echo Install Python %PYTHON_VERSION% from https://python.org/downloads/
        echo ^(the "py" launcher is installed automatically with it^), then run
        echo this again.
    ) else (
        where python >nul 2>nul
        if errorlevel 1 (
            echo Python wasn't found on PATH.
            echo.
            echo Install Python 3.9 or newer from https://python.org/downloads/
            echo and make sure to tick "Add python.exe to PATH" during setup,
            echo then run this again.
        ) else (
            echo Python is on PATH but isn't runnable -- this is the
            echo Microsoft Store's non-functional "App execution aliases" stub,
            echo not a real Python install.
            echo.
            echo Fix: Settings -^> Apps -^> Advanced app settings -^> App execution aliases
            echo -^> turn OFF both "App Installer python.exe" and "App Installer python3.exe",
            echo then run this again. If Python genuinely isn't installed, install
            echo it from https://python.org/downloads/ first.
        )
    )
    if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
    exit /b 1
)

REM --- Virtual environment ------------------------------------------------
if not exist %PY% (
    echo Setting up a virtual environment in "%VENV_DIR%\" ^(first run only^)...
    %PYCMD% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo Could not create the virtual environment. See the error above.
        if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
        exit /b 1
    )
)

REM --- Dependencies --------------------------------------------------------
REM Checks every package requirements-core.txt actually installs, not
REM just one of them -- a stale or partially-installed venv where one
REM package still imports fine but something else is missing used to
REM make this skip the install step entirely and fail later with a much
REM less clear error (Step 53). Keep this import list in sync with
REM requirements-core.txt's own packages.
%PY% -c "import requests, urllib3, bs4, anthropic, fastapi, multipart, uvicorn, numpy, PIL; assert tuple(int(x) for x in urllib3.__version__.split('.')[:2]) >= (2, 6)" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies -- this can take a few minutes the first time...
    if exist constraints.lock.txt (
        %PY% -m pip install -r requirements-core.txt -c constraints.lock.txt
    ) else (
        %PY% -m pip install -r requirements-core.txt -c constraints.txt
    )
    if errorlevel 1 (
        echo.
        echo Installing dependencies failed -- see the error above.
        echo A common fix: %PY% -m pip install --upgrade pip
        if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
        exit /b 1
    )
    echo.
)

REM --- Plain-words setup check (ffmpeg, JS runtime, CUDA) -----------------
%PY% check_setup.py
echo.

REM --- Prebuilt React app (frontend\dist) -----------------------------------
REM End users never need Node.js: the frontend ships as a prebuilt release
REM zip (docs/RELEASE.md) unzipped into frontend\dist. npm only ever runs
REM here when a developer passes --build-frontend.
if exist "frontend\dist\index.html" goto :frontend_ready
if defined BAIHE_BUILD_FRONTEND goto :build_frontend
if defined BAIHE_CI goto :frontend_missing_noninteractive
if defined BAIHE_SERVER_ONLY goto :frontend_missing_noninteractive
echo The app's screens ^(frontend\dist^) aren't installed yet.
echo.
echo Either:
echo   1. Download baihe-frontend-^<version^>.zip from the project's GitHub
echo      Releases page and unzip it into this folder, so that this file exists:
echo        %CD%\frontend\dist\index.html
echo   or
echo   2. ^(developers, needs Node.js 22^) build it here:  start.bat --build-frontend
echo.
echo Then run start.bat again. See docs\RELEASE.md for details.
pause
exit /b 1

:frontend_missing_noninteractive
echo WARNING: frontend\dist\index.html is missing -- the server will run API-only.
echo.
goto :frontend_ready

:build_frontend
where npm >nul 2>nul
if errorlevel 1 (
    echo --build-frontend needs Node.js and npm, and they weren't found on PATH.
    echo Install Node.js 22 from https://nodejs.org/, open a NEW window and run
    echo this again -- or use the prebuilt release zip ^(docs\RELEASE.md^).
    if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
    exit /b 1
)
echo Building the React app ^(npm ci, npm run build^) -- a few minutes the first time...
pushd frontend
call npm ci
if errorlevel 1 goto :build_failed
call npm run build
if errorlevel 1 goto :build_failed
popd
if exist "frontend\dist\index.html" goto :frontend_ready
echo The build finished but frontend\dist\index.html still isn't there.
if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
exit /b 1

:build_failed
popd
echo.
echo Building the React app failed -- see the npm error above.
if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
exit /b 1

:frontend_ready

REM --- Already running? ----------------------------------------------------
call :health_ok
if not errorlevel 1 (
    echo Baihe Studio is already running at %APP_URL% -- opening a window on it.
    goto :open_window
)
call :port_is_open
if not errorlevel 1 (
    echo Something else is already using port %PORT%, and it isn't Baihe Studio
    echo ^(%APP_URL%api/health doesn't answer^). Close that program, or choose
    echo another port first, e.g.:  set BAIHE_API_PORT=8601
    if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
    exit /b 1
)

REM --- Start the server ------------------------------------------------------
echo Starting Baihe Studio at %APP_URL% ...
if defined BAIHE_CI goto :start_server_noninteractive
if defined BAIHE_SERVER_ONLY goto :start_server_noninteractive
start "Baihe Studio (server -- closing this window stops the app)" /min ^
    %PY% -m api
goto :server_started

:start_server_noninteractive
REM Step 10b follow-up: `start`'s own new-console-window creation can
REM fail with "ERROR: Input redirection is not supported, exiting the
REM process immediately." when run from a shell with no real console
REM attached -- exactly how GitHub Actions' Windows `cmd` shell runs this
REM script (a real, confirmed failure on this project's own Windows CI
REM job, not a hypothetical). PowerShell's Start-Process -NoNewWindow
REM launches the same process without ever going through cmd.exe's
REM `start` builtin, sidestepping that failure mode entirely -- used
REM here for --ci/--server-only specifically, since neither one wants or
REM needs a visible window anyway. The child inherits BAIHE_API_HOST/PORT.
powershell -NoProfile -Command ^
    "Start-Process -FilePath '%VENV_DIR%\Scripts\python.exe' -ArgumentList '-m api' -NoNewWindow -RedirectStandardOutput 'api_server.log' -RedirectStandardError 'api_server_err.log'"
:server_started

REM Waits for /api/health to answer before opening a window, so the first
REM thing you see isn't a "can't connect" page. About 60 tries, 1s apart.
set /a _tries=0
:wait_loop
call :health_ok
if not errorlevel 1 goto :open_window
set /a _tries+=1
if %_tries% GEQ 60 (
    echo.
    echo The server didn't answer at %APP_URL%api/health after about a minute --
    echo something went wrong while starting it. Check the "Baihe Studio ^(server^)"
    echo window ^(or api_server_err.log with --ci/--server-only^) for the error.
    if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_loop

REM --- Open its own window (Edge app mode, falling back down) ---------------
:open_window
if defined BAIHE_CI (
    echo /api/health is answering at %APP_URL% -- CI mode, not opening a browser window.
    exit /b 0
)
if defined BAIHE_SERVER_ONLY (
    echo /api/health is answering at %APP_URL% -- server-only mode, not opening a browser window.
    exit /b 0
)

set "EDGE_EXE="
for %%P in (
    "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
    "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
    "%LocalAppData%\Microsoft\Edge\Application\msedge.exe"
) do if not defined EDGE_EXE if exist %%P set "EDGE_EXE=%%~P"
if defined EDGE_EXE (
    start "" "%EDGE_EXE%" --app=%APP_URL%
    goto :eof
)
where msedge >nul 2>nul
if not errorlevel 1 (
    start "" msedge --app=%APP_URL%
    goto :eof
)

set "CHROME_EXE="
for %%P in (
    "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do if not defined CHROME_EXE if exist %%P set "CHROME_EXE=%%~P"
if defined CHROME_EXE (
    start "" "%CHROME_EXE%" --app=%APP_URL%
    goto :eof
)
where chrome >nul 2>nul
if not errorlevel 1 (
    start "" chrome --app=%APP_URL%
    goto :eof
)

echo Neither Edge nor Chrome was found in the usual places -- opening your
echo default browser instead ^(it won't look like its own window there^).
start "" %APP_URL%
goto :eof

REM --- Helper: sets errorlevel 0 if something answers on %PORT% ------------
:port_is_open
%PY% -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1', %PORT%))==0 else 1)" 2>nul
exit /b %errorlevel%

REM --- Helper: sets errorlevel 0 if GET /api/health answers 200 -------------
:health_ok
%PY% -c "import sys,urllib.request as u; r=u.urlopen('http://127.0.0.1:%PORT%/api/health', timeout=1); sys.exit(0 if r.status==200 else 1)" >nul 2>nul
exit /b %errorlevel%
