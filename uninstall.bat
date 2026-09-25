@echo off
setlocal enabledelayedexpansion
REM uninstall.bat -- Step 10. This app has very little system footprint
REM to begin with: no registry entries, nothing in Program Files, no
REM PATH changes -- it's just this folder plus a desktop shortcut. This
REM removes exactly that, and separately asks (default: no) before
REM touching your library.

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
echo   - ffmpeg, Deno/Node/Bun, Ollama, or your CUDA/GPU driver install --
echo     those are your own separate, system-wide installs from other
echo     installers. This uninstaller doesn't try to remove them, since
echo     it can't know whether something else on this machine still
echo     depends on them.
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
set /p DELETE_LIBRARY="Also delete your library (all projects, translations, audio/video, backups)? This cannot be undone. [y/N] "
if /i "%DELETE_LIBRARY%"=="y" (
    if exist "library\" (
        rmdir /s /q "library"
        echo Deleted library\.
    ) else (
        echo No library\ folder found.
    )
) else (
    echo Keeping library\ -- nothing there was touched.
)

echo.
echo Done. This folder ^(and library\, if you kept it^) can now be deleted
echo by hand whenever you're ready -- this script doesn't delete itself
echo or the rest of the folder, only what's listed above.
pause
