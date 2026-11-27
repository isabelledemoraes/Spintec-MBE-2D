@echo off
title MBE Control - download packages for an offline PC
REM Run this on a PC WITH internet, using the SAME Python version (3.x, 64-bit)
REM as the instrument PC. It fills the "wheels" folder; copy the whole project
REM folder (including "wheels") to the instrument PC and run install_windows.bat.
cd /d "%~dp0"

python --version
set REQ=requirements.txt
if exist requirements-lock.txt set REQ=requirements-lock.txt
python -m pip download -r %REQ% -d wheels
if errorlevel 1 (
    echo.
    echo  ERROR: download failed.
    pause
    exit /b 1
)
echo.
echo  Done. Copy the project folder, including "wheels", to the instrument PC.
pause
