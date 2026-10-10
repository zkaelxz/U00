@echo off
setlocal enabledelayedexpansion
REM setup.bat -- optional helper for a new Windows PC.
REM
REM Checks for Git, Python, ffmpeg (with the subtitles filter) and, only if
REM the app's screens still have to be built, Node.js 22 or newer. Installs
REM only what is missing, with winget. start.bat never calls this; it only
REM points here when something is missing.
REM
REM Needs no admin rights itself; winget may show its own permission prompt.
REM Fixed winget commands only, and no network access other than winget.
REM The NVIDIA driver is only reported, never installed: winget does not
REM install it reliably.
REM
REM A program installed by winget is not on PATH in the window that
REM installed it, so after any install the checks cannot pass until the
REM script is run again in a new window.

cd /d "%~dp0"

echo ============================================
echo   Baihe Studio -- setup check
echo ============================================
echo.

set "INSTALLED_ANY="
set "MISS_GIT=" & set "MISS_PY=" & set "MISS_FFMPEG=" & set "MISS_LIBASS=" & set "MISS_NODE="
set "NEW_GIT=" & set "NEW_PY=" & set "NEW_FFMPEG=" & set "NEW_NODE="

set "HAVE_WINGET=1"
where winget >nul 2>nul
if errorlevel 1 set "HAVE_WINGET="
if not defined HAVE_WINGET (
    echo winget ^(Windows Package Manager^) wasn't found, so nothing can be
    echo installed automatically. Install "App Installer" from the Microsoft
    echo Store ^(https://aka.ms/getwinget^), or use the download links in the
    echo list at the end.
    echo.
)

REM --- Git ---------------------------------------------------------------
where git >nul 2>nul
if errorlevel 1 goto :git_missing
echo Git: found
goto :check_python
:git_missing
echo Git: not found
set "PKG_ID=Git.Git"
set "PKG_NAME=Git"
call :winget_install
if errorlevel 1 (set "MISS_GIT=1") else (set "NEW_GIT=1")

REM --- Python ------------------------------------------------------------
REM Same real-versus-Store-stub test as start.bat: running it is the only
REM way to tell them apart.
:check_python
python --version >nul 2>nul
if errorlevel 1 goto :python_not_runnable
python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :python_old
echo Python: found
goto :check_ffmpeg
:python_not_runnable
where python >nul 2>nul
if errorlevel 1 goto :python_missing
call :stub_warning
goto :python_missing
:python_old
echo Python: found, but older than 3.10. Installing 3.12 next to it; once it
echo is installed, start.bat --python-version 3.12 uses that one.
goto :python_install
:python_missing
echo Python: not found
:python_install
set "PKG_ID=Python.Python.3.12"
set "PKG_NAME=Python 3.12"
call :winget_install
if errorlevel 1 (set "MISS_PY=1") else (set "NEW_PY=1")

REM --- ffmpeg ------------------------------------------------------------
:check_ffmpeg
where ffmpeg >nul 2>nul
if errorlevel 1 goto :ffmpeg_missing
REM Burning subtitles into video needs the "subtitles" filter, which only
REM builds with libass have.
ffmpeg -hide_banner -filters 2>nul | findstr /c:" subtitles " >nul
if errorlevel 1 (
    echo ffmpeg: found, but without the subtitles filter ^(libass^)
    set "MISS_LIBASS=1"
) else (
    echo ffmpeg: found, with the subtitles filter
)
goto :check_node
:ffmpeg_missing
echo ffmpeg: not found
set "PKG_ID=Gyan.FFmpeg"
set "PKG_NAME=ffmpeg"
call :winget_install
if errorlevel 1 (set "MISS_FFMPEG=1") else (set "NEW_FFMPEG=1")

REM --- Node.js (only to build the app's screens) -------------------------
:check_node
if exist "frontend\dist\index.html" (
    echo Node.js: not needed, the app's screens are already in frontend\dist
    goto :check_nvidia
)
where node >nul 2>nul
if errorlevel 1 goto :node_missing
set "NODE_MAJOR="
for /f "tokens=1 delims=." %%V in ('node -v 2^>nul') do set "NODE_MAJOR=%%V"
if not defined NODE_MAJOR goto :node_missing
set "NODE_MAJOR=!NODE_MAJOR:~1!"
set /a NODE_N=NODE_MAJOR
if !NODE_N! GEQ 22 (
    echo Node.js: found ^(version !NODE_MAJOR!^)
    goto :check_nvidia
)
echo Node.js: found, but version !NODE_MAJOR! is older than 22
goto :node_ask
:node_missing
echo Node.js: not found
:node_ask
set "ANSWER="
set /p "ANSWER=Install Node.js LTS to build the app's screens? [Y/n] "
if /i "!ANSWER!"=="n" goto :node_declined
if /i "!ANSWER!"=="no" goto :node_declined
set "PKG_ID=OpenJS.NodeJS.LTS"
set "PKG_NAME=Node.js LTS"
call :winget_install
if errorlevel 1 (set "MISS_NODE=1") else (set "NEW_NODE=1")
goto :check_nvidia
:node_declined
echo Skipped. Download baihe-frontend-^<version^>.zip from the project's GitHub
echo Releases page instead ^(docs\RELEASE.md^).

REM --- NVIDIA driver (reported only) -------------------------------------
:check_nvidia
nvidia-smi >nul 2>nul
if errorlevel 1 (
    echo NVIDIA: no NVIDIA driver found: GPU features need one from https://www.nvidia.com/drivers; everything else works without it
) else (
    echo NVIDIA: NVIDIA driver found
)
echo.

REM --- Summary -------------------------------------------------------------
echo ============================================
set "STILL="
if defined NEW_GIT set "STILL=1"
if defined NEW_PY set "STILL=1"
if defined NEW_FFMPEG set "STILL=1"
if defined NEW_NODE set "STILL=1"
if defined MISS_GIT set "STILL=1"
if defined MISS_PY set "STILL=1"
if defined MISS_FFMPEG set "STILL=1"
if defined MISS_LIBASS set "STILL=1"
if defined MISS_NODE set "STILL=1"
if not defined STILL (
    echo Ready: everything Baihe needs is installed. Double-click start.bat.
    pause
    exit /b 0
)
if defined INSTALLED_ANY (
    echo Installed just now ^(not checked yet, Windows only finds new programs in a new window^):
    if defined NEW_GIT echo   - Git
    if defined NEW_PY echo   - Python 3.12
    if defined NEW_FFMPEG echo   - ffmpeg
    if defined NEW_NODE echo   - Node.js
    echo.
    echo Close this window, open a new one and run setup.bat again to check.
    echo.
)
if defined MISS_GIT echo still missing: Git -- https://git-scm.com/download/win
if defined MISS_PY echo still missing: Python 3.12 -- https://python.org/downloads/
if defined MISS_FFMPEG echo still missing: ffmpeg -- https://www.gyan.dev/ffmpeg/builds/
if defined MISS_LIBASS echo still missing: ffmpeg with the subtitles filter ^(libass^) -- https://www.gyan.dev/ffmpeg/builds/
if defined MISS_NODE echo still missing: Node.js 22 or newer -- https://nodejs.org/
pause
exit /b 1

REM --- Helper: installs %PKG_ID% with winget; errorlevel 0 if it did -------
:winget_install
if not defined HAVE_WINGET exit /b 1
echo Installing %PKG_NAME% with winget ^(it may ask for permission^)...
winget install -e --id %PKG_ID% --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1
set "INSTALLED_ANY=1"
exit /b 0

REM --- Helper: same wording as start.bat's Python check ---------------------
:stub_warning
echo Python is on PATH but isn't runnable -- this is the
echo Microsoft Store's non-functional "App execution aliases" stub,
echo not a real Python install.
echo.
echo Fix: Settings -^> Apps -^> Advanced app settings -^> App execution aliases
echo -^> turn OFF both "App Installer python.exe" and "App Installer python3.exe",
echo then run this again.
exit /b 0
