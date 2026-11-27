# MBE Control — Installation Guide (Windows)

## Prerequisites

| Requirement | Minimum | Where to get it |
|---|---|---|
| Python | 3.11 or newer (64-bit) | https://www.python.org/downloads/ |
| Windows | 10 or 11 | — |
| NI-DAQmx driver | Latest | https://www.ni.com/en/support/downloads/drivers/download.ni-daqmx.html |

> **Python install tip:** During Python installation, tick **"Add Python to PATH"**.
> Without this, the installer script cannot find Python.

---

## Quick Install (5 minutes)

1. Copy the project folder to the instrument computer (USB drive, network
   share or `git clone`). Do **not** copy a `venv` folder from another PC:
   the installer creates a fresh one.
2. Double-click **`install_windows.bat`**. It will:
   - check Python (3.11+), create the isolated environment `venv` in the project folder
   - install the exact tested versions from `requirements-lock.txt` (else `requirements.txt`) into it (nothing is installed globally)
   - check the NI-DAQmx driver, create `config\machine.yaml` if it is missing
     (from a former `addresses.py` if there is one, else from the example, in
     simulation mode), run an import check and offer to create the desktop shortcut
3. Double-click **`run.bat`** (or the desktop shortcut) to launch.
4. Open the **⚙ Configuration** tab and set the machine up (see the checklist below).

To update the tested versions later, on the tested PC run `python -m pip freeze > requirements-lock.txt` and commit it.

### No internet on the instrument PC?

1. On a PC **with** internet and the **same Python version** (64-bit) as the
   instrument PC, double-click **`make_offline_wheels.bat`**. It fills a `wheels` folder.
2. Copy the whole project folder, including `wheels`, to the instrument PC.
3. Run `install_windows.bat` there: when `wheels` exists, it installs from it only.

### Before the first real run: configuration checklist

The configuration of a machine is one file, **`config\machine.yaml`**. It is
machine-specific and not in Git: never copy the one from a test laptop.
It is edited from the program, in the **⚙ Configuration** tab (password
protected), or by hand with a text editor (`config\machine.example.yaml`
explains every key).

- [ ] **Machine → Simulation mode** unchecked (checked = fake instruments)
- [ ] **Machine → Machine name** and **Archive folder**: **Run is blocked until both are set**
- [ ] COM port, Modbus slave and NI-DAQ line of every cell; NI device name (Dev1...)
- [ ] **Max temperature** (and min) of every cell: scripts and "Set temperature"
      are refused on a cell without it
- [ ] Pressure gauge set to mbar, unit `mbar`, and the pressure interlock of Liquid_N2
- [ ] NI-DAQmx driver installed (the installer warns if not)

A fresh install starts in simulation mode, so the program can be checked
without touching the instruments. Uncheck it when the configuration is done.

---

## Configuring the Machine

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

### Step 3 — Open the ⚙ Configuration tab

1. Start MBE Control and click the **⚙ Configuration** tab.
2. First access: choose the password that protects this area. Afterwards the
   password is asked each time; the tab locks itself when you leave it.
3. Pick an entry on the left and edit it on the right:

| Entry | What you set |
|---|---|
| **Machine** | machine name, simulation mode, run archive folder, absolute max ramp rate |
| **Pressure gauge** | enabled, serial port and frame, read command (`DS IG1 \r\n`), unit |
| **NI-DAQ** | device name (Dev1), number of ports set LOW at startup |
| **Cells** (one per Eurotherm) | name, COM port, Modbus slave, shutter line, limits, critical zone, Modbus registers |
| **Shutters** | name, button label, NI-DAQ line, pressure interlock (limit, bypass) |

   **+ Cell** / **+ Shutter** add an entry, **Duplicate** copies the selected
   one, **↑ ↓** change the display order, **Delete** removes it.
4. **Save** checks every value (nothing is written if one is wrong), keeps the
   previous file as `machine.yaml.bak` and logs each changed value.
   **Save and restart** also restarts the program: a change takes effect at
   the next start. Restart is refused while a script is running.

Notes:

- The **name** of a cell is what scripts use (`temperature_ramp("Au", ...)`) and
  the name of its CSV columns: renaming a cell means updating the scripts.
- **Write the setpoint directly** (cell → Temperature changes): a temperature
  change only writes the setpoint, no ramp rate (mbe1: Se1, Se2 — mbe2:
  Te-Cracker, Te-Tube).
- **Keep the ramp generator state**: a ramp skips the "ramp generator OFF"
  safety step and restores the previous state afterwards (mbe2: Te-Res).
- Switching simulation off, bypassing or removing a pressure interlock must
  be confirmed when saving.

### Forgotten password

Only a salted hash of the password is stored, in `config\machine.yaml`. To
reset it, close the program, open that file in a text editor and set

```yaml
security:
  password: null
```

The program asks for a new password at the next access. (The password keeps
operators out of the settings; it does not protect the file itself.)

### Coming from a version with `addresses.py`

Nothing to do: at the first start (or when `install_windows.bat` runs), an
existing `addresses.py` is converted to `config\machine.yaml` with the same
values. `addresses.py` is left untouched and is no longer read; keep it as a
backup or delete it.

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

> `watchdog.py` automatically restarts the software if it crashes. If a
> script was running, the General View then shows which script and statement
> were interrupted. The script is **not** resumed: check the machine state
> and start a new script if needed.

**Simulation mode (no instruments needed):**
Check **Simulation mode** in ⚙ Configuration → Machine (`machine.simulation: true`
in `config\machine.yaml`; it is the default of a fresh install). The window
title then shows **[SIMULATION]**, the General View shows a SIMULATION mark, and
the GUI runs with simulated instrument data — useful for testing scripts offline.

**General View:** one card per cell with the measured temperature in large
digits, setpoint, ramp rate and power. **Set temperature**: type a target and a
rate, the line below says what will happen (duration, critical zone) or why it
is refused, then **Set**. The rules are those of scripts (`temperature_ramp`):
limits of the cell, slow rate inside the critical zone. A cell used by a
running script cannot be changed by hand.

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
| `No module named PyQt6` | Packages not installed or wrong Python | Run `install_windows.bat`, then start with `run.bat` (it uses `venv`) |
| `No module named yaml` | PyYAML not installed (older `venv`) | Run `install_windows.bat` again |
| "configuration error" at startup | `config\machine.yaml` is not valid YAML | Fix the file, or restore `machine.yaml.bak` |
| Run button does nothing / blocked | machine name / archive folder not set | ⚙ Configuration → Machine |
| "Set" stays grey in the General View | no max temperature for this cell, or value out of limits | Read the line under the fields; ⚙ Configuration → cell → Limits |
| `FileNotFoundError: COM1` | Wrong COM port | Check Device Manager, ⚙ Configuration → cell |
| `minimalmodbus.NoResponseError` | Eurotherm not responding | Check RS-485 cable, Modbus slave address |
| `Serial timeout on COM10` | Pressure gauge off or wrong port | Check gauge power, ⚙ Configuration → Pressure gauge |
| Configuration password forgotten | — | See "Forgotten password" above |
| Shutters not moving | nidaqmx not installed | Install NI-DAQmx driver + `pip install nidaqmx` |

---

## File Overview

```
MBE2026/
├── GUIv5.py                   Main application
├── watchdog.py                Auto-restart launcher (use this to run)
├── visu_window.py             Live dashboard (launched by "📊 Dashboard" button)
├── run.bat                    Windows launcher (double-click)
├── install_windows.bat        One-click installer (creates venv\)
├── make_offline_wheels.bat    Optional: packages for a PC without internet
├── create_shortcut.ps1        Desktop shortcut with the MBE icon
├── mbe_icon.ico               Application icon
├── venv/                      Python environment (created by the installer, not in Git)
├── requirements.txt           Python package list
├── config/
│   ├── machine.yaml           YOUR machine configuration (not in Git)
│   └── machine.example.yaml   Commented template
├── scripts/                   Saved experiment scripts
└── logs/                      Instrument logs (auto-created)
```

---

## Updating the Software

If you receive an updated version of the project:

1. Replace the project folder contents (or `git pull` if using Git)
2. Run `install_windows.bat` again: it reuses `venv` and updates packages if needed
3. Your `config\machine.yaml` is preserved (it is never overwritten by an update)