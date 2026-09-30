@echo off
echo Starting MBEControl...

REM Change into the directory where this .bat file lives (portable — works on any machine)
cd /d "%~dp0"

REM Activate virtual environment
call venv\Scripts\activate

REM Run via watchdog — auto-restarts on crash and reports the interrupted script
python watchdog.py