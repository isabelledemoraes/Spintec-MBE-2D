# MBE Instrument Control — Project Memory

## What this project is
A PyQt6 desktop GUI (`GUIv5.py`) for controlling an MBE (Molecular Beam Epitaxy) deposition system.
It talks to Eurotherm temperature controllers via Modbus/RS-485, NI-DAQ digital outputs for shutters,
and a serial pressure gauge. There is also a live dashboard (`visu_window.py`, pywebview + Plotly)
launched as a subprocess; `visu_server.py` is an older standalone Plotly Dash version (not launched
by the GUI, not kept in sync).

Entry points:
- `watchdog.py` — **preferred launcher** (restarts on crash; no script resume, the checkpoint is a crash record)
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
| `script_compiler.py` | Compile step: `compile_script()` runs every check in `CHECKS` → `CompileReport` (ERROR/WARNING) |
| `script_archive.py` | Run archive (#9): `save_run()` writes script + compile report before execution; `ArchiveError` blocks Run |
| `connection_monitor.py` | Connection status (#12), no Qt: counts consecutive failed tries per instrument → lost / recovered, banner + log text |
| `data_updater_threaded.py` | `EurothermWorker` (2 s poll) + `PressureWorker` (1 s poll) via `QThread` |
| `log_manager.py` | CSV logger; Standby mode (60 s interval, daily file) / Growth mode (5 s interval, named file) |
| `checkpoint_manager.py` | JSON crash record written before each script statement; `crash_message()` for the startup banner |
| `visu_server.py` | Older standalone Plotly Dash dashboard (manual launch only) |
| `visu_window.py` | Dashboard launched as subprocess by "📊 Dashboard" button; reads CSV logs; refreshes every 5 s; instruments have a Select all / Deselect all toggle; Pressure is not an instrument, it has its own plot ("Show" line) |
| `watchdog.py` | Crash-restart wrapper; exit code 0 = clean quit |

## Script DSL (script_editor_integration.py)
Scripts are plain text executed inside a sandboxed `exec()`. Allowed functions only:

```python
power_ramp("Au", target_%, rate_%_per_s)
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
  and switches rate/setpoint at each segment boundary. A failed PV read never moves on: the next
  segment waits until the PV is readable AND within 3 °C of the previous target (logged once when
  lost, once when back).
- Se1/Se2 exception: just writes temperature_setpoint, no ramp generator involvement.
- Critical zone for Au: 1014–1114 °C, 3 °C/min forced through that range.

**power_ramp behaviour**:
- Switches controller to MANUAL mode.
- Enables ramp generator, writes the script rate (%/s) to `Ramp_OP`, writes `output_setpoint`.
- Fire-and-return (does NOT block until target is reached).

**Crash record** (#10, no resume): `checkpoint_manager.save(name, stmt_index, stmt_count)` is called
before each statement (also stores `saved_at` = statement start; no script text). Cleared on clean
finish, user stop or interlock stop; kept on a crash or a script exception. At startup
`MainWindow._report_previous_crash` logs `crash_message(cp)`, clears the checkpoint and shows a
yellow banner at the top of the General View until **Acknowledge** (logged too). The script is
never reloaded or resumed; `watchdog.py` only restarts the GUI.

**Compile before Run** (issue #4): the Compile button runs `script_compiler.compile_script()`.
Each check is a function `(CompileContext) -> list[CompileMessage(level, line, text)]` registered in
`CHECKS`; limits must come from `ctx.addresses`. The per-tab report panel lists errors, then warnings,
then the summary line. Run is enabled only if the last report has 0 errors AND its `code` equals the
current editor text; `run_script()` re-checks this. Editing the text marks the report outdated.

**Limit checks** (#5): each Eurotherm loop in `addresses.py` has a `"limits"` dict
(`max_temp_rate` °C/min, `power_ramp_allowed` (False for cells), `min_power_rate` %/s, `max_temp` °C,
optional `min_temp` °C; shared values
come from `CELL_LIMITS` / `SUBSTRATE_LIMITS` / `EUROTHERM_LIMITS`, see `config/addresses.py.example`).
A limit missing or `None` for a loop used by the script is an ERROR. `temperature_ramp` rate must be
> 0. Targets of `temperature_ramp` and `wait_until_temperature` are checked against `min/max_temp`.

**Critical zones** (#6): `ramp_planning.plan_ramp_segments(start, target, rate, critical_zone)` is the
single segment planner, used by the runtime `temperature_ramp` and by the compiler. Inside the zone the
rate is `min(rate, slow_rate)`. `script_compiler.temperature_timeline(ctx)` simulates each loop's
temperature command by command (start = live `temperature_pv` via `ctx.controllers`, then previous
target; `power_ramp` or no controller → unknown) and is cached on `ctx.timeline` for reuse by #7.
`check_critical_zones` emits a WARNING per ramp slowed by a zone ("may cross" when start unknown).

**Time estimate** (#7): `script_compiler.time_timeline(ctx)` → `ScriptTiming` (cached on `ctx.timing`,
attached to `CompileReport.timing` only when there are no errors). Clock starts at compile time
(`compile_script(now=...)` for tests). Ramps take 0 s and run in the background in parallel;
only `hold`, `wait_until`, `wait_until_temperature` advance the clock (`ramp_planning.time_to_reach`
for the latter; `timeout` in seconds bounds it). `power_ramp` end = |target − live `output_pv`| / rate.
Each step has a `Duration(minutes, source, note)`; source is `"calculated"` or `"unknown"`; other
sources (#10) go in `DURATION_SOURCES` (only consulted for unknown steps). After an unknown step,
clock times are minimums (`≥`). A new ramp on a loop whose previous ramp may still be running has an
unknown end (runtime replaces the ramp; old `_continue_segments` thread can still override it — open).

**Pressure interlock** (#8): `pressure_interlock.py`. A shutter entry with
`"pressure_interlock": {"max_pressure_mbar": 1.0e-8}` (Liquid_N2) opens only if a fresh gauge read is
strictly below the limit. The check is in `ShutterController.set_shutter` (matched by NI-DAQ **line**, so
aliases are covered), so scripts AND manual buttons pass through it; refusal raises `InterlockError`
(nothing written). Any doubt blocks: no/disconnected gauge, read error, non-finite/≤0 value,
`addresses["Pressure"]["unit"] != "mbar"`, bad limit. In a script: stop at that line, `stop_event` set
(stops ramp segments), checkpoint cleared, message box. Compile: `Level.INFO` line per opening
(ERROR on bad config). `read_value` flushes the serial input first (no stale reply).
Bypass: `"bypass": True` in the same dict (only literal `True`) opens with no pressure check —
Compile WARNING per opening, startup log line, console warning at each bypassed opening.

**Run archive** (#9): `run_script()` recompiles the current text (`compile_current()`); errors → Run
refused, nothing saved. Then `_archive_run()` → `script_archive.save_run()` writes
`YYYY-MM-DD_HH-MM-SS_<name>.txt` (exact executed text, UTF-8, `newline=""`) and `…_<name>_report.txt`
(header: machine, Run time, git version, script name/file; then `format_lines()`)
in `SCRIPT_ARCHIVE_DIR`, BEFORE locks/thread. Settings are top-level in `addresses.py`: `MACHINE_NAME`,
`SCRIPT_ARCHIVE_DIR` (relative = project folder, created if missing); missing/None/empty or any write
error → `ArchiveError` → script NOT started + message box. Files opened in `"x"` mode (never overwrite;
same name + same second → `_2`, `_3` on both); script file removed if the report fails. Name =
`safe_filename(script_name)` (tab name, `Script N` for new tabs, `untitled` if empty). Version =
`git rev-parse --short HEAD` (+`-dirty`) read once at import, else `unknown`. Tests that press Run
must patch `script_editor_integration.addresses_config` to a temp folder.

**Connection check** (#12): no new polling. Each `EurothermWorker` / `PressureWorker` poll cycle
reports one try to `MainWindow.connection_monitor` (success, or failure = first failing read; a gauge
value that is non-finite/≤ 0 fails, same rule as the interlock via `is_valid_pressure`). `MAX_FAILURES`
= 10 consecutive failed polls → LOST (since = first failed try); delay between tries = worker
`interval_s` (2 s Eurotherm, 1 s gauge); a real Eurotherm failed poll itself takes ~2.1 s (3 retries).
Controller init failure → LOST at once. NI-DAQ (`DAQ_NAME`, max 1 failure): startup status =
result of `initialize_daq_outputs()` passed to `MainWindow(daq_init_ok=...)`, then every
`ShutterController.set_shutter` write is reported; `nidaqmx` missing → simulated OK if SIMULATION,
else LOST. The workers' first cycles are the startup check ("Checking instruments..." until every
instrument answered or is lost). The 1 s GUI timer drains events to `append_log` (`[Connection] ...`),
refreshes the red banner and panel marks (red border, ⚠ + last OK). A recovered instrument stays in
the banner until **Acknowledge** (still-lost ones stay). Simulation: `fail = True` on a sim controller
makes it stop answering. Running scripts are NOT affected (detect and alert only).
CSV: every instrument's columns exist from startup (`worker.data_keys`, NaN until the first answer),
so the header never changes; `write_log_entry` writes NaN for an instrument whose last poll failed
(`InstrumentStatus.answering` False — from the first failed poll, not only when LOST).

**Tab close protection**: `ScriptManager.close_tab` silently blocks if `editor.is_active()`.

**Script names**: `script_name` is the lock owner, checkpoint name and archive name, so it is
unique among tabs (`ScriptManager.name_is_free`, case-insensitive) and never changes during a run.
✏️ Rename (`request_rename`, `QInputDialog`) refuses a name used by another tab; button disabled while
running. `set_name()` updates label + tab title (`name_changed` signal). Save renames the tab after the
file (like Load); Load/Save/new-tab clashes get `name (2)`, `name (3)`…

## GUI structure
- Tab 1: **General View** — grid of `InstrumentPanel` (one per Eurotherm); global shutter frame
- Tab 2: **Scripts** — `ScriptManager` with closable sub-tabs of `ScriptEditor`
- Tab 3: **Pressure** — `PressureGaugePanel` with rolling log-scale plot
- Log output strip at bottom (read-only `QTextEdit`, max 120 px)
- Log mode bar: Standby / Growth + filename + Confirm + 📊 Dashboard button

`InstrumentPanel.set_locked(owner)` — greys out setpoint input and Set button (loop lock);
shows 🔒 icon with tooltip "Locked by {owner}". The shutter button is locked separately by
`set_shutter_display(state, owner)`, only when the script calls open/close_shutter on it.

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
                                    ↓ (dashboard polls every 5 s)
                          visu_window.py dashboard
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
- Compile framework (#4): Check + preview graph removed, Compile + per-tab report added ✅; next: #6–#9 plug into `CHECKS`
- Ramp limit checks (#5): `check_ramp_rates` + `check_temperature_targets`; `power_ramp` 3rd arg is
  now a rate in %/s ✅ — real `max_temp`/`min_temp` values still to be set in each machine's `addresses.py`
- Critical zone warnings (#6): shared `ramp_planning.py` + `temperature_timeline` + `check_critical_zones` ✅;
  runtime zone rate is now `min(rate, slow_rate)` — not yet tested on real hardware
- Time estimate (#7): per-line durations, total, script end + last ramp end in the Compile report ✅;
  report panel height raised to 200 px. Open: same-loop ramp replacement at runtime (stale
  `_continue_segments` thread and `_hw_wait_items` entry of the replaced ramp)
- Completion blocking (block script until all ramp segments physically done): IN PROGRESS
- `wait_until("YYYY-MM-DD HH:MM")` DSL function: blocks script until target datetime, with countdown status label and stop-event support ✅
- NI DAQ USB-6501 output init: `initialize_daq_outputs()` added to `shutter_controller.py`, called first in `GUIv5.py __main__` — sets all 24 lines LOW before any task is created ✅
- Shutter status sync (issue #1): one shared `ShutterController` (GUI + scripts); General View
  refreshes shutter state every 1 s; shutters used by a running script are reserved in
  `active_shutter_locks` (separate from loop locks) and only those buttons are disabled; a failed
  shutter write stops the script ✅ — tested in simulation, not yet on real NI-DAQ
- Pressure interlock (#8) ✅ in simulation — before real use: set the IG1 controller to mbar and
  `"unit": "mbar"` + `pressure_interlock` in each machine's `addresses.py`; not yet tested on real gauge
- Run archive (#9) ✅ — before real use: set `MACHINE_NAME` and `SCRIPT_ARCHIVE_DIR` in each machine's
  `addresses.py` (Run is blocked until then); git may be absent on instrument PCs → version `unknown`
- Auto-resume removed (#10): "Session Recovery" dialog, `_resume_from`, `resume_from` in
  `script_archive` gone; crash banner in General View instead ✅. Open: a script *exception* (not a
  crash) also keeps the checkpoint, so the next start reports it as "ended unexpectedly"
- UI cleanup (#11): dashboard Select/Deselect all toggle + Pressure out of the instrument line
  (`visu_window.py` only); Script page "Follow-Up Plot" button removed with its `hold()` data
  collection ✅
- Connection check (#12) ✅ in simulation — not yet tested on real hardware. CSV logs NaN while an
  instrument does not answer ✅; `_continue_segments` no longer jumps to the next segment on a read
  error ✅. Open: Compile does not warn about lost instruments; other script functions on a lost
  instrument unchanged (`wait_until_temperature` returns on a read error and the script continues,
  `power_ramp` logs and continues)
- Pressure gauge: simulated OK; real COM10 not yet tested end-to-end
- `gui_main.py` uses a relative import `.GUIv5` — will fail if run directly; use `watchdog.py`

## How to start a session
1. Read this file.
2. Ask the user what they want to work on today.
3. Check "Recent work / open items" above for ongoing tasks.
4. Before editing any file, re-read it first — don't rely on memory of this summary.
