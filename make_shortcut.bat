@echo off
REM make_shortcut.bat -- creates a "Baihe Studio" shortcut on
REM the desktop pointing at start.bat, with the app's own icon, starting
REM minimized so the console window stays out of the way. Safe to run
REM again later -- it just overwrites the same shortcut file.

setlocal
cd /d "%~dp0"

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "ICON_PATH=%SCRIPT_DIR%\assets\app_icon.ico"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell;" ^
    "$desktop = $ws.SpecialFolders('Desktop');" ^
    "$old = [System.IO.Path]::Combine($desktop, 'Baihe Subtitler.lnk');" ^
    "if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old };" ^
    "$shortcut = $ws.CreateShortcut([System.IO.Path]::Combine($desktop, 'Baihe Studio.lnk'));" ^
    "$shortcut.TargetPath = [System.IO.Path]::Combine('%SCRIPT_DIR%', 'start.bat');" ^
    "$shortcut.WorkingDirectory = '%SCRIPT_DIR%';" ^
    "$shortcut.IconLocation = '%ICON_PATH%';" ^
    "$shortcut.WindowStyle = 7;" ^
    "$shortcut.Description = 'Baihe Studio';" ^
    "$shortcut.Save()"

if errorlevel 1 (
    echo.
    echo Couldn't create the shortcut -- see the error above.
    pause
    exit /b 1
)

echo.
echo Created a "Baihe Studio" shortcut on your desktop (a "Baihe Subtitler" one from before the rename is removed).
pause
