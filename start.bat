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

cd /d "%~dp0"

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--portable" set BAIHE_PORTABLE=1
if /i "%~1"=="--ci" set BAIHE_CI=1
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
    if not defined BAIHE_CI pause
    exit /b 1
)

REM --- Virtual environment ------------------------------------------------
if not exist %PY% (
    echo Setting up a virtual environment in "%VENV_DIR%\" ^(first run only^)...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo Could not create the virtual environment. See the error above.
        if not defined BAIHE_CI pause
        exit /b 1
    )
)

REM --- Dependencies --------------------------------------------------------
REM Only actually installs anything the first time, or after
REM requirements-core.txt changes -- importing streamlit is a cheap way
REM to tell "already installed" from "needs installing" without shelling
REM out to pip just to ask.
%PY% -c "import streamlit" >nul 2>nul
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
        if not defined BAIHE_CI pause
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
start "Baihe Subtitler (server -- closing this window stops the app)" /min ^
    %PY% -m streamlit run app.py --server.headless true --server.port %PORT%

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
    if not defined BAIHE_CI pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_loop

REM --- Open its own window (Edge app mode, falling back down) ---------------
:open_window
if defined BAIHE_CI (
    echo Server is answering on port %PORT% -- CI mode, not opening a browser window.
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
