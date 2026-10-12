@echo off
REM Double-click to rebuild the app's screens (frontend\dist) and then start Baihe Studio.
REM Same as start.bat --build-frontend, which needs Node.js; extra arguments pass through.
cd /d "%~dp0"
call "%~dp0start.bat" --build-frontend %*
