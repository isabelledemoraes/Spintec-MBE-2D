@echo off
setlocal EnableDelayedExpansion
title MBE Control - Windows Installer

REM Always work in the project folder, wherever the installer is started from
REM (double-click, "Run as administrator", a shortcut...).
cd /d "%~dp0"

echo.
echo ============================================================
echo   MBE Control Software - Windows Installation
echo ============================================================
echo   Project folder: %CD%
echo.

REM -- Step 1: Check Python (3.11 or newer: pandas 3 needs it) ----------------
echo [1/9] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found on PATH.
    echo.
    echo  Please install Python 3.11 or newer from:
    echo    https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: During installation, tick "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo.
    echo  ERROR: Python !PYVER! is too old. Python 3.11 or newer is required.
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python %PYVER% found. OK.

REM -- Step 2: Create virtual environment --------------------------------------
echo.
echo [2/9] Creating virtual environment ^(venv^)...
if exist venv\Scripts\python.exe (
    echo  Virtual environment already exists - skipping creation.
) else (
    python -m venv venv
    if errorlevel 1 (
        echo  ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  Virtual environment created. OK.
)

REM -- Step 3: Activate venv ---------------------------------------------------
echo.
echo [3/9] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo  ERROR: Could not activate virtual environment.
    pause
    exit /b 1
)
echo  Virtual environment active. OK.

REM -- Step 4/5: Install packages ----------------------------------------------
REM Offline PC: if a "wheels" folder is next to this file (made with
REM make_offline_wheels.bat on a PC with internet), install from it only.
echo.
if exist wheels\ (
    echo [4/9] Offline package folder "wheels" found - no internet needed.
    set PIP_ARGS=--no-index --find-links wheels
) else (
    echo [4/9] Upgrading pip...
    python -m pip install --upgrade pip --quiet
    set PIP_ARGS=
)

echo.
echo [5/9] Installing Python packages ^(this may take a few minutes^)...
REM Exact tested versions if requirements-lock.txt exists, else requirements.txt.
set REQ=requirements.txt
if exist requirements-lock.txt set REQ=requirements-lock.txt
echo  Using !REQ!
python -m pip install !PIP_ARGS! -r !REQ!
if errorlevel 1 (
    echo.
    echo  ERROR: Package installation failed. Check your internet connection
    echo  ^(or the "wheels" folder^) and run this installer again.
    pause
    exit /b 1
)
echo  All packages installed. OK.

REM -- Step 6: Check NI-DAQmx driver ------------------------------------------
echo.
echo [6/9] Checking NI-DAQmx ^(shutter control^)...
python -c "from nidaqmx.system import System; System.local().driver_version" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  WARNING: NI-DAQmx driver or Python package not working.
    echo.
    echo  Real shutter control needs the NI-DAQmx DRIVER ^(from ni.com^) and
    echo  the nidaqmx Python package. Without them the shutters are
    echo  SIMULATED on a PC with SIMULATION = True, and reported LOST
    echo  on the instrument PC ^(SIMULATION = False^).
    echo.
    echo  Install the driver from:
    echo    https://www.ni.com/en/support/downloads/drivers/download.ni-daqmx.html
    echo  then run this installer again.
    echo.
) else (
    echo  NI-DAQmx driver found. Shutter control enabled. OK.
)

REM -- Step 7: Configure addresses ---------------------------------------------
echo.
echo [7/9] Checking instrument configuration...
if exist addresses.py (
    echo  addresses.py found. OK.
) else (
    if exist config\addresses.py.example (
        copy config\addresses.py.example addresses.py >nul
        echo.
        echo  NOTICE: addresses.py was not found - a template has been copied.
        echo  You MUST edit addresses.py for THIS machine before using it:
        echo.
        echo    - COM ports, Modbus slave ids, NI device name ^(Dev1...^)
        echo    - MACHINE_NAME and SCRIPT_ARCHIVE_DIR  ^(Run is blocked until set^)
        echo    - max_temp / min_temp of every loop    ^(scripts need them^)
        echo    - Pressure unit "mbar" + pressure_interlock ^(Liquid_N2^)
        echo    - SIMULATION = False  ^(True = fake instruments^)
        echo.
        echo  Use Device Manager to find COM port numbers.
        echo  See README_INSTALL.md for details.
        echo.
    ) else (
        echo  WARNING: addresses.py not found and no example template available.
        echo  The software will not communicate with instruments until
        echo  a valid addresses.py is present.
    )
)

REM -- Step 8: Sanity check ----------------------------------------------------
echo.
echo [8/9] Running sanity check...
python -c "import PyQt6; import pyqtgraph; import numpy; import serial; import minimalmodbus; import plotly; import pandas; import webview; print('All core packages OK')"
if errorlevel 1 (
    echo  ERROR: Sanity check failed - one or more packages did not import correctly.
    pause
    exit /b 1
)
if exist addresses.py (
    python -c "import addresses as a; print('  Machine:', a.MACHINE_NAME, '| Simulation:', getattr(a, 'SIMULATION', True), '| Archive:', a.SCRIPT_ARCHIVE_DIR); import sys; sys.exit(0 if (a.MACHINE_NAME and a.SCRIPT_ARCHIVE_DIR) else 2)"
    if errorlevel 1 (
        echo.
        echo  WARNING: MACHINE_NAME and/or SCRIPT_ARCHIVE_DIR are not set in
        echo  addresses.py. The software starts, but Run is blocked until they are.
    )
)

REM -- Step 9: Desktop shortcut (optional) -------------------------------------
echo.
echo [9/9] Desktop shortcut
choice /c YN /m " Create the desktop shortcut MBE_Control_2026 now"
if errorlevel 2 (
    echo  Skipped. You can run create_shortcut.ps1 later.
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1"
)

REM -- Done ---------------------------------------------------------------------
echo.
echo ============================================================
echo   INSTALLATION COMPLETE
echo ============================================================
echo.
echo  To launch MBE Control:
echo    Option A - double-click  run.bat  ^(or the desktop shortcut^)
echo    Option B - open a terminal in this folder, then:
echo               call venv\Scripts\activate
echo               python watchdog.py
echo.
echo  Before the first real run, finish addresses.py ^(see above^).
echo.
pause
