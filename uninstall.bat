@echo off
setlocal enabledelayedexpansion
REM uninstall.bat -- Step 10. This app has very little system footprint
REM to begin with: no registry entries, nothing in Program Files, no
REM PATH changes of its own -- it's just this folder plus a desktop
REM shortcut. This removes exactly that, then keeps your library unless you
REM type DELETE, and (default: no) asks before checking
REM your PATH for ffmpeg/Tesseract entries you may have added by hand
REM during setup.

cd /d "%~dp0"

echo ============================================
echo   Baihe Subtitler -- Uninstall
echo ============================================
echo.
echo This will remove:
echo   - The desktop shortcut and Start Menu entry, if either exists
echo   - The venv\ virtual environment in this folder
echo   - Downloaded model weights this app manages under this folder
echo     (model_cache\, if portable mode was ever used)
echo.
echo It will NOT touch:
echo   - ffmpeg, Deno/Node/Bun, Ollama, or your CUDA/GPU driver install
echo     themselves, or their PATH entries unless you opt in below --
echo     those are your own separate, system-wide installs from other
echo     installers, and this uninstaller can't know whether something
echo     else on this machine still depends on them.
echo   - Model weights Hugging Face/PyTorch downloaded to their own
echo     default cache (usually %%USERPROFILE%%\.cache\) rather than
echo     inside this folder -- only files this app's own portable mode
echo     put under this folder are cleaned up here.
echo.
set /p CONFIRM="Continue? [y/N] "
if /i not "%CONFIRM%"=="y" (
    echo Cancelled -- nothing was removed.
    pause
    exit /b 0
)

echo.
set "DESKTOP_LNK=%USERPROFILE%\Desktop\Baihe Subtitler.lnk"
if exist "%DESKTOP_LNK%" (
    del "%DESKTOP_LNK%"
    echo Removed the desktop shortcut.
) else (
    echo No desktop shortcut found -- nothing to remove there.
)

set "STARTMENU_LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Baihe Subtitler.lnk"
if exist "%STARTMENU_LNK%" (
    del "%STARTMENU_LNK%"
    echo Removed the Start Menu entry.
)

if exist "venv\" (
    rmdir /s /q "venv"
    echo Removed the venv\ virtual environment.
) else (
    echo No venv\ folder found -- nothing to remove there.
)

if exist "model_cache\" (
    rmdir /s /q "model_cache"
    echo Removed model_cache\ ^(downloaded models from portable mode^).
)

echo.
echo App files removed. Your library is untouched so far.
echo.
echo If you added ffmpeg or Tesseract to PATH by hand while setting this
echo app up, they can be found and removed here -- only your user-level
echo PATH is touched (never system-wide, which needs admin rights), and
echo only entries that look like those two tools. Skip this if you still
echo use ffmpeg or Tesseract for anything else on this machine, since
echo removing a shared entry would break that too.
set /p CHECK_PATH="Check your PATH for ffmpeg/Tesseract entries to remove? [y/N] "
if /i "%CHECK_PATH%"=="y" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_path_cleanup.ps1"
)

echo.
if defined BAIHE_DATA_DIR (
    echo BAIHE_DATA_DIR is set, so your real library is NOT in this folder:
    echo   !BAIHE_DATA_DIR!
    echo This script never touches that location, so it is kept.
    echo.
)
REM The comparison below is exact and case-sensitive on purpose: "y", "yes"
REM and Enter must all keep the library.
if exist "library\" (
    set "LIB_PATH=%~dp0library"
    call :foldersize "!LIB_PATH!" LIB_SIZE
    echo Your library folder: !LIB_PATH!
    echo Size: !LIB_SIZE!
    echo It holds all your projects, translations, audio/video and library.db,
    echo and your backups too -- backups are stored inside it by default.
    echo Deleting it cannot be undone.
    set "DELETE_LIBRARY="
    set /p DELETE_LIBRARY="Type DELETE in capital letters to delete it, or press Enter to keep it: "
    if "!DELETE_LIBRARY!"=="DELETE" (
        rmdir /s /q "library"
        if exist "library\" (
            echo Could not delete library\ completely -- a file may be in use.
        ) else (
            echo Deleted library\.
        )
    ) else (
        echo Keeping library\ -- you did not type DELETE, so nothing there was touched.
    )
) else (
    echo No library\ folder found next to this script -- nothing to delete.
)

echo.
echo ============================================
echo   Left on this PC ^(not removed by this uninstaller^)
echo ============================================
echo These can be large and may be shared with other programs. Delete them
echo by hand if you no longer need them.
set "HF_DIR=%HF_HOME%"
if not defined HF_DIR set "HF_DIR=%USERPROFILE%\.cache\huggingface"
call :leftover "Hugging Face model cache" "!HF_DIR!"
set "TORCH_DIR=%TORCH_HOME%"
if not defined TORCH_DIR set "TORCH_DIR=%USERPROFILE%\.cache\torch"
call :leftover "PyTorch cache" "!TORCH_DIR!"
call :leftover "pip download cache" "%LOCALAPPDATA%\pip\Cache"
set "OLLAMA_DIR=%OLLAMA_MODELS%"
if not defined OLLAMA_DIR set "OLLAMA_DIR=%USERPROFILE%\.ollama\models"
call :leftover "Ollama models" "!OLLAMA_DIR!"
echo Programs installed with winget during setup ^(remove in Settings, Apps,
echo or with: winget uninstall^):
call :wingetprog "Python" python
call :wingetprog "Git" git
call :wingetprog "ffmpeg" ffmpeg
call :wingetprog "Node.js" node

echo.
echo Done. This folder ^(and library\, if you kept it^) can now be deleted
echo by hand whenever you're ready -- this script doesn't delete itself
echo or the rest of the folder, only what's listed above.
pause
exit /b 0

:foldersize
REM %1 = folder, %2 = variable to set. The path goes through the environment
REM so quotes, spaces and non-ASCII characters never reach PowerShell's parser.
set "BAIHE_SZ_PATH=%~1"
set "%~2=unknown"
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "$s = Get-ChildItem -LiteralPath $env:BAIHE_SZ_PATH -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum; $b = [double]$s.Sum; if ($b -ge 1GB) { '{0:N1} GB' -f ($b / 1GB) } else { '{0:N0} MB' -f ($b / 1MB) }"`) do set "%~2=%%S"
exit /b 0

:leftover
REM %1 = label, %2 = path. Lists it with its size when it exists; never deletes.
if not exist "%~2" exit /b 0
call :foldersize "%~2" LEFT_SIZE
echo   - %~1: %~2 ^(!LEFT_SIZE!^)
exit /b 0

:wingetprog
REM %1 = label, %2 = command name. Prints where it is installed, if found.
set "WINGET_FOUND="
for /f "delims=" %%P in ('where %~2 2^>nul') do if not defined WINGET_FOUND set "WINGET_FOUND=%%P"
if defined WINGET_FOUND echo   - %~1: !WINGET_FOUND!
exit /b 0
