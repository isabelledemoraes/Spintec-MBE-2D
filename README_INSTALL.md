# MBE Control — Installation Guide (Windows)

## Prerequisites

| Requirement | Minimum | Where to get it |
|---|---|---|
| Python | 3.8 or newer | https://www.python.org/downloads/ |
| Windows | 10 or 11 | — |
| NI-DAQmx driver | Latest | https://www.ni.com/en/support/downloads/drivers/download.ni-daqmx.html |

> **Python install tip:** During Python installation, tick **"Add Python to PATH"**.
> Without this, the installer script cannot find Python.

---

## Quick Install (5 minutes)

1. Copy the project folder to the instrument computer (USB drive or network share).
2. Double-click **`install_windows.bat`**.
3. Follow the on-screen prompts — it will:
   - Create an isolated Python environment
   - Install all required packages automatically
   - Warn you about any missing drivers
4. Edit **`addresses.py`** with your instrument COM ports (see below).
5. Double-click **`run.bat`** to launch.

---

## Configuring Instrument Addresses

### Step 1 — Find your COM ports

1. Press **Win + X** → **Device Manager**
2. Expand **Ports (COM & LPT)**
3. Look for entries like `USB Serial Port (COM3)` or `USB-SERIAL CH340 (COM7)`
4. Note down the COM number for each instrument

Typical setup:
- **Eurotherm bus** → COM1 (or whichever COM port the RS-485 adapter is on)
- **Pressure gauge** → COM10 (or your USB serial adapter's port)

### Step 2 — Find your NI device name

1. Open **NI MAX** (NI Measurement & Automation Explorer)
2. Expand **My System → Devices and Interfaces**
3. Find your DAQPad / NI USB device — it will show a name like **Dev1**

### Step 3 — Edit addresses.py

Open `addresses.py` in Notepad or any text editor and update the values:

```python
# For each Eurotherm controller, change the COM port:
"device": ("COM1", 1),   # ("your_port", modbus_slave_id)

# For the pressure gauge:
"device": ("COM10", 300),  # ("your_port", baud_rate)

# For shutters (NI-DAQmx):
"shutter_line": "Dev1/port0/line2"   # "DevX/portY/lineZ"
```

### Step 4 — Machine-specific settings

Everything that differs between machines lives in `addresses.py`, so the
code itself is identical on every PC. See `config/addresses.py.example`
for details.

| Setting | Where | Default |
|---|---|---|
| `SIMULATION = False` | top of the file | `True` (simulated instruments) |
| `"command": "DS IG1 \r\n"` | `"Pressure"` entry | `DS IG1` |
| `"script_direct_setpoint": True` | an Eurotherm loop | off |
| `"script_keep_ramp_generator": True` | an Eurotherm loop | off |

- **`script_direct_setpoint`** — in scripts, `temperature_ramp()` only writes
  the setpoint, like the GUI **Set** button (mbe1: Se1, Se2 — mbe2:
  Te-Cracker, Te-Tube).
- **`script_keep_ramp_generator`** — in scripts, `temperature_ramp()` skips
  the "ramp generator OFF" safety interlock and restores its previous state
  afterwards (mbe2: Te-Res).

---

## How to Launch

**Normal launch:**
```
Double-click run.bat
```

**From a terminal:**
```
call venv\Scripts\activate
python watchdog.py
```

> `watchdog.py` automatically restarts the software if it crashes, and
> offers to resume any script that was running at the time of the crash.

**Simulation mode (no instruments needed):**
Set `SIMULATION = True` at the top of `addresses.py` (it is also the default
when the line is missing). The window title then shows **[SIMULATION]** and
the GUI runs with simulated instrument data — useful for testing scripts offline.

---

## Shutter Control (NI-DAQmx)

The shutter control requires:
1. The **NI-DAQmx** hardware driver (from NI website above)
2. The Python binding: `pip install nidaqmx`

If `nidaqmx` is not installed, the software still runs — shutters are silently
simulated (no real movement).

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Python not found` | Python not on PATH | Reinstall Python, tick "Add to PATH" |
| `No module named PyQt6` | Packages not installed | Run `install_windows.bat` again |
| `FileNotFoundError: COM1` | Wrong COM port | Check Device Manager, update `addresses.py` |
| `minimalmodbus.NoResponseError` | Eurotherm not responding | Check RS-485 cable, Modbus slave address |
| `Serial timeout on COM10` | Pressure gauge off or wrong port | Check gauge power, update `addresses.py` |
| Software crashes on open | Missing `addresses.py` | Copy `config/addresses.py.example` → `addresses.py` and fill in ports |
| Shutters not moving | nidaqmx not installed | Install NI-DAQmx driver + `pip install nidaqmx` |

---

## File Overview

```
MBE2026/
├── GUIv5.py                   Main application
├── watchdog.py                Auto-restart launcher (use this to run)
├── visu_window.py             Live dashboard (launched by "📊 Dashboard" button)
├── run.bat                    Windows launcher (double-click)
├── install_windows.bat        One-click installer
├── requirements.txt           Python package list
├── addresses.py               YOUR instrument configuration (not in Git)
├── config/
│   └── addresses.py.example   Template — copy to addresses.py
├── scripts/                   Saved experiment scripts
└── logs/                      Instrument logs (auto-created)
```

---

## Updating the Software

If you receive an updated version of the project:

1. Replace the project folder contents (or `git pull` if using Git)
2. Run `install_windows.bat` again — it will update packages if needed
3. Your `addresses.py` is preserved (it is never overwritten)