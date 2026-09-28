@echo off
setlocal EnableDelayedExpansion
title MBE Control — Windows Installer

echo.
echo ============================================================
echo   MBE Control Software — Windows Installation
echo ============================================================
echo.

REM ── Step 1: Check Python ─────────────────────────────────────────────────────
echo [1/8] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: Python not found on PATH.
    echo.
    echo  Please install Python 3.8 or newer from:
    echo    https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: During installation, tick "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  Python %PYVER% found. OK.

REM ── Step 2: Create virtual environment ───────────────────────────────────────
echo.
echo [2/8] Creating virtual environment (venv)...
if exist venv\ (
    echo  Virtual environment already exists — skipping creation.
) else (
    python -m venv venv
    if errorlevel 1 (
        echo  ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  Virtual environment created. OK.
)

REM ── Step 3: Activate venv ────────────────────────────────────────────────────
echo.
echo [3/8] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo  ERROR: Could not activate virtual environment.
    pause
    exit /b 1
)
echo  Virtual environment active. OK.

REM ── Step 4: Upgrade pip ──────────────────────────────────────────────────────
echo.
echo [4/8] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo  pip upgraded. OK.

REM ── Step 5: Install requirements ─────────────────────────────────────────────
echo.
echo [5/8] Installing Python packages (this may take a few minutes)...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: Package installation failed. Check your internet connection
    echo  and try running this script again.
    pause
    exit /b 1
)
echo  All packages installed. OK.

REM ── Step 6: Check NI-DAQmx driver ────────────────────────────────────────────
echo.
echo [6/8] Checking NI-DAQmx driver (for shutter control)...
python -c "import nidaqmx" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  WARNING: nidaqmx Python package not found.
    echo.
    echo  The shutter control (Au, CaF2, Se, SnSe shutters) requires
    echo  the NI-DAQmx driver AND Python binding to be installed.
    echo.
    echo  Without it the software will still run — shutters will be
    echo  SIMULATED (no real shutter movement).
    echo.
    echo  To enable real shutter control:
    echo    1. Install NI-DAQmx driver from:
    echo       https://www.ni.com/en/support/downloads/drivers/download.ni-daqmx.html
    echo    2. Then run:  pip install nidaqmx
    echo.
) else (
    echo  nidaqmx found. Shutter control enabled. OK.
)

REM ── Step 7: Configure addresses ──────────────────────────────────────────────
echo.
echo [7/8] Checking instrument configuration...
if exist addresses.py (
    echo  addresses.py found. OK.
) else (
    if exist config\addresses.py.example (
        copy config\addresses.py.example addresses.py >nul
        echo.
        echo  NOTICE: addresses.py was not found — a template has been copied.
        echo  You MUST edit addresses.py and set your COM ports and device names
        echo  before the software can talk to instruments.
        echo.
        echo  Open addresses.py in any text editor and update:
        echo    EUROTHERM_PORT  ^= COM port for Eurotherm (e.g. COM1)
        echo    PRESSURE_PORT   ^= COM port for pressure gauge (e.g. COM10)
        echo    NI_DEVICE       ^= NI DAQmx device name (e.g. Dev1)
        echo.
        echo  Use Device Manager to find COM port numbers.
        echo.
    ) else (
        echo  WARNING: addresses.py not found and no example template available.
        echo  The software will not communicate with instruments until
        echo  a valid addresses.py is present.
    )
)

REM ── Step 8: Sanity check ──────────────────────────────────────────────────────
echo.
echo [8/8] Running sanity check...
python -c "import PyQt6; import pyqtgraph; import numpy; import minimalmodbus; import plotly; import pandas; import webview; print('All core packages OK')"
if errorlevel 1 (
    echo  ERROR: Sanity check failed — one or more packages did not import correctly.
    pause
    exit /b 1
)

REM ── Done ──────────────────────────────────────────────────────────────────────
echo.
echo ============================================================
echo   INSTALLATION COMPLETE
echo ============================================================
echo.
echo  To launch MBE Control:
echo    Option A — double-click  run.bat
echo    Option B — open a terminal, then:
echo               call venv\Scripts\activate
echo               python watchdog.py
echo.
echo  Remember to configure addresses.py with your instrument
echo  COM ports before connecting to real hardware.
echo.
pause
