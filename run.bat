@echo off
title MBE Control
REM Work in the project folder, wherever this file is started from.
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo The virtual environment "venv" was not found.
    echo Run install_windows.bat first.
    pause
    exit /b 1
)

echo Starting MBE Control...
REM Run via watchdog: restarts after a crash and reports the interrupted script.
"venv\Scripts\python.exe" watchdog.py
set RC=%ERRORLEVEL%

REM Keep the window open only if something went wrong, so the message can be read.
if not "%RC%"=="0" pause
exit /b %RC%
