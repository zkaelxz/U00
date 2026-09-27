@echo off
setlocal enabledelayedexpansion
REM start.bat -- Step 10's one-click Windows launcher.
REM
REM Double-click this (or the desktop shortcut make_shortcut.bat creates)
REM to set up and run Baihe Subtitler with no typed commands. Safe to
REM run more than once: if the app is already running, this just opens
REM a window pointed at it instead of starting a second copy.
REM
REM   start.bat            -- normal launch
REM   start.bat --portable -- also turns on portable mode for this run
REM                            (see portable.py; a PORTABLE marker file
REM                            next to this script does the same thing
REM                            without needing the flag every time)
REM   start.bat --ci       -- Step 10b: non-interactive. Runs the exact
REM                            same bootstrap (venv, deps, dependency
REM                            check, wait for the server to answer), but
REM                            skips opening a browser window and never
REM                            calls `pause` -- so it can run unattended
REM                            on a CI runner. Exits 0 once the server
REM                            answers, non-zero (with the same message)
REM                            if it never does. Same effect as setting
REM                            the BAIHE_CI environment variable.
REM   start.bat --server-only -- Step 10e: identical browser-skip/no-pause
REM                            behavior as --ci, under a name that fits a
REM                            human deliberately running this machine as
REM                            an always-on personal server for other
REM                            devices on the LAN, rather than reaching for
REM                            a flag literally called "CI" for that. Same
REM                            effect as setting BAIHE_SERVER_ONLY.

cd /d "%~dp0"

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--portable" set BAIHE_PORTABLE=1
if /i "%~1"=="--ci" set BAIHE_CI=1
if /i "%~1"=="--server-only" set BAIHE_SERVER_ONLY=1
shift
goto :parse_args
:args_done

set PORT=8501
set VENV_DIR=venv
set PY="%VENV_DIR%\Scripts\python.exe"

echo ============================================
echo   Baihe Subtitler
echo ============================================
echo.

REM --- Python check -----------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo Python wasn't found on PATH.
    echo.
    echo Install Python 3.9 or newer from https://python.org/downloads/
    echo and make sure to tick "Add python.exe to PATH" during setup,
    echo then run this again.
    if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
    exit /b 1
)

REM --- Virtual environment ------------------------------------------------
if not exist %PY% (
    echo Setting up a virtual environment in "%VENV_DIR%\" ^(first run only^)...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo Could not create the virtual environment. See the error above.
        if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
        exit /b 1
    )
)

REM --- Dependencies --------------------------------------------------------
REM Checks every package requirements-core.txt actually installs, not
REM just streamlit -- a stale or partially-installed venv where
REM streamlit still imports fine but something else is missing used to
REM make this skip the install step entirely and fail later with a much
REM less clear error (Step 53). Keep this import list in sync with
REM requirements-core.txt's own packages.
%PY% -c "import streamlit, pandas, requests, bs4, anthropic" >nul 2>nul
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

REM --- Already running? ----------------------------------------------------
call :port_is_open
if not errorlevel 1 (
    echo Baihe Subtitler is already running on port %PORT% -- opening a window on it.
    goto :open_window
)

REM --- Start the server ------------------------------------------------------
echo Starting Baihe Subtitler on port %PORT% ...
if defined BAIHE_CI goto :start_server_noninteractive
if defined BAIHE_SERVER_ONLY goto :start_server_noninteractive
start "Baihe Subtitler (server -- closing this window stops the app)" /min ^
    %PY% -m streamlit run app.py --server.headless true --server.port %PORT%
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
REM needs a visible window anyway.
powershell -NoProfile -Command ^
    "Start-Process -FilePath '%VENV_DIR%\Scripts\python.exe' -ArgumentList '-m streamlit run app.py --server.headless true --server.port %PORT%' -NoNewWindow -RedirectStandardOutput 'streamlit_ci.log' -RedirectStandardError 'streamlit_ci_err.log'"
:server_started

REM Waits for the server to actually answer before opening a browser
REM window, so the first thing you see isn't a "can't connect" page.
set /a _tries=0
:wait_loop
call :port_is_open
if not errorlevel 1 goto :open_window
set /a _tries+=1
if %_tries% GEQ 60 (
    echo.
    echo The server didn't answer after 30 seconds -- something may have gone
    echo wrong. Check the "Baihe Subtitler ^(server^)" window for an error.
    if not defined BAIHE_CI if not defined BAIHE_SERVER_ONLY pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_loop

REM --- Open its own window (Edge app mode, falling back down) ---------------
:open_window
REM Step 10e: Streamlit already binds 0.0.0.0 under --server.headless
REM true (confirmed directly, not assumed), so another device on this
REM LAN can already reach this server -- it just never told anyone its
REM own address. Printed for every launch (CI/server-only included),
REM since that's exactly the case someone running this as an always-on
REM personal server most wants to see.
call :print_lan_url
if defined BAIHE_CI (
    echo Server is answering on port %PORT% -- CI mode, not opening a browser window.
    exit /b 0
)
if defined BAIHE_SERVER_ONLY (
    echo Server is answering on port %PORT% -- server-only mode, not opening a browser window.
    exit /b 0
)
set "APP_URL=http://localhost:%PORT%"

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

REM --- Helper: prints the LAN-reachable URL, for a personal-server setup --
REM Parses the first "IPv4 Address" line out of `ipconfig` (the primary
REM adapter's address on a normal single-NIC machine) -- a plain, built-in
REM Windows command, not a new dependency. English-locale label only; on a
REM non-English Windows install, or a machine with several adapters
REM where the first one isn't the right one, this just silently finds
REM nothing and falls back to the message below rather than guessing.
:print_lan_url
set "LAN_IP="
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
    if not defined LAN_IP set "LAN_IP=%%A"
)
if defined LAN_IP (
    set "LAN_IP=!LAN_IP: =!"
    echo Also reachable from other devices on this network at: http://!LAN_IP!:%PORT%
) else (
    echo Couldn't automatically determine this machine's LAN IP -- run
    echo "ipconfig" yourself and use its IPv4 Address with port %PORT% from
    echo another device on this network.
)
exit /b 0
