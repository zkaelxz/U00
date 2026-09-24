@echo off
REM make_lock.bat -- snapshots your current, working set of installed
REM package versions into constraints.lock.txt.
REM
REM Run this once your setup is working the way you want. From then on,
REM constraints.lock.txt takes priority over constraints.txt for any
REM install command that checks for it (the README's install steps,
REM the CI workflow, and Step 10's launcher) -- commit it so a fresh
REM clone reproduces the exact versions that worked for you, instead of
REM whatever the loose >= bounds happen to resolve to on that day.

pip freeze > constraints.lock.txt
echo Wrote constraints.lock.txt -- commit this file to lock in your current working setup.
