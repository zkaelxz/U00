@echo off
REM Delayed expansion stays off until the folder is entered and its path saved:
REM with it on, a ! in the path would be eaten from %~dp0.
setlocal DisableDelayedExpansion
REM uninstall.bat -- Step 10. This app has very little system footprint
REM to begin with: no registry entries, nothing in Program Files, no
REM PATH changes of its own -- it's just this folder plus a desktop
REM shortcut. This removes exactly that, then keeps your library unless you
REM type DELETE, and (default: no) asks before checking
REM your PATH for ffmpeg/Tesseract entries you may have added by hand
REM during setup.

cd /d "%~dp0" || (
    echo Could not enter the Baihe folder, so nothing was removed.
    pause
    exit /b 1
)
set "APP_DIR=%~dp0"
setlocal EnableDelayedExpansion

echo ============================================
echo   Baihe Studio -- Uninstall
echo ============================================
echo.
echo This will remove:
echo   - The desktop shortcut and Start Menu entry, if either exists
echo   - The venv\ virtual environment in this folder
echo   - Downloaded model weights this app manages under this folder
echo     (model_cache\, if portable mode was ever used)
echo   - vtuber_names.json (the downloaded VTuber name list) and its
echo     unfinished download, vtuber_names.json.part
echo.
echo If you later type DELETE to delete your library, the restore lock and
echo the library.pre_restore_* and .restore_staging_* folders beside it go too.
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
REM Shortcuts made before the rename are still called "Baihe Subtitler".
set "DESKTOP_LNK=%USERPROFILE%\Desktop\Baihe Studio.lnk"
set "OLD_DESKTOP_LNK=%USERPROFILE%\Desktop\Baihe Subtitler.lnk"
set "REMOVED_DESKTOP="
if exist "%DESKTOP_LNK%" (
    del "%DESKTOP_LNK%"
    set "REMOVED_DESKTOP=1"
)
if exist "%OLD_DESKTOP_LNK%" (
    del "%OLD_DESKTOP_LNK%"
    set "REMOVED_DESKTOP=1"
)
if defined REMOVED_DESKTOP (
    echo Removed the desktop shortcut.
) else (
    echo No desktop shortcut found -- nothing to remove there.
)

set "STARTMENU_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
set "REMOVED_STARTMENU="
if exist "%STARTMENU_DIR%\Baihe Studio.lnk" (
    del "%STARTMENU_DIR%\Baihe Studio.lnk"
    set "REMOVED_STARTMENU=1"
)
if exist "%STARTMENU_DIR%\Baihe Subtitler.lnk" (
    del "%STARTMENU_DIR%\Baihe Subtitler.lnk"
    set "REMOVED_STARTMENU=1"
)
if defined REMOVED_STARTMENU echo Removed the Start Menu entry.

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

REM Relative names on purpose: the script has done cd /d "%~dp0", so nothing
REM here can reach outside this folder, whatever BAIHE_DATA_DIR says.
if exist "vtuber_names.json" (
    call :removefile "vtuber_names.json"
    if errorlevel 1 (
        echo Could not remove vtuber_names.json -- it may be in use.
    ) else (
        echo Removed vtuber_names.json ^(the downloaded VTuber name list^).
    )
)
REM Only the one name Baihe writes here (services/vdb_names_service.py); other
REM .part files belong to downloads in subfolders, or to other programs.
if exist "vtuber_names.json.part" (
    call :removefile "vtuber_names.json.part"
    if errorlevel 1 (
        echo Could not remove vtuber_names.json.part -- it may be in use.
    ) else (
        echo Removed vtuber_names.json.part ^(an unfinished download^).
    )
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
    powershell -NoProfile -ExecutionPolicy Bypass -File "!APP_DIR!uninstall_path_cleanup.ps1"
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
    set "LIB_PATH=!APP_DIR!library"
    call :foldersize LIB_PATH LIB_SIZE
    echo Your library folder: !LIB_PATH!
    echo Size: !LIB_SIZE!
    set "SZ_LIST=!LIB_PATH!"
    set "HAVE_NEIGHBOURS="
    for /d %%D in ("library.pre_restore_*" ".restore_staging_*") do (
        if not defined HAVE_NEIGHBOURS echo Typing DELETE also deletes these copies a restore left beside it:
        set "HAVE_NEIGHBOURS=1"
        echo   - %%D
        set "SZ_LIST=!SZ_LIST!|%%~fD"
    )
    if exist "library.restore-lock" echo Typing DELETE also deletes the restore lock file, library.restore-lock.
    if defined HAVE_NEIGHBOURS (
        call :foldersize SZ_LIST TOTAL_SIZE
        echo Size of the library plus these copies: !TOTAL_SIZE!
    )
    echo It holds all your projects, translations, audio/video and library.db,
    echo and your backups too -- backups are stored inside it by default.
    echo Deleting it cannot be undone.
    set "DELETE_LIBRARY="
    set /p DELETE_LIBRARY="Type DELETE in capital letters to delete it, or press Enter to keep it: "
    if "!DELETE_LIBRARY!"=="DELETE" (
        call :removedir "library"
        if exist "library\" (
            echo Could not delete library\ completely -- a file may be in use.
        ) else (
            echo Deleted library\.
            REM Only once the library itself is gone: until then a pre_restore
            REM folder may be the only good copy of it.
            call :removeneighbours
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
REM What this script itself could not or would not remove, by name and size only; file contents are never read.
call :leftover "Your library (projects, translations, backups)" "!APP_DIR!library"
call :leftover "venv folder (not fully removed)" "!APP_DIR!venv"
call :leftover "model_cache folder (not fully removed)" "!APP_DIR!model_cache"
if exist "vtuber_names.json" echo   - vtuber_names.json ^(could not be removed^)
if exist "!DESKTOP_LNK!" echo   - Desktop shortcut: !DESKTOP_LNK!
if exist "!OLD_DESKTOP_LNK!" echo   - Desktop shortcut: !OLD_DESKTOP_LNK!
for %%N in ("Baihe Studio" "Baihe Subtitler") do if exist "!STARTMENU_DIR!\%%~N.lnk" echo   - Start Menu entry: !STARTMENU_DIR!\%%~N.lnk
for /d %%D in ("library.pre_restore_*" "library.pre_reset_*" ".restore_staging_*") do call :leftover "Library copy kept beside it (may hold your real library if library\ is missing)" "%%~fD"
if exist "library.restore-lock" echo   - library.restore-lock ^(restore lock file^)
if exist ".env" echo   - .env ^(your settings and API keys; stays in this folder, contents are never shown^)
if defined BAIHE_DATA_DIR if exist "!BAIHE_DATA_DIR!\" echo   - Your BAIHE_DATA_DIR folder: !BAIHE_DATA_DIR! ^(library, settings, vtuber_names.json and anything else Baihe saved there; not touched^)
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
echo Programs Baihe never installs but may use ^(left alone^):
call :wingetprog "Ollama" ollama
echo Anything not listed here was not installed by Baihe and was not touched.

echo.
echo Done. This folder ^(and library\, if you kept it^) can now be deleted
echo by hand whenever you're ready -- this script doesn't delete itself
echo or the rest of the folder, only what's listed above.
pause
exit /b 0

:foldersize
REM %1 = NAME of a variable holding a folder (several joined with |), %2 = variable to set.
REM Names, not values: a value with a ! or quote would be mangled as an argument.
REM The paths go through the environment so quotes, spaces and non-ASCII characters
REM never reach PowerShell's parser.
set "BAIHE_SZ_PATH=!%~1!"
set "%~2=unknown"
for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "$s = ($env:BAIHE_SZ_PATH -split '\|' | ForEach-Object { Get-ChildItem -LiteralPath $_ -Recurse -Force -File -ErrorAction SilentlyContinue }) | Measure-Object Length -Sum; $b = [double]$s.Sum; if ($b -ge 1GB) { '{0:N1} GB' -f ($b / 1GB) } else { '{0:N0} MB' -f ($b / 1MB) }"`) do set "%~2=%%S"
exit /b 0

:leftover
REM %1 = label, %2 = path. Lists it with its size when it exists; never deletes.
REM Delayed expansion is off while the arguments are copied, so a ! in a path survives.
setlocal DisableDelayedExpansion
set "LEFT_LABEL=%~1"
set "LEFT_PATH=%~2"
setlocal EnableDelayedExpansion
if exist "!LEFT_PATH!" (
    call :foldersize LEFT_PATH LEFT_SIZE
    echo   - !LEFT_LABEL!: !LEFT_PATH! ^(!LEFT_SIZE!^)
)
endlocal
endlocal
exit /b 0

:wingetprog
REM %1 = label, %2 = command name. Prints where it is installed, if found.
set "WINGET_FOUND="
for /f "delims=" %%P in ('where %~2 2^>nul') do if not defined WINGET_FOUND set "WINGET_FOUND=%%P"
if defined WINGET_FOUND echo   - %~1: !WINGET_FOUND!
exit /b 0

:removefile
REM %1 = a file name in this folder. Deletes a plain file, or a file symlink
REM (the link, never its target). A folder of that name is left alone: del on a
REM folder would empty it. Exit 0 = gone, 1 = absent, 2 = a folder, 3 = still there.
set "RF_ATTR="
for %%F in ("%~1") do set "RF_ATTR=%%~aF"
if not defined RF_ATTR exit /b 1
if /i "!RF_ATTR:~0,1!"=="d" exit /b 2
del /f /q "%~1" >nul 2>&1
if exist "%~1" exit /b 3
exit /b 0

:removedir
REM %1 = a folder name in this folder. A plain folder goes with its contents.
REM A junction or symlink (attribute string contains l) is removed as a link
REM with rmdir and no /s, so the folder it points to is never followed.
REM Exit 0 = gone, 1 = absent, 2 = not a folder, 3 = still there.
set "RD_ATTR="
for %%F in ("%~1") do set "RD_ATTR=%%~aF"
if not defined RD_ATTR exit /b 1
if /i not "!RD_ATTR:~0,1!"=="d" exit /b 2
if /i not "!RD_ATTR:l=!"=="!RD_ATTR!" (
    rmdir "%~1" >nul 2>&1
) else (
    rmdir /s /q "%~1" >nul 2>&1
)
if exist "%~1" exit /b 3
exit /b 0

:removeneighbours
REM The files a library restore leaves beside library\: its lock, the previous
REM library, and an unfinished staging folder (db/library_folder.py,
REM services/library_restore_service.py). A reset copy (library.pre_reset_*) is
REM a full copy of the data, but it is not in this list, so it is only reported.
if exist "library.restore-lock" (
    call :removefile "library.restore-lock"
    if errorlevel 1 (
        echo Could not remove library.restore-lock.
    ) else (
        echo Removed library.restore-lock.
    )
)
for /d %%D in ("library.pre_restore_*" ".restore_staging_*") do (
    set "NEIGHBOUR=%%D"
    call :removedir "%%D"
    if errorlevel 1 (
        echo Could not remove !NEIGHBOUR! completely.
    ) else (
        echo Removed !NEIGHBOUR!.
    )
)
exit /b 0
