import threading
import time
import io
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QTextEdit, QHBoxLayout, QTabWidget, QFileDialog, QMessageBox,
    QInputDialog
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QTextCursor, QTextCharFormat, QColor, QFont
from datetime import datetime, timedelta
import os
import ast
from html import escape
import checkpoint_manager
import script_archive
import addresses as addresses_config   # module-level settings (MACHINE_NAME, SCRIPT_ARCHIVE_DIR)
from addresses import addresses
from script_compiler import compile_script, Level
from ramp_planning import plan_ramp_segments
from pressure_interlock import InterlockError
from script_help import ScriptHelpDialog


def _unique_name(name: str, is_free) -> str:
    """`name`, or `name (2)`, `name (3)`... until is_free(candidate) is True."""
    candidate, n = name, 2
    while not is_free(candidate):
        candidate, n = f"{name} ({n})", n + 1
    return candidate


def _loop_flag(loop_id, key):
    """Per-loop boolean option from addresses.py (False when absent)."""
    return bool(addresses.get(loop_id, {}).get(key, False))


# In script_editor_integration.py, at the top level

# This dictionary will store an owner id for each loop_id that is currently in use.
# e.g., {"Au": "Script 1", "Se1": "Script 2"}
active_script_locks = {}

# Same idea for shutters controlled by open_shutter/close_shutter in a running script.
# Kept separate from loop locks: loop "Au" and shutter "Au" share a name but are
# locked independently. e.g., {"eGun1": "Script 1", "Au": "Script 2"}
active_shutter_locks = {}

# A lock to prevent race conditions when accessing both dictionaries
lock_manager_lock = threading.Lock()


_SHUTTER_FUNCS = ("open_shutter", "close_shutter")


def _script_ids(code: str) -> tuple[set[str], set[str]]:
    """
    Split the literal first arguments of script calls into
    (IDs used by loop functions, IDs used by open_shutter/close_shutter),
    so that open_shutter("Au") locks the Au shutter but not the Au loop.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set(), set()
    loop_ids, shutter_ids = set(), set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            target = shutter_ids if node.func.id in _SHUTTER_FUNCS else loop_ids
            target.add(node.args[0].value)
    return loop_ids, shutter_ids

def _try_acquire_loops(owner: str, loops: set[str]) -> bool:
    """Return True iff all loops were reserved for this owner."""
    with lock_manager_lock:
        # check conflicts
        for lp in loops:
            if lp in active_script_locks and active_script_locks[lp] != owner:
                return False
        # reserve them (idempotent for same owner)
        for lp in loops:
            active_script_locks[lp] = owner
        return True

def _release_loops(owner: str):
    """Release any loops and shutters owned by this owner."""
    with lock_manager_lock:
        to_free = [lp for lp, who in active_script_locks.items() if who == owner]
        for lp in to_free:
            active_script_locks.pop(lp, None)
        to_free = [sid for sid, who in active_shutter_locks.items() if who == owner]
        for sid in to_free:
            active_shutter_locks.pop(sid, None)



class ScriptEditor(QWidget):
    # Emitted from the runner thread (cross-thread safe).
    # (current_stmt_idx, total_stmts)  — current_idx == -1 means reset/done.
    _stmt_highlight = pyqtSignal(int, int)
    # Emitted to update the hardware-wait status label ("" to clear).
    _hw_status = pyqtSignal(str)
    # Emitted when the pressure interlock stops the script (message to show).
    _interlock_alert = pyqtSignal(str)
    # Emitted with the new name after a rename (the tab title follows it).
    name_changed = pyqtSignal(str)

    def __init__(self, script_name, log_callback, controllers=None, shutter_ctrl=None,
                 open_script_callback=None, name_is_free=None):
        super().__init__()
        # The name is also the owner of the loop/shutter locks and of the checkpoint,
        # so it must be unique among the open tabs and never change during a run.
        self.script_name = script_name
        self.log_callback = log_callback
        # Called as open_script_callback(filepath, content) by load_script to open the
        # file in a new tab; if None, the file is loaded into this editor instead.
        self.open_script_callback = open_script_callback
        # Called as name_is_free(name, self) -> bool; if None, every name is free.
        self.name_is_free = name_is_free
        self.shared_controllers = controllers if controllers is not None else {}
        # Shared with the General View so shutter buttons follow script actions.
        self.shutter_ctrl = shutter_ctrl
        self.thread = None
        self.stop_event = threading.Event()
        self._hw_wait_items = []  # [(loop_id, finish_datetime), ...] populated by temperature_ramp

        self.editor = QPlainTextEdit()

        self.compile_button = QPushButton("Compile")
        self.compile_button.clicked.connect(self.compile_current)

        # Run is enabled only after a compilation with no errors of the current text.
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self.run_script)
        self.run_button.setEnabled(False)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_script)

        self.save_button = QPushButton("💾 Save Script")
        self.save_button.clicked.connect(self.save_script)

        self.load_button = QPushButton("📂 Load Script")
        self.load_button.clicked.connect(self.load_script)

        self.help_button = QPushButton("❓ Help")
        self.help_button.clicked.connect(self.show_help)
        self._help_dialog = None

        self._stmt_ranges = []   # [(start_line_0based, end_line_0based), ...]
        self._stmt_highlight.connect(self._apply_statement_colors)
        self._hw_label = QLabel("")
        self._hw_status.connect(self._hw_label.setText)
        self._interlock_alert.connect(self._show_interlock_alert)

        # Per-tab compilation state and report panel.
        self._last_report = None      # CompileReport of the last compilation
        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setMaximumHeight(200)
        self.report_view.setPlaceholderText("Not compiled. Click Compile to check the script.")
        self.editor.textChanged.connect(self._on_text_changed)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.compile_button)
        btn_layout.addWidget(self.run_button)
        btn_layout.addWidget(self.stop_button)
        btn_layout.addWidget(self.save_button)
        btn_layout.addWidget(self.load_button)
        btn_layout.addWidget(self.help_button)

        self.name_label = QLabel()
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        font = self.name_label.font()
        font.setBold(True)
        self.name_label.setFont(font)
        self.name_label.setText(script_name)
        self.rename_button = QPushButton("✏️ Rename")
        self.rename_button.clicked.connect(self.request_rename)
        name_layout = QHBoxLayout()
        name_layout.addWidget(self.name_label)
        name_layout.addWidget(self.rename_button)
        name_layout.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(name_layout)
        layout.addLayout(btn_layout)
        layout.addWidget(self.editor)
        layout.addWidget(self.report_view)
        layout.addWidget(self._hw_label)
        self.setLayout(layout)

    def run_script(self):
        if self.thread and self.thread.is_alive():
            self.log_callback(f"[{self.script_name}] Already running.")
            return
        if not self._is_compiled_ok():
            self._update_run_enabled()
            self.log_callback(
                f"[{self.script_name}] Run refused: compile the script without errors first."
            )
            return

        # Recompile now: the archived report is the state at Run, not at the last Compile.
        self.compile_current()
        if not self._last_report.ok:
            self.log_callback(
                f"[{self.script_name}] Run refused: the script no longer compiles without errors."
            )
            return
        code = self._last_report.code
        # Save script + report before anything starts (#9); no archive, no run.
        if not self._archive_run(code):
            return

        self.stop_event.clear()
        self._hw_wait_items = []

        # Parse statement line ranges for the colour highlighter (0-based).
        try:
            _tree = ast.parse(code)
            self._stmt_ranges = [
                (n.lineno - 1,
                 getattr(n, "end_lineno", n.lineno) - 1)
                for n in _tree.body
            ]
        except SyntaxError:
            self._stmt_ranges = []

        # Lock the editor (and recompilation) while the script is running.
        self.editor.setReadOnly(True)
        self.compile_button.setEnabled(False)
        self.rename_button.setEnabled(False)

        owner_id = f"{self.script_name}"

        def runner():
            from addresses import addresses

            # --- START OF NEW LOCKING LOGIC ---

            # IDs used as first argument in calls like power_ramp("Au", ...);
            # shutter calls are collected separately (shutter locks below).
            loop_call_ids, used_shutters = _script_ids(code)

            # Consider only real Eurotherm loops: entries that have a temperature_pv register.
            used_loops = {
                loop_id for loop_id in loop_call_ids
                if loop_id in addresses
                and isinstance(addresses[loop_id], dict)
                and "temperature_pv" in addresses[loop_id]
            }

            if not used_loops:
                # No Eurotherm loops in this script -> maybe only shutters/holds.
                # In this case we simply do not take any instrument locks.
                self.log_callback(
                    f"[{self.script_name}] No Eurotherm loops to reserve. Running without loop locks."
                )
                acquired_locks = []
            else:
                self.log_callback(
                    f"[{self.script_name}] Script requires access to: {', '.join(sorted(used_loops))}"
                )

                acquired_locks = []
                with lock_manager_lock:
                    for loop in used_loops:
                        if loop in active_script_locks:
                            self.log_callback(
                                f"[{self.script_name}] FATAL ERROR: Instrument '{loop}' is already in use by another script. Aborting."
                            )
                            # Release any locks we might have acquired before failing
                            for locked_loop in acquired_locks:
                                del active_script_locks[locked_loop]
                            self._stmt_highlight.emit(-1, 0)  # unlock the editor
                            return

                        # Reserve this loop for the current script
                        active_script_locks[loop] = owner_id
                        acquired_locks.append(loop)

            # Reserve only the shutters this script opens/closes; their
            # General View buttons are disabled until the run ends.
            acquired_shutters = []
            if used_shutters:
                with lock_manager_lock:
                    busy = sorted(s for s in used_shutters if s in active_shutter_locks)
                    if busy:
                        self.log_callback(
                            f"[{self.script_name}] FATAL ERROR: Shutter(s) {', '.join(busy)} "
                            f"already controlled by another script. Aborting."
                        )
                        for locked_loop in acquired_locks:
                            active_script_locks.pop(locked_loop, None)
                        self._stmt_highlight.emit(-1, 0)  # unlock the editor
                        return
                    for sid in used_shutters:
                        active_shutter_locks[sid] = owner_id
                    acquired_shutters = sorted(used_shutters)
                self.log_callback(
                    f"[{self.script_name}] Script controls shutters: {', '.join(acquired_shutters)}"
                )

            # --- END OF NEW LOCKING LOGIC ---


            # This is the original runner code, now inside a try...finally block

            def get_controller(loop_id):
                ctrl = self.shared_controllers.get(loop_id)
                if ctrl is None or not ctrl.connected:
                    raise ConnectionError(
                        f"[{loop_id}] No active connection. "
                        f"Check that the controller initialised successfully at startup."
                    )
                return ctrl

            def power_ramp(loop_id, target_power, rate):
                # rate in %/s, written as-is to the Ramp_OP register.
                if self.stop_event.is_set():
                    return
                ctrl = get_controller(loop_id)
                try:
                    mode = ctrl.get_mode(loop_id)
                    if mode != "manual":
                        ctrl.set_mode(loop_id, "manual")
                        self.log_callback(f"[{loop_id}] Switched to MANUAL mode.")
                except Exception as e:
                    self.log_callback(f"[{loop_id}] Mode check/set failed: {e}")
                    return

                try:
                    current_power = ctrl.read_value(loop_id, "output_pv")
                except Exception as e:
                    self.log_callback(f"[{loop_id}] Failed to read current power: {e}")
                    return

                duration_min = abs(target_power - current_power) / rate / 60

                try:
                    ctrl.set_ramp_status(loop_id, True)
                    self.log_callback(f"[{loop_id}] Attempting to enable ramp mode...")
                    ramp_verified = False
                    for i in range(3):
                        status = ctrl.get_ramp_status(loop_id)
                        if status == "on":
                            ramp_verified = True
                            self.log_callback(f"[{loop_id}] Ramp mode successfully verified as ON.")
                            break
                        self.log_callback(f"[{loop_id}] Verification failed (attempt {i + 1}/3). Retrying...")
                        time.sleep(0.3)
                    if not ramp_verified:
                        self.log_callback(
                            f"[{loop_id}] CRITICAL: Failed to enable ramp mode after 3 attempts. Aborting ramp.")
                        ctrl.set_ramp_status(loop_id, False)
                        return

                    ctrl.write_register(loop_id, "Ramp_OP", rate)
                    ctrl.write_value(loop_id, "output_setpoint", target_power)
                    self.log_callback(
                        f"[{loop_id}] Ramp_OP = {rate:.2f} %/s → {target_power:.1f}% "
                        f"(est. {duration_min:.1f} min) — moving to next step.")
                except Exception as e:
                    self.log_callback(f"[{loop_id}] Error during ramp: {e}")

            def temperature_ramp(loop_id, target_temp, normal_rate_min):
                if self.stop_event.is_set():
                    return
                MAX_TEMP_RATE = 20.0
                if normal_rate_min > MAX_TEMP_RATE:
                    self.log_callback(f"[{loop_id}] WARNING: Requested rate capped at {MAX_TEMP_RATE}°C/min.")
                    normal_rate_min = MAX_TEMP_RATE

                ctrl = get_controller(loop_id)    # Special case: loops with "script_direct_setpoint"
                # in addresses.py behave like the GUI "Set" button,
                # i.e. just write the setpoint, no ramp generator / OP changes.
                if _loop_flag(loop_id, "script_direct_setpoint"):
                    self.log_callback(
                        f"[{loop_id}] Using simple GUI-style temperature set: "
                        f"writing setpoint to {target_temp}°C (no ramp logic, no ramp/OP changes)."
                    )
                    try:
                        ctrl.write_value(loop_id, "temperature_setpoint", target_temp)
                        self.log_callback(f"[{loop_id}] New temperature setpoint = {target_temp}°C.")
                    except Exception as e:
                        self.log_callback(f"[{loop_id}] Error setting temperature setpoint: {e}")
                    return



                self.log_callback(f"[{loop_id}] Planning temperature ramp to {target_temp}°C.")

                # ------------------------------------------------------------------
                # EXCEPTION: For loops with "script_keep_ramp_generator" in
                # addresses.py we do NOT want to permanently disable
                # the OP ramp / ramp generator. We remember the initial ramp state
                # and restore it at the end instead of forcing it OFF.
                # ------------------------------------------------------------------
                skip_ramp_safety = _loop_flag(loop_id, "script_keep_ramp_generator")

                try:
                    initial_ramp_status = ctrl.get_ramp_status(loop_id)  # "on" or "off"
                except Exception as e:
                    self.log_callback(f"[{loop_id}] WARNING: Could not read initial ramp status: {e}")
                    initial_ramp_status = None

                # --- SAFETY INTERLOCK (skipped for "script_keep_ramp_generator" loops) ---
                if not skip_ramp_safety:
                    try:
                        self.log_callback(f"[{loop_id}] Safety Interlock: Ensuring ramp generator is disabled.")
                        ramp_disabled = False
                        for i in range(3):
                            ctrl.set_ramp_status(loop_id, False)
                            status = ctrl.get_ramp_status(loop_id)
                            if status == "off":
                                ramp_disabled = True
                                self.log_callback(f"[{loop_id}] Ramp generator successfully verified as OFF.")
                                break
                            self.log_callback(f"[{loop_id}] Verification failed (attempt {i + 1}/3). Retrying...")
                            time.sleep(0.3)

                        if not ramp_disabled:
                            self.log_callback(
                                f"[{loop_id}] CRITICAL: Failed to disable ramp generator. Aborting temperature ramp.")
                            return

                    except Exception as e:
                        self.log_callback(
                            f"[{loop_id}] CRITICAL: Failed to execute safety interlock. Aborting ramp. Error: {e}")
                        return
                else:
                    self.log_callback(
                        f"[{loop_id}] Se source detected → skipping ramp safety interlock (will restore state at the end)."
                    )

                try:
                    # STEP 1: Ensure controller is in AUTO mode
                    mode = ctrl.get_mode(loop_id)
                    if mode != "auto":
                        ctrl.set_mode(loop_id, "auto")
                        self.log_callback(f"[{loop_id}] Switched to AUTO mode for temperature ramp.")

                    current_temp = ctrl.read_value(loop_id, "temperature_pv")
                    cz_config = addresses.get(loop_id, {}).get("critical_zone")

                    # STEP 3: Plan the ramp segments (slowed inside the critical zone)
                    ramp_segments = plan_ramp_segments(current_temp, target_temp,
                                                       normal_rate_min, cz_config)

                    if not ramp_segments:
                        self.log_callback(f"[{loop_id}] Already at target temperature.")
                        return

                    total_duration_min = sum(seg.duration_min for seg in ramp_segments)

                    finish_time = datetime.now() + timedelta(minutes=total_duration_min)
                    self.log_callback(
                        f"[{loop_id}] Ramp plan: {len(ramp_segments)} segment(s). Estimated duration: {total_duration_min:.1f} min.")
                    self.log_callback(
                        f"[{loop_id}] Estimated completion time: {finish_time.strftime('%H:%M:%S')}.")

                    # STEP 4: Enable ramp generator for the temperature ramp
                    self.log_callback(f"[{loop_id}] Enabling ramp generator for temperature ramp.")
                    ctrl.set_ramp_status(loop_id, False)

                    # ── Fire-and-return: write registers for the first segment,
                    #    then immediately hand control back to the script.
                    #    If the ramp crosses a critical zone (multiple segments),
                    #    a daemon thread watches the PV and switches rates at each
                    #    segment boundary without blocking the script thread.
                    first_seg = ramp_segments[0]
                    self.log_callback(
                        f"[{loop_id}] Segment 1/{len(ramp_segments)}: "
                        f"Ramping to {first_seg.target}°C at {first_seg.rate}°C/min.")
                    ctrl.write_register(loop_id, "Ramp_temp", first_seg.rate)
                    ctrl.write_value(loop_id, "temperature_setpoint", first_seg.target)

                    if len(ramp_segments) > 1:
                        remaining_segs = ramp_segments[1:]
                        stop_ev       = self.stop_event
                        n_total       = len(ramp_segments)

                        def _continue_segments(segs, prev_target):
                            for idx, seg in enumerate(segs, start=2):
                                # Poll PV until the previous segment target is reached.
                                # A failed read never moves on to the next segment:
                                # keep waiting until the PV can be read again.
                                read_failed = False
                                while True:
                                    if stop_ev.is_set():
                                        return
                                    try:
                                        pv = ctrl.read_value(loop_id, "temperature_pv")
                                    except Exception as e:
                                        if not read_failed:
                                            read_failed = True
                                            self.log_callback(
                                                f"[{loop_id}] Cannot read PV ({e}): segment {idx}/{n_total} "
                                                f"held until communication is back.")
                                    else:
                                        if read_failed:
                                            read_failed = False
                                            self.log_callback(
                                                f"[{loop_id}] PV readable again ({pv:.1f}°C): "
                                                f"waiting for {prev_target}°C before segment {idx}/{n_total}.")
                                        if abs(pv - prev_target) <= 3.0:
                                            break
                                    time.sleep(2)
                                if stop_ev.is_set():
                                    return
                                self.log_callback(
                                    f"[{loop_id}] Segment {idx}/{n_total}: "
                                    f"Ramping to {seg.target}°C at {seg.rate}°C/min.")
                                ctrl.write_register(loop_id, "Ramp_temp", seg.rate)
                                ctrl.write_value(loop_id, "temperature_setpoint", seg.target)
                                prev_target = seg.target

                        threading.Thread(
                            target=_continue_segments,
                            args=(remaining_segs, first_seg.target),
                            daemon=True,
                        ).start()

                    self.log_callback(
                        f"[{loop_id}] Ramp to {target_temp}°C started "
                        f"(est. {total_duration_min:.1f} min) — moving to next step.")
                    self._hw_wait_items.append((loop_id, finish_time))

                except Exception as e:
                    self.log_callback(f"[{loop_id}] Error during temperature ramp: {e}")
                finally:
                    # ------------------------------------------------------------------
                    # FINALIZATION:
                    #  - Normal loops: disable ramp generator (safety).
                    #  - "script_keep_ramp_generator" loops: restore whatever the state
                    #    was before the ramp.
                    # ------------------------------------------------------------------
                    if skip_ramp_safety:
                        if initial_ramp_status is not None:
                            self.log_callback(
                                f"[{loop_id}] Finalizing sequence: restoring ramp generator to '{initial_ramp_status}'.")
                            ctrl.set_ramp_status(loop_id, initial_ramp_status == "on")
                        else:
                            self.log_callback(
                                f"[{loop_id}] Finalizing sequence: no initial ramp state → leaving ramp generator as-is.")
                    else:
                        self.log_callback(f"[{loop_id}] Finalizing sequence: Disabling ramp generator.")
                        ctrl.set_ramp_status(loop_id, False)

            def hold(duration_min):
                self.log_callback(f"Holding for {duration_min} min")
                end_time = time.time() + duration_min * 60
                while time.time() < end_time:
                    if self.stop_event.is_set():
                        self.log_callback("Hold interrupted.")
                        return
                    time.sleep(1)

            def wait_until(datetime_str: str):
                from datetime import datetime as _dt
                try:
                    target = _dt.strptime(datetime_str, "%Y-%m-%d %H:%M")
                except ValueError:
                    self.log_callback(
                        f"[{self.script_name}] wait_until: invalid format '{datetime_str}'. "
                        f"Expected YYYY-MM-DD HH:MM."
                    )
                    return

                now = _dt.now()
                if target <= now:
                    self.log_callback(
                        f"[{self.script_name}] wait_until: target time {datetime_str} is in the past — skipping."
                    )
                    return

                total_sec = (target - now).total_seconds()
                h, rem = divmod(int(total_sec), 3600)
                m = rem // 60
                self.log_callback(
                    f"[{self.script_name}] Waiting until {datetime_str} (in {h} h {m:02d} min)."
                )

                while not self.stop_event.is_set():
                    remaining = (target - _dt.now()).total_seconds()
                    if remaining <= 0:
                        break
                    h2, rem2 = divmod(int(remaining), 3600)
                    m2 = rem2 // 60
                    self._hw_status.emit(
                        f"⏳ Waiting until {target.strftime('%H:%M')} — {h2} h {m2:02d} min remaining"
                    )
                    time.sleep(min(5, remaining))

                self._hw_status.emit("")
                if not self.stop_event.is_set():
                    self.log_callback(f"[{self.script_name}] wait_until reached — continuing.")

            def wait_until_temperature(loop_id, target_temp, tolerance=0.5, timeout=None):
                """
                Wait until the PV temperature of loop_id reaches target_temp within tolerance.
                - loop_id: instrument name as in addresses.py ("Substrate", ...)
                - target_temp: temperature to reach (°C)
                - tolerance: acceptable difference (default ±0.5°C)
                - timeout: max waiting time in seconds (None = no timeout)

                Script stops immediately if user presses STOP.
                """

                ctrl = get_controller(loop_id)
                start_time = time.time()

                self.log_callback(
                    f"[{loop_id}] Waiting for temperature PV to reach {target_temp}°C "
                    f"(tolerance ±{tolerance}°C)..."
                )

                while True:
                    # Stop if user presses STOP button
                    if self.stop_event.is_set():
                        self.log_callback(
                            f"[{loop_id}] wait_until_temperature interrupted by user."
                        )
                        return False

                    try:
                        pv = ctrl.read_value(loop_id, "temperature_pv")
                    except Exception as e:
                        self.log_callback(
                            f"[{loop_id}] ERROR reading PV temperature: {e}"
                        )
                        return False

                    # Check temperature tolerance
                    if abs(pv - target_temp) <= tolerance:
                        self.log_callback(
                            f"[{loop_id}] Target temperature reached: PV={pv:.2f}°C"
                        )
                        return True

                    # Timeout check
                    if timeout is not None:
                        if time.time() - start_time > timeout:
                            self.log_callback(
                                f"[{loop_id}] TIMEOUT: temperature did not reach {target_temp}°C "
                                f"within {timeout} seconds (last PV={pv:.2f}°C)"
                            )
                            return False

                    time.sleep(1)  # avoid spamming Modbus

            # --- SHUTTER HELPERS ---

            def _resolve_shutter_line(shutter_id: str):
                """
                Resolve a shutter ID to its NI-DAQ line.
                Shutters are stored as top-level entries in addresses with a 'shutter_line' key.
                """
                info = addresses.get(shutter_id)
                if not info or "shutter_line" not in info:
                    raise KeyError(f"Unknown shutter id '{shutter_id}' in addresses.py")
                return shutter_id, info["shutter_line"]

            def _set_shutter(shutter_id: str, state: bool):
                """
                Write a shutter through the shared controller.
                Any failure raises, which stops the run (caught by the runner below).
                """
                word = "OPEN" if state else "CLOSED"
                if self.shutter_ctrl is None:
                    raise RuntimeError(f"No shutter controller available to set '{shutter_id}' {word}.")
                loop_key, line_name = _resolve_shutter_line(shutter_id)
                if not self.shutter_ctrl.set_shutter(loop_key, line_name, state):
                    raise RuntimeError(
                        f"Shutter '{shutter_id}' could not be set {word} (line: {line_name}). Script stopped."
                    )
                self.log_callback(f"[Shutter] {shutter_id} → {word} (line: {line_name})")

            def open_shutter(shutter_id: str):
                """Open a shutter (global or associated to a loop)."""
                if self.stop_event.is_set():
                    self.log_callback(f"[Shutter] open_shutter('{shutter_id}') skipped — script stopped.")
                    return
                _set_shutter(shutter_id, True)

            def close_shutter(shutter_id: str):
                """Close a shutter (global or associated to a loop)."""
                if self.stop_event.is_set():
                    self.log_callback(f"[Shutter] close_shutter('{shutter_id}') skipped — script stopped.")
                    return
                _set_shutter(shutter_id, False)



            # Expose helpers to the user's script
            ns = {
                "power_ramp": power_ramp,
                "temperature_ramp": temperature_ramp,
                "hold": hold,
                "wait_until": wait_until,
                "open_shutter": open_shutter,
                "close_shutter": close_shutter,
                "stop_event": self.stop_event,
                "wait_until_temperature": wait_until_temperature,
            }

            # ── Statement-by-statement execution with checkpoint ──────────
            try:
                tree = ast.parse(code)
                stmts = tree.body

                for i, node in enumerate(stmts):
                    # Highlight current statement before executing.
                    self._stmt_highlight.emit(i, len(stmts))

                    # Write checkpoint BEFORE executing — survives a crash (crash record only)
                    checkpoint_manager.save(self.script_name, i, len(stmts))

                    mod = ast.Module(body=[node], type_ignores=[])
                    try:
                        exec(compile(mod, "<script>", "exec"), ns, ns)
                    except InterlockError as e:
                        # Shutter not opened. Deliberate stop, not a crash: set
                        # stop_event (also stops background ramp segments) and
                        # fall through to the checkpoint clear below.
                        self.stop_event.set()
                        msg = f"Script stopped at line {node.lineno}: {e}"
                        self.log_callback(f"[{self.script_name}] {msg}")
                        self._interlock_alert.emit(msg)
                        break

                    if self.stop_event.is_set():
                        self.log_callback(
                            f"[{self.script_name}] Stopped by user after "
                            f"statement {i + 1}."
                        )
                        break

                # Clean finish or user stop → no crash → discard checkpoint
                checkpoint_manager.clear()

                # ── Hardware wait (Feature 2) ────────────────────────────────
                # Keep the window open until all hardware ramps physically finish.
                if self._hw_wait_items and not self.stop_event.is_set():
                    while not self.stop_event.is_set():
                        now = datetime.now()
                        still_pending = [
                            (lid, ft) for lid, ft in self._hw_wait_items if ft > now
                        ]
                        if not still_pending:
                            break
                        latest_lid, latest_ft = max(still_pending, key=lambda x: x[1])
                        self._hw_status.emit(
                            f"⏳ Waiting for hardware: {latest_lid} ramp completing at "
                            f"{latest_ft.strftime('%H:%M:%S')}"
                        )
                        time.sleep(5)
                self._hw_status.emit("")

            except Exception as e:
                self.log_callback(f"[{self.script_name}] Error: {e}")
                # Checkpoint is intentionally kept: after the watchdog restart
                # the GUI reports which script was running (no resume).
            finally:
                # --- RELEASE LOCKS ---
                with lock_manager_lock:
                    for loop in acquired_locks:
                        if loop in active_script_locks:
                            del active_script_locks[loop]
                    for sid in acquired_shutters:
                        active_shutter_locks.pop(sid, None)
                self.log_callback(
                    f"[{self.script_name}] Released instrument locks: "
                    f"{', '.join(acquired_locks + acquired_shutters)}"
                )
                # Clear hardware-wait label and unlock the editor.
                self._hw_status.emit("")
                self._stmt_highlight.emit(-1, len(stmts))

        self.log_callback(f"[{self.script_name}] Running script...")
        self.thread = threading.Thread(target=runner)
        self.thread.start()

    def _archive_run(self, code: str) -> bool:
        """Save the exact script and its compile report; False (and a message) blocks Run."""
        try:
            script_path, report_path = script_archive.save_run(
                code, self._last_report, self.script_name, addresses_config)
        except Exception as e:
            reason = str(e) if isinstance(e, script_archive.ArchiveError) \
                else f"unexpected error: {e!r}"
            self.log_callback(f"[{self.script_name}] Script NOT started: archive failed: {reason}")
            QMessageBox.critical(
                self, f"{self.script_name}: Run blocked",
                f"The script was NOT started because it could not be archived:\n\n{reason}")
            return False
        self.log_callback(f"[{self.script_name}] Run archived: {script_path} "
                          f"(+ {os.path.basename(report_path)})")
        return True

    def _show_interlock_alert(self, msg: str):
        QMessageBox.warning(self, f"{self.script_name}: pressure interlock", msg)

    # ── Editor highlight helpers (always called on the main thread via signal) ──

    def _apply_statement_colors(self, current_idx: int, total_stmts: int):
        """Color-code all script statements relative to the one being executed."""
        if current_idx == -1:
            self._reset_editor_highlight()
            return

        doc = self.editor.document()
        cursor = QTextCursor(doc)

        done_fmt = QTextCharFormat()
        done_fmt.setForeground(QColor("#1a7a1a"))          # green
        done_fmt.setFontWeight(QFont.Weight.Bold)

        active_fmt = QTextCharFormat()
        active_fmt.setForeground(QColor("#000000"))        # black
        active_fmt.setFontWeight(QFont.Weight.Bold)

        pending_fmt = QTextCharFormat()
        pending_fmt.setForeground(QColor("#c05a00"))       # orange
        pending_fmt.setFontWeight(QFont.Weight.Normal)

        for i, (start_line, end_line) in enumerate(self._stmt_ranges):
            fmt = done_fmt if i < current_idx else (
                  active_fmt if i == current_idx else pending_fmt)
            for line_num in range(start_line, end_line + 1):
                block = doc.findBlockByLineNumber(line_num)
                if not block.isValid():
                    continue
                cursor.setPosition(block.position())
                cursor.movePosition(
                    QTextCursor.MoveOperation.EndOfBlock,
                    QTextCursor.MoveMode.KeepAnchor,
                )
                cursor.setCharFormat(fmt)

    def _reset_editor_highlight(self):
        """Remove all colour formatting and unlock the editor."""
        cursor = QTextCursor(self.editor.document())
        cursor.select(QTextCursor.SelectionType.Document)
        default_fmt = QTextCharFormat()
        default_fmt.setForeground(QColor("#000000"))
        default_fmt.setFontWeight(QFont.Weight.Normal)
        cursor.setCharFormat(default_fmt)
        self.editor.setReadOnly(False)
        self.compile_button.setEnabled(True)
        self.rename_button.setEnabled(True)

    def is_active(self) -> bool:
        """Return True while the runner thread is alive (running or waiting_for_hardware)."""
        return self.thread is not None and self.thread.is_alive()

    def stop_script(self):
        self.stop_event.set()
        self.log_callback(f"[{self.script_name}] Stop requested.")
        # try to join briefly so runner can reach its finally:
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        # ensure loops are freed even if the thread is stubborn
        _release_loops(self.script_name)

    # ── Compilation ──────────────────────────────────────────────────────────

    def compile_current(self):
        """Run all compile checks on the current text and show the report."""
        report = compile_script(self.editor.toPlainText(),
                                controllers=self.shared_controllers)
        self._last_report = report
        self._show_report(report, outdated=False)
        self._update_run_enabled()
        self.log_callback(f"[{self.script_name}] {report.summary_line()}")

    def _is_compiled_ok(self) -> bool:
        """True when the last compilation has no errors and the text is unchanged since."""
        return (self._last_report is not None
                and self._last_report.ok
                and self._last_report.code == self.editor.toPlainText())

    def _update_run_enabled(self):
        self.run_button.setEnabled(self._is_compiled_ok())

    def _on_text_changed(self):
        # textChanged also fires on formatting (run highlight), so compare the
        # text itself: only a real edit invalidates the compilation.
        self._update_run_enabled()
        if self._last_report is not None:
            outdated = self._last_report.code != self.editor.toPlainText()
            self._show_report(self._last_report, outdated)

    def _show_report(self, report, outdated: bool):
        colors = {Level.ERROR: "#c00000", Level.WARNING: "#c05a00", Level.INFO: "#1f4e9c"}
        html = []
        if outdated:
            html.append("<b>⚠ Report outdated: script modified since last compilation. "
                        "Recompile to enable Run.</b>")
        html.append(f"<i>Compiled at {report.compiled_at.strftime('%H:%M:%S')}</i>")
        for m in report.messages:
            color = "#808080" if outdated else colors[m.level]
            html.append(f'<span style="color:{color}">{escape(m.format())}</span>')
        color = "#808080" if outdated else ("#1a7a1a" if report.ok else "#c00000")
        html.append(f'<b style="color:{color}">{escape(report.summary_line())}</b>')
        for label, value in report.summary.items():
            html.append(escape(f"{label}: {value}"))
        if report.timing is not None:
            timing = [escape(line) for line in report.timing.format_lines()]
            timing[0] = f"<b>{timing[0]}</b>"
            timing[-1] = f"<b>{timing[-1]}</b>"
            if outdated:
                timing = [f'<span style="color:#808080">{t}</span>' for t in timing]
            html.extend(timing)
        self.report_view.setHtml("<br>".join(html))

    # ── Name ─────────────────────────────────────────────────────────────────

    def _is_free(self, name: str) -> bool:
        return self.name_is_free is None or self.name_is_free(name, self)

    def unique_name(self, name: str) -> str:
        """`name`, or `name (2)`, `name (3)`... if another tab already uses it."""
        return _unique_name(name, self._is_free)

    def set_name(self, name: str) -> bool:
        """Rename the script (label + tab title). Refused while running. True if renamed."""
        name = name.strip()
        if not name or name == self.script_name:
            return False
        if self.is_active():
            self.log_callback(f"[{self.script_name}] Cannot rename while the script is running.")
            return False
        old = self.script_name
        self.script_name = name
        self.name_label.setText(name)
        self.name_changed.emit(name)
        self.log_callback(f"[{name}] Renamed from '{old}'.")
        return True

    def show_help(self):
        """Open (or bring to the front) the non-modal pop-up of script functions."""
        if self._help_dialog is None:
            self._help_dialog = ScriptHelpDialog(self)
        self._help_dialog.show()
        self._help_dialog.raise_()
        self._help_dialog.activateWindow()

    def request_rename(self):
        """✏️ Rename button: ask for a new name; a name used by another tab is refused."""
        if self.is_active():
            self.log_callback(f"[{self.script_name}] Cannot rename while the script is running.")
            return
        name, ok = QInputDialog.getText(self, "Rename script", "Script name:",
                                        text=self.script_name)
        name = name.strip()
        if not ok or not name or name == self.script_name:
            return
        if not self._is_free(name):
            QMessageBox.warning(self, "Rename script",
                                f"Another script tab is already named '{name}'.")
            return
        self.set_name(name)

    def save_script(self):
        os.makedirs("scripts", exist_ok=True)
        filepath, _ = QFileDialog.getSaveFileName(self, "Save Script", "scripts/", "Python Files (*.py)")
        if filepath:
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(self.editor.toPlainText())
                self.log_callback(f"[{self.script_name}] Script saved to {filepath}")
            except Exception as e:
                self.log_callback(f"[{self.script_name}] Save failed: {e}")
                return
            # The tab takes the file name, as when a script is loaded.
            if not self.is_active():
                self.set_name(self.unique_name(os.path.splitext(os.path.basename(filepath))[0]))

    def load_script(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Load Script", "scripts/", "Python Files (*.py)")
        if filepath:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                self.log_callback(f"[{self.script_name}] Load failed: {e}")
                return
            if self.open_script_callback is not None:
                self.open_script_callback(filepath, content)
            else:
                self.editor.setPlainText(content)
                self.editor.setFocus()
                self.log_callback(f"[{self.script_name}] Script loaded from {filepath}")


class ScriptManager(QWidget):
    def __init__(self, log_callback, controllers=None, shutter_ctrl=None):
        super().__init__()
        self.log_callback = log_callback
        self.controllers = controllers if controllers is not None else {}
        self.shutter_ctrl = shutter_ctrl
        self.tab_counter = 1

        self.tabs = QTabWidget()
        self.add_tab_button = QPushButton("+ Add Script Tab")
        self.add_tab_button.clicked.connect(self.add_new_tab)
        self.tabs.setCornerWidget(self.add_tab_button, Qt.Corner.TopRightCorner)
        # --- Make tabs closable once (avoid multiple signal connections) ---
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        self.setLayout(layout)

        self.add_new_tab()

    def add_new_tab(self):
        name = f"Script {self.tab_counter}"
        self.tab_counter += 1
        while not self.name_is_free(name):
            name = f"Script {self.tab_counter}"
            self.tab_counter += 1
        return self._create_tab(name)

    def open_script_tab(self, filepath, content):
        """Open a script loaded from disk in a new tab named after the file."""
        name = self.unique_name(os.path.splitext(os.path.basename(filepath))[0])
        editor = self._create_tab(name, content)
        self.log_callback(f"[{name}] Script loaded from {filepath}")
        return editor

    def name_is_free(self, name: str, editor=None) -> bool:
        """True if no tab other than `editor` uses `name` (case-insensitive).
        Names must be unique: the name owns the loop/shutter locks of a run."""
        key = name.strip().casefold()
        for i in range(self.tabs.count()):
            other = self.tabs.widget(i)
            if (isinstance(other, ScriptEditor) and other is not editor
                    and other.script_name.strip().casefold() == key):
                return False
        return True

    def unique_name(self, name: str) -> str:
        """`name`, or `name (2)`, `name (3)`... if a tab already uses it."""
        return _unique_name(name, self.name_is_free)

    def _create_tab(self, name, content=None):
        """Create a script tab, make it the active one and put the cursor in its editor."""
        editor = ScriptEditor(name, self.log_callback, self.controllers, self.shutter_ctrl,
                              open_script_callback=self.open_script_tab,
                              name_is_free=self.name_is_free)
        if content is not None:
            editor.editor.setPlainText(content)
        editor.name_changed.connect(
            lambda new_name, ed=editor: self.tabs.setTabText(self.tabs.indexOf(ed), new_name))
        index = self.tabs.addTab(editor, name)
        self.tabs.setCurrentIndex(index)
        editor.editor.setFocus()
        return editor

    def close_tab(self, index: int):
        """Safely close a finished or unwanted script tab."""
        widget = self.tabs.widget(index)
        # Feature 1: silently block close while the script is still running.
        if isinstance(widget, ScriptEditor) and widget.is_active():
            return
        if widget:
            try:
                if hasattr(widget, "stop_script"):
                    widget.stop_script()
            except Exception:
                pass
            widget.deleteLater()
        self.tabs.removeTab(index)
        # Always keep at least one script tab so the manager stays usable
        if self.tabs.count() == 0:
            self.add_new_tab()
