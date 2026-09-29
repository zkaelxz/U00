@echo off
setlocal enabledelayedexpansion
REM start-react.bat -- one-command launcher for the React app (experimental).
REM
REM Runs the FastAPI server, which also serves the built React app
REM (frontend\dist), as ONE process on ONE port -- no Vite or npm at
REM runtime. Then opens http://127.0.0.1:8600/ in your default browser.
REM
REM   * Uses the venv from start.bat (venv\). Run start.bat once first if
REM     it doesn't exist yet.
REM   * If frontend\dist is missing, builds it once (npm ci, then npm run build).
REM     That needs Node.js (22 is what CI uses). Once built, Node is not
REM     needed again until the frontend code changes -- delete
REM     frontend\dist to force a rebuild.
REM   * Loopback only (127.0.0.1), no login -- same as `python -m api`.
REM
REM This script does not touch start.bat or the Streamlit app.

cd /d "%~dp0"

set VENV_DIR=venv
set PY="%VENV_DIR%\Scripts\python.exe"
if not defined BAIHE_API_PORT set BAIHE_API_PORT=8600
set APP_URL=http://127.0.0.1:%BAIHE_API_PORT%/

echo ============================================
echo   Baihe Studio (React app)
echo ============================================
echo.

REM --- Virtual environment ---------------------------------------------------
if not exist %PY% (
    echo The Python environment "%VENV_DIR%\" doesn't exist yet.
    echo.
    echo Run start.bat once first -- it creates the environment and installs
    echo what the app needs -- then run this again.
    pause
    exit /b 1
)
%PY% -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
    echo The Python environment is missing the web server packages
    echo ^(fastapi, uvicorn^).
    echo.
    echo Run start.bat once first so it installs everything, or run:
    echo   %PY% -m pip install -r requirements-core.txt
    pause
    exit /b 1
)

REM --- Build the frontend once, if needed -----------------------------------
if exist "frontend\dist\index.html" goto :frontend_ready

echo The React app hasn't been built yet ^(frontend\dist is missing^).
where npm >nul 2>nul
if errorlevel 1 (
    echo.
    echo Building it needs Node.js and npm, and they weren't found on PATH.
    echo.
    echo Install Node.js 22 from https://nodejs.org/ ^(the LTS installer adds
    echo npm and puts it on PATH^), open a NEW window, then run this again.
    pause
    exit /b 1
)
echo Building it now -- this takes a few minutes the first time only...
pushd frontend
call npm ci
if errorlevel 1 (
    popd
    echo.
    echo "npm ci" failed -- see the error above. Common causes: no internet
    echo connection, or a Node.js version older than 20.
    pause
    exit /b 1
)
call npm run build
if errorlevel 1 (
    popd
    echo.
    echo "npm run build" failed -- see the error above.
    pause
    exit /b 1
)
popd
if not exist "frontend\dist\index.html" (
    echo.
    echo The build finished but frontend\dist\index.html still isn't there.
    pause
    exit /b 1
)
echo.
:frontend_ready

REM --- Already running? -------------------------------------------------------
call :port_is_open
if not errorlevel 1 (
    echo Something is already answering on port %BAIHE_API_PORT% -- opening it
    echo in your browser instead of starting a second copy.
    start "" "%APP_URL%"
    exit /b 0
)

REM --- Start the server, wait for it, open the browser ---------------------------
echo Starting Baihe Studio at %APP_URL%
echo ^(closing the server window stops the app^)
start "Baihe Studio (server -- closing this window stops the app)" /min ^
    %PY% -m api

set /a _tries=0
:wait_loop
call :port_is_open
if not errorlevel 1 goto :open_browser
set /a _tries+=1
if %_tries% GEQ 60 (
    echo.
    echo The server didn't answer after 30 seconds -- something may have gone
    echo wrong. Check the "Baihe Studio ^(server^)" window for an error.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_loop

:open_browser
start "" "%APP_URL%"
echo Opened %APP_URL% in your default browser.
exit /b 0

REM --- Helper: sets errorlevel 0 if something answers on the port -----------------
:port_is_open
%PY% -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1', %BAIHE_API_PORT%))==0 else 1)" 2>nul
exit /b %errorlevel%
