@echo off
REM Builds the Android app with the next version number and offers it to the phone.
cd /d "%~dp0"
if exist venv\Scripts\python.exe (
    venv\Scripts\python.exe tools\publish_android_app.py
) else (
    python tools\publish_android_app.py
)
pause
