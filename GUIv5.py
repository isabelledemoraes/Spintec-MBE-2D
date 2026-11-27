import sys
import threading
import time
import os
import csv
import subprocess
import numpy as np
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QLineEdit,
    QHBoxLayout, QMainWindow, QTabWidget, QTextEdit, QComboBox,
    QGridLayout, QFrame, QSizePolicy, QDialog, QScrollArea, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, QThread
from PyQt6.QtGui import QIcon
from eurotherm_controller import EurothermController
import machine_config
from machine_config import addresses
from log_manager import LogManager
import shutter_controller
from shutter_controller import ShutterController, initialize_daq_outputs, DAQ_NAME
from connection_monitor import ConnectionMonitor
from pressure_interlock import InterlockError, bypassed_shutters
from pressure_gauge_panel import PressureGaugePanel
from pressure_gauge_controller import PressureGaugeController
from data_updater_threaded import EurothermWorker, PressureWorker
from script_editor_integration import ScriptManager
from instrument_panel import InstrumentPanel, ShutterPanel, CardGrid
from config_editor import ConfigTab
import checkpoint_manager
import ui_theme as ui

USE_SHUTTER_HARDWARE = True

# ─────────────────────────────────────────────────────────────────────────────
# Everything machine-specific comes from config/machine.yaml (machine_config.py):
# instruments, ports, limits, shutters, NI-DAQ device, simulation mode.
#
# SIMULATION — machine.simulation in machine.yaml (false on the instrument PC).
# When True: fake Eurotherm + pressure data, no serial ports needed.
# When False: real Eurotherm (Modbus) + pressure gauge required.
# ─────────────────────────────────────────────────────────────────────────────
SIMULATION = machine_config.SIMULATION
PRESSURE = machine_config.PRESSURE_KEY

# Exit code asking watchdog.py to start the program again (configuration changed).
RESTART_EXIT_CODE = 42

if SIMULATION:
    from sim_controllers import SimEurothermController, SimPressureGaugeController


def make_controller(loop_id):
    if SIMULATION:
        return SimEurothermController(loop_id)
    return EurothermController(loop_id)


class MainWindow(QMainWindow):
    def __init__(self, daq_init_ok=None):
        """daq_init_ok: result of initialize_daq_outputs() (NI-DAQ startup check, #12)."""
        super().__init__()
        machine = machine_config.MACHINE_NAME
        self.setWindowTitle("MBE Control" + (f" — {machine}" if machine else "")
                            + (" [SIMULATION]" if SIMULATION else ""))
        self.resize(1180, 900)
        self.setStyleSheet(ui.APP_STYLESHEET)
        # Set by the Configuration tab: start the program again after closing.
        self.restart_requested = False

        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.write_log_entry)
        self.log_timer.start(1000)  # Try to log every 1 second (1000 ms)

        self.instrument_panels = []
        self.latest_data = {}
        # latest_data keys of each instrument; NaN in the CSV while it does not answer (#12)
        self._log_keys = {}

        self.logger = LogManager()
        self.dialog_log_dir = "logs"
        os.makedirs(self.dialog_log_dir, exist_ok=True)
        self.dialog_log_date = datetime.now().strftime("%Y%m%d")
        self.dialog_log_path = os.path.join(
            self.dialog_log_dir,
            f"dialog_{self.dialog_log_date}.txt"
        )

        self.controllers = {}
        # Connection status of every instrument (#12), fed by the workers and
        # the shutter controller; shown by _update_connection_status.
        self.connection_monitor = ConnectionMonitor()

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(12, 8, 12, 4)
        self.setCentralWidget(widget)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.general_tab = QWidget()
        self.tabs.addTab(self.general_tab, "General View")
        _gen_outer = QVBoxLayout(self.general_tab)
        _gen_outer.setContentsMargins(4, 10, 4, 4)
        _gen_outer.setSpacing(10)

        # Header strip: machine, simulation mark, chamber pressure at a glance.
        _head = QHBoxLayout()
        _head.setSpacing(10)
        self._machine_label = QLabel(machine or "Machine name not set")
        self._machine_label.setTextFormat(Qt.TextFormat.PlainText)
        self._machine_label.setProperty("title", True)
        _head.addWidget(self._machine_label)
        if SIMULATION:
            _sim = QLabel("SIMULATION")
            _sim.setToolTip("Simulated instruments: nothing is sent to the real hardware.\n"
                            "Change it in the Configuration tab (machine.simulation).")
            _sim.setStyleSheet(ui.chip_style(ui.WARN, ui.WARN_SOFT))
            _head.addWidget(_sim)
        _head.addStretch()
        self._pressure_chip = QLabel("")
        self._pressure_chip.setTextFormat(Qt.TextFormat.RichText)
        self._pressure_chip.hide()
        _head.addWidget(self._pressure_chip)
        _gen_outer.addLayout(_head)

        # Crash banner (issue #10): hidden unless the previous session crashed
        # during a script; stays until acknowledged. See _report_previous_crash.
        self._crash_banner = QFrame()
        self._crash_banner.setStyleSheet(
            "QFrame { background-color: #fff3cd; border: 1px solid #e0a800; border-radius: 8px; }"
            "QLabel { color: #664d03; font-weight: bold; border: none; }"
        )
        _banner_layout = QHBoxLayout(self._crash_banner)
        self._crash_label = QLabel()
        self._crash_label.setWordWrap(True)
        self._crash_label.setTextFormat(Qt.TextFormat.PlainText)   # script name shown as is
        _banner_layout.addWidget(self._crash_label, 1)
        _ack_btn = QPushButton("Acknowledge")
        _ack_btn.clicked.connect(self._acknowledge_crash)
        _banner_layout.addWidget(_ack_btn)
        self._crash_banner.hide()
        _gen_outer.addWidget(self._crash_banner)

        # Communication banner (issue #12): instruments lost, and recovered ones
        # until acknowledged. See _update_connection_status.
        self._comm_banner = QFrame()
        self._comm_banner.setStyleSheet(
            "QFrame { background-color: #f8d7da; border: 1px solid #dc3545; border-radius: 8px; }"
            "QLabel { color: #842029; font-weight: bold; border: none; }"
        )
        _comm_layout = QHBoxLayout(self._comm_banner)
        self._comm_label = QLabel()
        self._comm_label.setWordWrap(True)
        self._comm_label.setTextFormat(Qt.TextFormat.PlainText)
        _comm_layout.addWidget(self._comm_label, 1)
        self._comm_ack_btn = QPushButton("Acknowledge")
        self._comm_ack_btn.setToolTip("Remove recovered instruments from this message")
        self._comm_ack_btn.clicked.connect(self._acknowledge_comm)
        _comm_layout.addWidget(self._comm_ack_btn)
        self._comm_banner.hide()
        _gen_outer.addWidget(self._comm_banner)

        # Shown until every instrument has answered or is lost (startup check).
        self._checking_label = QLabel("Checking instruments...")
        self._checking_label.setStyleSheet("color: #555; font-style: italic; padding: 2px;")
        _gen_outer.addWidget(self._checking_label)

        _gen_scroll = QScrollArea()
        _gen_scroll.setWidgetResizable(True)
        _gen_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # One card per Eurotherm loop, then the shutter card; columns follow the width.
        self.card_grid = CardGrid()
        _gen_scroll.setWidget(self.card_grid)
        _gen_outer.addWidget(_gen_scroll)

        self.log_mode_selector = QComboBox()
        self.log_mode_selector.addItems(["Standby", "Growth"])
        

        self.filename_input = QLineEdit()
        self.filename_input.setPlaceholderText("Filename (only for Growth)")

        self.confirm_log_btn = QPushButton("Confirm")
        self.confirm_log_btn.clicked.connect(self.set_log_mode)

        self.shutter_ctrl = ShutterController()

        log_layout = QHBoxLayout()
        log_layout.addWidget(QLabel("Log Mode:"))
        log_layout.addWidget(self.log_mode_selector)
        log_layout.addWidget(self.filename_input)
        log_layout.addWidget(self.confirm_log_btn)

        dash_btn = QPushButton("📊 Dashboard")
        dash_btn.setToolTip("Open live data dashboard in a standalone desktop window")
        dash_btn.clicked.connect(self.launch_dashboard)
        log_layout.addWidget(dash_btn)

        layout.addLayout(log_layout)
        self.script_tab = ScriptManager(
            log_callback=self.append_log,
            controllers=self.controllers,
            shutter_ctrl=self.shutter_ctrl,
        )
        self.tabs.addTab(self.script_tab, "Scripts")

#self.plot_tab = PlotTab()  # ✅ connect external tab
        #self.tabs.addTab(self.plot_tab, "Plots")

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(120)
        layout.addWidget(self.log_output)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready.")

        for loop_id in machine_config.loop_ids(addresses):
            self.connection_monitor.add(loop_id)
            ctrl = None
            try:
                ctrl = make_controller(loop_id)
                ctrl.connect()
                self.controllers[loop_id] = ctrl
            except Exception as e:
                ctrl = None
                self.append_log(f"[{loop_id}] Controller init failed: {e}")
            panel = InstrumentPanel(
                loop_id, ctrl, addresses[loop_id].get("shutter_line"), self.shutter_ctrl,
                addresses=addresses, log=self.append_log,
                use_shutter_hardware=USE_SHUTTER_HARDWARE)
            self.instrument_panels.append(panel)
            self.card_grid.add_card(panel)

        # The gauge exists when the configuration has it (pressure_gauge.enabled).
        if PRESSURE in addresses:
            self.connection_monitor.add(PRESSURE)
            self._pressure_unit = addresses[PRESSURE].get("unit") or ""
            self._show_pressure(None)
            try:
                pg_ctrl = SimPressureGaugeController(PRESSURE) if SIMULATION else PressureGaugeController(PRESSURE)
                pg_ctrl.connect()
                pressure_panel = PressureGaugePanel(PRESSURE, pg_ctrl)
                # Pressure interlock (#8): without this, interlocked shutters stay closed.
                self.shutter_ctrl.pressure_gauge = pg_ctrl
            except Exception as e:
                self.append_log(f"[Pressure] Controller init failed: {e}")
                pressure_panel = PressureGaugePanel(PRESSURE, None)

            # Worker list — pressure panel stays here so EurothermWorker/PressureWorker dispatch works
            self.instrument_panels.append(pressure_panel)

            # Dedicated "Pressure" tab (not in the general grid)
            _pressure_tab = QWidget()
            _pressure_tab_layout = QVBoxLayout(_pressure_tab)
            _pressure_tab_layout.addWidget(pressure_panel)
            self.tabs.addTab(_pressure_tab, "Pressure")


        self.global_shutter_buttons = {}
        self.add_global_shutter_panel()

        for sid in bypassed_shutters(addresses):
            self.append_log(f"[Shutter] WARNING: pressure interlock BYPASSED in machine.yaml "
                            f"for {sid}: it opens without pressure check.")

        # System configuration (password protected): edits config/machine.yaml.
        self.config_tab = ConfigTab(
            log=self.append_log,
            scripts_running=self.running_scripts,
            request_restart=self.request_restart,
        )
        self._config_tab_index = self.tabs.addTab(self.config_tab, "⚙ Configuration")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        if machine_config.LOAD_INFO:
            self.append_log(f"[Config] Configuration {machine_config.LOAD_INFO}.")
        for problem in machine_config.validate(machine_config.config):
            self.append_log(f"[Config] WARNING: {machine_config.CONFIG_NAME}: {problem}")

        # After every instrument is registered: the startup check ends when all have reported.
        self._monitor_daq(daq_init_ok)

        self.threads = []
        for panel in self.instrument_panels:
            worker = None
            # Decide which worker to create based on the panel type
            if isinstance(panel, PressureGaugePanel):
                worker = PressureWorker(panel.name, panel.controller, self.connection_monitor)
            else:
                # Assume any other panel is for a Eurotherm
                worker = EurothermWorker(panel.name, panel.controller, self.connection_monitor)

            if worker:
                # Every column exists from the start (NaN until the first answer),
                # so the CSV header does not depend on which instruments answered.
                keys = [f"{panel.name}_{k}" for k in worker.data_keys]
                self._log_keys[panel.name] = keys
                self.latest_data.update(dict.fromkeys(keys, float("nan")))
                thread = QThread()
                worker.moveToThread(thread)
                thread.started.connect(worker.run)
                thread.finished.connect(worker.deleteLater)
                worker.data_ready.connect(self.handle_data_update)
                self.threads.append((thread, worker))
                thread.start()

        # Report a script that was interrupted by a crash (no resume)
        self._report_previous_crash()

        # Feature 3: poll active_script_locks every second to update lock icons.
        self._lock_poll_timer = QTimer(self)
        self._lock_poll_timer.timeout.connect(self._update_instrument_locks)
        self._lock_poll_timer.timeout.connect(self._update_connection_status)
        self._lock_poll_timer.start(1000)
        self._update_connection_status()

    def _monitor_daq(self, daq_init_ok):
        """
        NI-DAQ startup status (#12): the result of initialize_daq_outputs() (run
        before the window). Afterwards every shutter write is reported; a single
        failed write marks the NI-DAQ lost.
        """
        if not USE_SHUTTER_HARDWARE:
            return
        if daq_init_ok is None and shutter_controller.nidaqmx is not None:
            # initialize_daq_outputs() was not run (other launcher): status unknown.
            self.append_log(f"[Connection] {DAQ_NAME} not checked at startup "
                            f"(outputs not initialized); not monitored.")
            return
        self.connection_monitor.add(DAQ_NAME, max_failures=1)
        self.shutter_ctrl.monitor = self.connection_monitor
        if daq_init_ok:
            self.connection_monitor.report_success(DAQ_NAME)
        elif daq_init_ok is False:
            self.connection_monitor.report_failure(
                DAQ_NAME, "output initialization failed (see console)")
        elif SIMULATION:
            self.connection_monitor.report_success(DAQ_NAME)   # simulated shutters
        else:
            self.connection_monitor.mark_lost(
                DAQ_NAME, "nidaqmx not installed: shutters are NOT driven")

    def _update_connection_status(self):
        """Every 1 s: log lost/recovered events, refresh the banner and the panel marks (#12)."""
        monitor = self.connection_monitor
        for message in monitor.drain_events():
            self.append_log(f"[Connection] {message}")
        self._checking_label.setVisible(not monitor.startup_done())
        text = monitor.banner_text()
        self._comm_label.setText(text or "")
        self._comm_banner.setVisible(text is not None)
        self._comm_ack_btn.setVisible(monitor.has_recovered())

        statuses = monitor.snapshot()
        for panel in self.instrument_panels:
            status = statuses.get(panel.name)
            if status is not None:
                panel.set_connection(status)
        daq = statuses.get(DAQ_NAME)
        if daq is not None:
            self._shutter_frame.set_connection(DAQ_NAME, daq)
        gauge = statuses.get(PRESSURE)
        if gauge is not None and not gauge.answering:
            self._show_pressure(None)       # never a stale pressure in the header

    def _acknowledge_comm(self):
        names = self.connection_monitor.acknowledge()
        if names:
            self.append_log(f"[Connection] Recovery acknowledged: {', '.join(names)}.")
        self._update_connection_status()

    def _report_previous_crash(self):
        """
        A checkpoint left at startup means a script was running when the app
        crashed. Log it, clear the checkpoint (the log is the permanent record)
        and show a banner in the General View until acknowledged. No resume.
        """
        cp = checkpoint_manager.load()
        if cp is None:
            return
        message = checkpoint_manager.crash_message(cp)
        self.append_log(f"[Recovery] {message}")
        checkpoint_manager.clear()
        self._crash_label.setText(message)
        self._crash_banner.show()

    def _acknowledge_crash(self):
        self._crash_banner.hide()
        self.append_log("[Recovery] Crash message acknowledged.")

    def _update_instrument_locks(self):
        """
        Poll script locks and the shared shutter controller (every 1 s):
        lock icons on instrument panels, plus real state and lock of every shutter button.
        """
        from script_editor_integration import active_script_locks, active_shutter_locks, lock_manager_lock
        with lock_manager_lock:
            snapshot = dict(active_script_locks)
            shutter_snapshot = dict(active_shutter_locks)
        sync_shutters = USE_SHUTTER_HARDWARE and self.shutter_ctrl is not None
        for panel in self.instrument_panels:
            if isinstance(panel, InstrumentPanel):
                owner = snapshot.get(panel.name)
                panel.set_locked(owner if isinstance(owner, str) else None)
                if sync_shutters and panel.shutter_line:
                    panel.set_shutter_display(
                        self.shutter_ctrl.get_state(panel.name),
                        shutter_snapshot.get(panel.name),
                    )
        if sync_shutters:
            for shutter_id, (btn, label_text) in self.global_shutter_buttons.items():
                state = self.shutter_ctrl.get_state(shutter_id)
                owner = shutter_snapshot.get(shutter_id)
                btn.setChecked(state)
                self.update_global_shutter_style(btn, label_text, state)
                btn.setEnabled(owner is None)
                btn.setToolTip(f"🔒 Controlled by {owner}" if owner else "")

    def append_log(self, message):
        now = datetime.now()
        # What is shown in the GUI (same as before, time only)
        gui_timestamp = now.strftime("%H:%M:%S")
        self.log_output.append(f"[{gui_timestamp}] {message}")

        # What is written to the text file (date + time)
        file_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        # Rotate file if the day changed during a long run
        current_date = now.strftime("%Y%m%d")
        if current_date != getattr(self, "dialog_log_date", None):
            self.dialog_log_date = current_date
            self.dialog_log_path = os.path.join(
                self.dialog_log_dir,
                f"dialog_{self.dialog_log_date}.txt"
            )

        try:
            with open(self.dialog_log_path, "a", encoding="utf-8") as f:
                f.write(f"[{file_timestamp}] {message}\n")
        except Exception as e:
            # Fallback to console if we cannot write the log file
            print(f"[DialogLog] Failed to write log: {e}")


        # In GUIv5.py, inside the MainWindow class

        # In GUIv5.py, inside the MainWindow class

    def add_global_shutter_panel(self):
        """Card with one button per shutter that is not tied to a Eurotherm loop."""
        frame = ShutterPanel()
        self._shutter_frame = frame
        self._daq_label = frame.daq_label

        # (id, label) of every shutter-only entry of the configuration.
        for shutter_id, label_text in machine_config.global_shutters(addresses):
            btn = QPushButton(f"{label_text}: CLOSED")
            btn.setCheckable(True)

            # Restore last saved state (simulated or hardware)
            initial_state = self.shutter_ctrl.get_last_saved(shutter_id)
            btn.setChecked(initial_state)
            self.update_global_shutter_style(btn, label_text, initial_state)

            btn.clicked.connect(
                lambda checked, sid=shutter_id, prefix=label_text, b=btn: self.toggle_global_shutter(sid, prefix, b, checked)
            )

            frame.add_button(btn)
            self.global_shutter_buttons[shutter_id] = (btn, label_text)

        self.card_grid.add_card(frame, full_width=True)
        if not self.global_shutter_buttons:
            frame.hide()

    def update_global_shutter_style(self, button: QPushButton, label_prefix: str, state: bool):
        """Update text and color of a global shutter button."""
        text = f"{label_prefix}: {'OPEN' if state else 'CLOSED'}"
        style = ui.shutter_style(state)
        if button.text() != text:
            button.setText(text)
        if button.styleSheet() != style:
            button.setStyleSheet(style)

    def toggle_global_shutter(self, shutter_id: str, label_prefix: str, button: QPushButton, state: bool):
        """Callback when a global shutter button is toggled."""
        line_name = addresses.get(shutter_id, {}).get("shutter_line")

        print(f"[Global Shutter] {shutter_id} → {state} (line: {line_name})")

        if USE_SHUTTER_HARDWARE and self.shutter_ctrl and line_name:
            try:
                ok = self.shutter_ctrl.set_shutter(shutter_id, line_name, state)
            except InterlockError as e:
                # Opening refused, nothing written: shutter stays closed.
                button.setChecked(False)
                self.update_global_shutter_style(button, label_prefix, False)
                self.append_log(f"[Shutter] {shutter_id} not opened: {e}")
                QMessageBox.warning(self, "Pressure interlock", f"{shutter_id} not opened: {e}")
                return
            if not ok:
                # Write failed: revert the button to the previous (real) state.
                state = not state
                button.setChecked(state)
                self.append_log(f"[Shutter] Failed to set {shutter_id}; state unchanged.")

        self.update_global_shutter_style(button, label_prefix, state)

    def handle_data_update(self, name, data):
        for panel in self.instrument_panels:
            if panel.name == name:
                if isinstance(panel, PressureGaugePanel):
                    if "P" in data:
                        panel.update_pressure(data['P'])
                        self._show_pressure(data['P'])
                else:
                    panel.update_data(data)

                # Universal status dot logic
                if panel.controller:
                    # For pressure, the dot is handled in update_pressure. For Eurotherm, it's green.
                    if not isinstance(panel, PressureGaugePanel):
                        panel.set_status_dot("green")
                else:
                    panel.set_status_dot("red")

                # Update latest_data for logging
                for key, value in data.items():
                    self.latest_data[f"{name}_{key}"] = value
                break

    def _show_pressure(self, pressure):
        """Chamber pressure in the General View header (None = no current value)."""
        value = "—" if pressure is None else f"{pressure:.2e}"
        if value == getattr(self, "_pressure_shown", None):
            return
        self._pressure_shown = value
        self._pressure_chip.setText(
            f'<span style="color:{ui.MUTED};">Pressure&nbsp;&nbsp;</span>'
            f'<span style="font-size:13pt; font-weight:700;">{value}</span>'
            f'<span style="color:{ui.MUTED};"> {self._pressure_unit}</span>')
        self._pressure_chip.setStyleSheet(
            f"background: {ui.SURFACE}; border: 1px solid {ui.BORDER}; "
            f"border-radius: 10px; padding: 5px 12px;")
        self._pressure_chip.show()

    # ── Configuration tab ────────────────────────────────────────────────

    def _on_tab_changed(self, index):
        # Leaving the Configuration tab locks it again (edits in progress are kept).
        if index != self._config_tab_index:
            self.config_tab.lock()

    def running_scripts(self) -> list[str]:
        """Names of the scripts being executed."""
        names = []
        for i in range(self.script_tab.tabs.count()):
            editor = self.script_tab.tabs.widget(i)
            if hasattr(editor, "is_active") and editor.is_active():
                names.append(editor.script_name)
        return names

    def request_restart(self) -> bool:
        """Close the program and start it again (new configuration). False if refused."""
        running = self.running_scripts()
        if running:
            QMessageBox.warning(
                self, "Restart",
                f"A script is running ({', '.join(running)}).\n"
                f"Stop it or wait for its end, then restart.")
            return False
        # A manual ramp across a critical zone is written segment by segment by
        # this program: restarting now would leave it at the zone boundary.
        ramping = [p.name for p in self.instrument_panels
                   if isinstance(p, InstrumentPanel) and p.follow_up_pending()]
        if ramping:
            QMessageBox.warning(
                self, "Restart",
                f"A manual ramp is crossing its critical zone ({', '.join(ramping)}): "
                f"its next segment is not written yet.\nWait for its end, then restart.")
            return False
        self.append_log("[Config] Restart requested to apply the configuration.")
        self.restart_requested = True
        self.close()
        return True

    def write_log_entry(self):
        """Periodically called by a QTimer to attempt logging."""
        try:
            # An instrument whose last poll failed is logged as NaN, never with
            # its last (stale) values; they come back with its next answer (#12).
            statuses = self.connection_monitor.snapshot()
            for name, keys in self._log_keys.items():
                status = statuses.get(name)
                if status is not None and not status.answering:
                    self.latest_data.update(dict.fromkeys(keys, float("nan")))
            # Always call the logger and let it decide what to do.
            self.logger.log(self.latest_data.copy())
        except Exception as e:
            print(f"[Logger] Failed to log: {e}")

    def launch_dashboard(self):
        """Launch visu_window.py as a separate subprocess (native desktop window)."""
        if getattr(self, "_dash_proc", None) and self._dash_proc.poll() is None:
            self.append_log("[Dashboard] Visualization window is already running.")
            return
        try:
            self._dash_proc = subprocess.Popen(
                [sys.executable, "visu_window.py"],
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            self.append_log("[Dashboard] Visualization window launched.")
        except Exception as e:
            self.append_log(f"[Dashboard] Failed to start: {e}")

    def closeEvent(self, event):
        # 0) Gracefully stop any running scripts so their threads can exit
        #    and checkpoint_manager.clear() is reached (no checkpoint = clean quit).
        #    If Python is killed hard (crash), this block never runs,
        #    the checkpoint survives, and the next start reports the crash.
        script_mgr = getattr(self, "script_tab", None)
        if script_mgr:
            for i in range(script_mgr.tabs.count()):
                editor = script_mgr.tabs.widget(i)
                if editor and hasattr(editor, "stop_script"):
                    try:
                        editor.stop_script()   # sets stop_event; joins up to 2 s
                    except Exception:
                        pass

        # Nothing polls or logs once the window is closed; manual ramps stop
        # writing their next segments, like the scripts above.
        self.log_timer.stop()
        self._lock_poll_timer.stop()
        for panel in getattr(self, "instrument_panels", []):
            if isinstance(panel, InstrumentPanel):
                panel.stop_follow_up()

        # 1) Stop workers and stop threads
        for thread, worker in getattr(self, "threads", []):
            try:
                worker.stop()
            except Exception:
                pass
            try:
                thread.quit()
            except Exception:
                pass
        # 2) Wait for all threads to finish
        for thread, _ in getattr(self, "threads", []):
            try:
                thread.wait(3000)
            except Exception:
                pass
        # 3) Let Qt delete thread wrappers
        for thread, _ in getattr(self, "threads", []):
            try:
                thread.deleteLater()
            except Exception:
                pass
        # 4) Release controllers / serial ports
        for ctrl in getattr(self, "controllers", {}).values():
            try:
                ctrl.disconnect()
            except Exception:
                pass
        # 5) Shut down dashboard subprocess if it was launched
        dash_proc = getattr(self, "_dash_proc", None)
        if dash_proc and dash_proc.poll() is None:
            try:
                dash_proc.terminate()
            except Exception:
                pass
        super().closeEvent(event)

    def set_log_mode(self):
        mode = self.log_mode_selector.currentText()
        filename = self.filename_input.text().strip()

        if mode == "Growth" and not filename:
            print("[Logging] Please enter a filename for Growth mode.")
            return

        self.logger.set_mode(mode, filename if mode == "Growth" else None)
        print(f"[Logging] Mode set to {mode}, filename: {filename if mode == 'Growth' else 'auto'}")


def main() -> int:
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MBEControl.2026")
    except Exception:
        pass    # not on Windows
    if machine_config.LOAD_ERROR:
        # Never start on a configuration that could not be read: nothing is
        # known about the machine. Exit code 0 so that the watchdog does not loop.
        app = QApplication(sys.argv)
        QMessageBox.critical(
            None, "MBE Control — configuration error",
            f"The machine configuration cannot be used:\n\n{machine_config.LOAD_ERROR}\n\n"
            f"Fix {machine_config.CONFIG_PATH} (or restore its .bak copy) and start again.")
        return 0
    daq_ok = initialize_daq_outputs()  # must be first DAQ call — sets all lines LOW
    app = QApplication(sys.argv)
    _icon = QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mbe_icon.ico"))
    app.setWindowIcon(_icon)
    window = MainWindow(daq_init_ok=daq_ok)
    window.setWindowIcon(_icon)
    window.show()
    code = app.exec()
    if window.restart_requested:
        if os.environ.get("MBE_WATCHDOG"):
            return RESTART_EXIT_CODE          # watchdog.py starts the program again
        subprocess.Popen([sys.executable, os.path.abspath(__file__)],
                         cwd=os.path.dirname(os.path.abspath(__file__)))
        return 0
    return code


if __name__ == "__main__":
    sys.exit(main())
