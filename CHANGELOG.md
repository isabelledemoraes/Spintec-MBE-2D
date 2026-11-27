# Changelog

All notable changes to MBE Control are documented here.

Format: `[version] — date — description`

---

## [Unreleased]

### Added
- **Machine configuration in YAML** — everything machine-specific (cell names, COM ports,
  Modbus slaves and registers, limits, critical zones, shutter lines and labels, pressure
  gauge, NI-DAQ device, simulation mode, machine name, archive folder) is in
  `config/machine.yaml` (`machine_config.py`); the code is generic. A former `addresses.py`
  is converted automatically at the first start and left untouched.
- **⚙ Configuration tab** — password-protected editor of `config/machine.yaml`: add / rename /
  duplicate / reorder / delete cells and shutters, edit every value in forms. Save validates
  everything, keeps the previous file as `.bak`, logs each changed value; "Save and restart"
  applies it (refused while a script runs). The tab locks itself when left.
- **Set temperature** on each General View card: target + rate, checked by the script compiler
  (limits, critical zone, time estimate shown before applying) and executed by the same
  sequence as `temperature_ramp` (`temperature_control.py`).

### Changed
- **General View redesigned** — one card per cell with the measured temperature in large
  digits, name, mode, heating / cooling / at setpoint, setpoint, ramp rate and power bar;
  chamber pressure in the header; cards reflow with the window width; shutters lit green when
  open. The "Details / Hide" section is gone.
- Shutters that do not belong to a cell are no longer hardcoded (Liquid N2, e-guns): one button
  per shutter of the configuration, with its own label.
- The absolute cap on temperature ramp rates (20 °C/min), the Eurotherm baud rate, the gauge
  serial frame and the NI device name come from the configuration.
- `watchdog.py` restarts the program at once when it exits with code 42 (restart asked from the
  Configuration tab); `install_windows.bat` checks / creates `config\machine.yaml`.
- New dependency: `PyYAML`.

### Removed
- `config/addresses.py.example` (replaced by `config/machine.example.yaml`).
- **Script resume after a crash** (#10) — the "Session Recovery" dialog is gone. After a crash
  during a script, the General View shows which script and statement were interrupted until
  acknowledged; the event is logged. The script is not reloaded or resumed.
- **Follow-Up Plot button** (#11) on the Script page, with its data collection during `hold()`
  (no more Modbus reads every second during a hold).

### Changed
- **Dashboard** (#11) — "Select all / Deselect all" toggle button for the instruments;
  Pressure is no longer in the instrument line, it keeps its own plot ("Pressure" in the Show line).

---

## [0.4.0] — 2026-02-20

### Added
- **Windows installer** (`install_windows.bat`) — one-click setup for fresh machines
- **`requirements.txt`** — pinned package versions for reproducible installs
- **`run.bat`** — simple Windows double-click launcher
- **`config/addresses.py.example`** — template for instrument address configuration
- **`README_INSTALL.md`** — non-technical installation and configuration guide
- **`CHANGELOG.md`** and **`COMMIT_GUIDE.md`** — project documentation

---

## [0.3.0] — 2026-02-20

### Added
- **Sync Ramp Calculator tab** (`⏱ Sync Ramp`) — computes staggered `hold()` delays
  so multiple evaporation cells all reach their target temperature at the same wall-clock time
- **Target finish time input (HH:MM)** — user sets desired end time; delays are baked in
  at script generation time with `hold(X)   # wait until HH:MM` comments
- **Material profiles system** — JSON config file (`config/material_profiles.json`),
  extensible without touching code
- **Critical zone support for Au** — segmented ramp calculation (normal → slow → normal)
  through the 1014–1114 °C zone around the Au melting point; other materials use simple linear calc
- **Visual timeline preview** — QPainter Gantt chart; blue = normal ramp, orange = critical zone
- **Script generator** — per-tab copy output with start-time comments
- **`⚡ Min` button** — auto-sets target time to earliest possible finish

### Fixed
- `addresses.py` is the single source of truth for critical zone data (no duplication in JSON)

---

## [0.2.0] — 2026-02-19

### Added
- **Simulation mode** (`SIMULATION = True` in `GUIv5.py`) — runs the full GUI with
  simulated Eurotherm and pressure gauge data; no serial hardware required
- `SimEurothermController` — in-memory controller with `_tick()` drift logic
- `SimPressureGaugeController` — returns a fixed simulated pressure
- **Crash recovery system** — `watchdog.py` auto-restarts the app after a crash;
  `checkpoint_manager.py` saves progress statement-by-statement so scripts resume where they left off
- **Logs tab** — visualizes instrument log files directly in the GUI
- **Script statement-by-statement execution** — scripts checkpoint before each statement,
  enabling recovery after a mid-script crash

### Fixed
- Pressure panel moved to its own dedicated tab (was crammed into General View)
- Pressure plot X-axis now shows wall-clock time (HH:MM:SS) using `DateAxisItem`
- Live plot buffer changed from unbounded list to `deque(maxlen=3600)` — prevents memory freeze
- Script window cannot be reopened after closing the last tab (auto-creates a new empty tab)
- Temperature ramp preview now renders segmented ramp curves in the Check plot
- Eurotherm layout wrapped in `QScrollArea` — scales correctly for 6+ controllers
- `closeEvent` now stops all running script threads before exiting (previously scripts
  continued running in the background after the window was closed)

---

## [0.1.0] — 2025-11-01

### Added
- Initial instrument control platform
- Eurotherm temperature controller panels (auto / manual mode, live plots)
- Pressure monitoring (Bayard-Alpert gauge via serial ASCII)
- Shutter control (NI-DAQmx digital output, silently simulated if driver absent)
- Script execution engine — sequential per-tab, parallel across tabs
- Built-in script commands: `temperature_ramp()`, `power_ramp()`, `hold()`,
  `open_shutter()`, `close_shutter()`, `wait_until_temperature()`
- Follow-up plot after script completion (power or temperature trace)
