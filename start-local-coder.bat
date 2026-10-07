@echo off
REM Double-click to start the local model and OpenCode; extra arguments
REM (for example -Terminal or -LlamaDir D:\llama) pass through to the script.
REM -ExecutionPolicy Bypass applies to this run only, so no system setting changes.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\start-local-coder.ps1" %*
echo.
pause
