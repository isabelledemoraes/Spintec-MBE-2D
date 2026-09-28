# MBE Instrument Control — Project Memory

## What this project is
A PyQt6 desktop GUI (`GUIv5.py`) for controlling an MBE (Molecular Beam Epitaxy) deposition system.
It talks to Eurotherm temperature controllers via Modbus/RS-485, NI-DAQ digital outputs for shutters,
and a serial pressure gauge. There is also a Plotly Dash live dashboard (`visu_server.py`) launched
as a subprocess.

Entry points:
- `watchdog.py` — **preferred launcher** (restarts on crash, relies on checkpoint for recovery)
- `GUIv5.py` — direct launch (dev/debug)
- `gui_main.py` — module entry point (relative import of GUIv5; use watchdog or GUIv5 directly instead)

## Hardware
| Instrument | Protocol | Port | Notes |
|---|---|---|---|
| Au | Modbus RTU | COM1, slave 1 | Has **critical zone** 1014–1114 °C (near melting point), slow_rate = 3 °C/min |
| CaF2 | Modbus RTU | COM1, slave 1 | High register addresses (102x range) |
| Se1 | Modbus RTU | COM1, slave 7 | Special: temperature_ramp just writes setpoint, no ramp generator |
| Se2 | Modbus RTU | COM1, slave 5 | Same as Se1 |
| Substrate | Modbus RTU | COM1, slave 3 | No shutter |
| SnSe | Modbus RTU | COM1, slave 2 | |
| Pressure | Serial | COM10, 300 baud, 7N2 | Reads `DS IG2` command |
| Shutters (Au/CaF2/Se1/Se2/SnSe) | NI-DAQ DO | Dev1/port0/line 1–6 | |
| Liquid_N2, eGun1, eGun2 | NI-DAQ DO | Dev1/port0/line7, port1/line0–1 | Global shutters |

Simulation mode: `SIMULATION = True` in `GUIv5.py` — uses `SimEurothermController` and
`SimPressureGaugeController` from `sim_controllers.py`. Set to `False` on the instrument PC.

## Key files
| File | Role |
|---|---|
| `addresses.py` | Single source of truth: Modbus register addresses, thresholds, shutter lines, critical zones |
| `eurotherm_controller.py` | Modbus R/W with per-port `threading.Lock`, 3-retry `safe_read/write_register` |
| `pressure_gauge_controller.py` | Serial RS-232, shared `port_locks` per port |
| `shutter_controller.py` | NI-DAQ digital output; persists state to `shutter_state.json` |
| `sim_controllers.py` | Fake Eurotherm (time-based drift) + fake pressure gauge for dev/testing |
| `base_instrument.py` | ABC: `connect`, `disconnect`, `read_value` |
| `GUIv5.py` | `MainWindow` + `InstrumentPanel`; creates controllers, workers, threads; handles close |
| `script_editor_integration.py` | `ScriptEditor` + `ScriptManager`; DSL execution engine |
| `data_updater_threaded.py` | `EurothermWorker` (2 s poll) + `PressureWorker` (1 s poll) via `QThread` |
| `log_manager.py` | CSV logger; Standby mode (60 s interval, daily file) / Growth mode (5 s interval, named file) |
| `checkpoint_manager.py` | JSON checkpoint written before each script statement; survives crashes |
| `visu_server.py` | Plotly Dash dashboard; reads CSV logs; auto-refreshes every 5 s |
| `visu_window.py` | Launched as subprocess by "📊 Dashboard" button |
| `watchdog.py` | Crash-restart wrapper; exit code 0 = clean quit |

## Script DSL (script_editor_integration.py)
Scripts are plain text executed inside a sandboxed `exec()`. Allowed functions only:

```python
power_ramp("Au", target_%, duration_min)
temperature_ramp("Au", target_°C, rate_°C_per_min)
hold(duration_min)
open_shutter("eGun1")
close_shutter("eGun1")
wait_until_temperature("Au", target_°C, tolerance=0.5, timeout=None)
wait_until("YYYY-MM-DD HH:MM")
```

**Instrument locking**: at run-start, all Eurotherm loop IDs used in the script are reserved in
`active_script_locks` (module-level dict + `lock_manager_lock`). If a loop is already locked by
another script, the run aborts. Locks are released in a `finally` block at end of run.

**temperature_ramp behaviour**:
- Fire-and-return: writes first segment to hardware, then immediately returns to script thread.
- Multi-segment ramps (critical zone crossing): a daemon thread (`_continue_segments`) polls PV
  and switches rate/setpoint at each segment boundary.
- Se1/Se2 exception: just writes temperature_setpoint, no ramp generator involvement.
- Critical zone for Au: 1014–1114 °C, 3 °C/min forced through that range.

**power_ramp behaviour**:
- Switches controller to MANUAL mode.
- Enables ramp generator, sets `Ramp_OP` rate (%/s), writes `output_setpoint`.
- Fire-and-return (does NOT block until target is reached).

**Checkpoint recovery**: `checkpoint_manager.save(name, text, stmt_index)` is called before each
statement. On next launch after a crash, `watchdog.py` keeps GUIv5 alive and a resume dialog
(if implemented) can set `_resume_from` on the ScriptEditor.

**Tab close protection**: `ScriptManager.close_tab` silently blocks if `editor.is_active()`.

## GUI structure
- Tab 1: **General View** — grid of `InstrumentPanel` (one per Eurotherm); global shutter frame
- Tab 2: **Scripts** — `ScriptManager` with closable sub-tabs of `ScriptEditor`
- Tab 3: **Pressure** — `PressureGaugePanel` with rolling log-scale plot
- Log output strip at bottom (read-only `QTextEdit`, max 120 px)
- Log mode bar: Standby / Growth + filename + Confirm + 📊 Dashboard button

`InstrumentPanel.set_locked(owner)` — greys out setpoint input, Set button, and shutter button;
shows 🔒 icon with tooltip "Locked by {owner}".

## Data flow
```
QThread → EurothermWorker.run() → data_ready signal
                                    ↓
                          MainWindow.handle_data_update()
                                    ↓
                          InstrumentPanel labels updated
                          latest_data dict updated
                                    ↓ (QTimer 1 s)
                          LogManager.log(latest_data)
                                    ↓
                          CSV file (logs/)
                                    ↓ (Dash polls every 5 s)
                          visu_server.py dashboard
```

## Known patterns / conventions
- All Modbus addresses in `addresses.py` as `(register, decimals)` tuples.
- `read_value(loop_id, name)` and `write_value(loop_id, name, value)` look up address by name.
- `write_register(loop_id, register_name, value)` is used for rate registers.
- Threads are stored as `(QThread, Worker)` pairs in `self.threads`; cleaned up in `closeEvent`.
- `append_log(msg)` writes to the GUI log strip AND to `logs/dialog_YYYYMMDD.txt`.
- Dashboard launched via `subprocess.Popen([sys.executable, "visu_window.py"])`.

## Recent work / open items
<!-- Update this section at the end of each session -->
- Script close prevention: tab close blocked while script active ✅
- Instrument lock UI (🔒 icon + grey out): `set_locked()` on InstrumentPanel ✅
- Completion blocking (block script until all ramp segments physically done): IN PROGRESS
- `wait_until("YYYY-MM-DD HH:MM")` DSL function: blocks script until target datetime, with countdown status label and stop-event support ✅
- NI DAQ USB-6501 output init: `initialize_daq_outputs()` added to `shutter_controller.py`, called first in `GUIv5.py __main__` — sets all 24 lines LOW before any task is created ✅
- Pressure gauge: simulated OK; real COM10 not yet tested end-to-end
- `gui_main.py` uses a relative import `.GUIv5` — will fail if run directly; use `watchdog.py`

## How to start a session
1. Read this file.
2. Ask the user what they want to work on today.
3. Check "Recent work / open items" above for ongoing tasks.
4. Before editing any file, re-read it first — don't rely on memory of this summary.
