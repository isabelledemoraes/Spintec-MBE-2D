@echo off
title MBE Control
echo Starting MBE Control...
call venv\Scripts\activate
python watchdog.py
pause
