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
from addresses import addresses
from log_manager import LogManager
import shutter_controller
from shutter_controller import ShutterController, initialize_daq_outputs, DAQ_NAME
from connection_monitor import ConnectionMonitor, LOST, panel_text, frame_style
from pressure_interlock import InterlockError, bypassed_shutters
from pressure_gauge_panel import PressureGaugePanel
from pressure_gauge_controller import PressureGaugeController
from data_updater_threaded import EurothermWorker, PressureWorker
from script_editor_integration import ScriptManager
import checkpoint_manager
#from plot_tab import PlotTab

USE_SHUTTER_HARDWARE = True
USE_PRESSURE_GAUGE = True

# ─────────────────────────────────────────────────────────────────────────────
# SIMULATION MODE — set per machine in addresses.py (SIMULATION = False on the
# instrument PC). Defaults to True when addresses.py does not define it.
# When True: fake Eurotherm + pressure data, no serial ports needed.
# When False: real Eurotherm (COM1 Modbus) + pressure gauge (COM10) required.
# ─────────────────────────────────────────────────────────────────────────────
import addresses as _machine_config
SIMULATION = getattr(_machine_config, "SIMULATION", True)

if SIMULATION:
    from sim_controllers import SimEurothermController, SimPressureGaugeController


def make_controller(loop_id):
    if SIMULATION:
        return SimEurothermController(loop_id)
    return EurothermController(loop_id)



PANEL_STYLE = "background-color: #f0f0f0; padding: 5px; border-radius: 8px;"
SHUTTER_FRAME_STYLE = "background-color: #f8f8f8; padding: 5px; border-radius: 8px;"


class InstrumentPanel(QFrame):
    def __init__(self, name, controller=None, shutter_line=None, shutter_ctrl=None):
        super().__init__()
        self.name = name
        self.controller = controller
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setObjectName("instrumentPanel")
        self._conn_mark = None   # last connection mark shown (#12)
        self.setStyleSheet(frame_style(PANEL_STYLE, "instrumentPanel", lost=False))
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)

        self.name_label = QLabel(f"<b>{name}</b>")
        self.temp_label = QLabel("Temp: -- °C")
        self.setpoint_temp = QLabel("Target Temp: -- °C")
        self.setpoint_power = QLabel("Target Power: -- %")
        self.mode_label = QLabel("Mode: --")
        self.power_label = QLabel("Power: -- %")
        self.ramp_temp_value = QLabel("Ramp Temp: -- °C/min")
        self.ramp_power_value = QLabel("Ramp Power: -- %/s")
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(12, 12)
        self.set_status_dot("grey")

        self.toggle_btn = QPushButton("Details ▼")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(False)
        self.toggle_btn.clicked.connect(self.toggle_details)

        self.setpoint_input = QLineEdit()
        self.setpoint_input.setFixedWidth(60)
        self.setpoint_input.setPlaceholderText("Set °C")
        self.set_temp_btn = QPushButton("Set")
        self.set_temp_btn.clicked.connect(self.set_temperature)

        self.details_frame = QWidget()
        details_layout = QHBoxLayout()
        details_layout.addWidget(self.setpoint_input)
        details_layout.addWidget(self.setpoint_power)
        details_layout.addWidget(self.set_temp_btn)
        self.details_frame.setLayout(details_layout)
        self.details_frame.setVisible(False)

        self.lock_label = QLabel("")
        self.lock_label.setToolTip("")

        # Connection status (#12): "⚠ No communication — last OK ..." / "Recovered at ..."
        self.comm_label = QLabel("")
        self.comm_label.setWordWrap(True)
        self.comm_label.hide()

        info_grid = QGridLayout()
        info_grid.addWidget(self.name_label, 0, 0)
        info_grid.addWidget(self.mode_label, 0, 1)
        info_grid.addWidget(self.lock_label, 0, 2)
        info_grid.addWidget(self.temp_label, 1, 0)
        info_grid.addWidget(self.power_label, 1, 1)
        info_grid.addWidget(self.setpoint_temp, 2, 0)
        info_grid.addWidget(self.setpoint_power, 2, 1)
        info_grid.addWidget(self.ramp_temp_value, 3, 0)
        info_grid.addWidget(self.ramp_power_value, 3, 1)

        layout.addLayout(info_grid)
        layout.addWidget(self.comm_label)
        layout.addWidget(self.toggle_btn)
        layout.addWidget(self.details_frame)
        layout.addWidget(self.status_dot)

        self.shutter_line = shutter_line
        self.shutter_ctrl = shutter_ctrl
        self.shutter_state = False
        self.shutter_btn = QPushButton("Shutter: CLOSED")
        self.shutter_btn.setCheckable(True)
        self.shutter_btn.setChecked(False)
        self.shutter_btn.clicked.connect(self.toggle_shutter)
        self.shutter_btn.setStyleSheet("background-color: lightcoral;")
        layout.addWidget(self.shutter_btn)

        if USE_SHUTTER_HARDWARE and self.shutter_ctrl and self.shutter_line:
            real_state = self.shutter_ctrl.get_last_saved(self.name)
            self.shutter_state = real_state
            self.shutter_btn.setChecked(real_state)
            self.update_shutter_style()

    def set_locked(self, owner: str | None):
        """Grey out controls and show lock icon when an instrument is in use by a script."""
        locked = owner is not None
        # The shutter button is locked separately (set_shutter_display), only
        # when the script actually opens/closes this shutter.
        self.setpoint_input.setEnabled(not locked)
        self.set_temp_btn.setEnabled(not locked)
        if locked:
            self.lock_label.setText("🔒")
            self.lock_label.setToolTip(f"Locked by {owner}")
        else:
            self.lock_label.setText("")
            self.lock_label.setToolTip("")

    def set_status_dot(self, color):
        self.status_dot.setStyleSheet(f"background-color: {color}; border-radius: 6px;")

    def set_connection(self, status):
        """Red border + ⚠ + last OK time while lost; "Recovered" until acknowledged (#12)."""
        lost = status.state == LOST
        text = panel_text(status)
        if (lost, text) == self._conn_mark:
            return
        self._conn_mark = (lost, text)
        self.setStyleSheet(frame_style(PANEL_STYLE, "instrumentPanel", lost))
        self.comm_label.setText(text)
        self.comm_label.setStyleSheet(f"color: {'#b02a37' if lost else '#146c43'}; font-weight: bold;")
        self.comm_label.setVisible(bool(text))
        if lost:
            self.set_status_dot("red")

    def set_shutter_display(self, state: bool, owner: str | None):
        """Show the real shutter state; disable the button while a script controls it."""
        self.shutter_state = state
        self.shutter_btn.setChecked(state)
        self.update_shutter_style()
        self.shutter_btn.setEnabled(owner is None)
        self.shutter_btn.setToolTip(f"🔒 Controlled by {owner}" if owner else "")

    def toggle_shutter(self):
        requested = self.shutter_btn.isChecked()
        if USE_SHUTTER_HARDWARE and self.shutter_ctrl and self.shutter_line:
            try:
                ok = self.shutter_ctrl.set_shutter(self.name, self.shutter_line, requested)
            except InterlockError as e:
                self.shutter_btn.setChecked(self.shutter_state)
                print(f"[{self.name}] Shutter not opened: {e}")
                QMessageBox.warning(self, "Pressure interlock", f"{self.name} not opened: {e}")
                return
            if not ok:
                # Write failed: keep showing the previous (real) state.
                self.shutter_btn.setChecked(self.shutter_state)
                print(f"[{self.name}] Shutter write failed; state unchanged ({self.shutter_state})")
                return
        self.shutter_state = requested
        self.update_shutter_style()
        print(f"[{self.name}] Shutter state = {self.shutter_state}")

    def update_shutter_style(self):
        if self.shutter_state:
            self.shutter_btn.setText("Shutter: OPEN")
            self.shutter_btn.setStyleSheet("background-color: lightgreen;")
        else:
            self.shutter_btn.setText("Shutter: CLOSED")
            self.shutter_btn.setStyleSheet("background-color: lightcoral;")

    def toggle_details(self):
        show = self.toggle_btn.isChecked()
        self.details_frame.setVisible(show)
        self.toggle_btn.setText("Hide ▲" if show else "Details ▼")

    def set_temperature(self):
        if not self.controller:
            return
        try:
            value = float(self.setpoint_input.text())
            self.controller.write_value(self.name, "temperature_setpoint", value)
        except Exception as e:
            print(f"[{self.name}] Failed to set temperature: {e}")

class MainWindow(QMainWindow):
    def __init__(self, daq_init_ok=None):
        """daq_init_ok: result of initialize_daq_outputs() (NI-DAQ startup check, #12)."""
        super().__init__()
        self.setWindowTitle("MBEControl-Se" + (" [SIMULATION]" if SIMULATION else ""))
        self.resize(1000, 800)

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
        self.setCentralWidget(widget)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.general_tab = QWidget()
        self.tabs.addTab(self.general_tab, "General View")
        _gen_outer = QVBoxLayout(self.general_tab)

        # Crash banner (issue #10): hidden unless the previous session crashed
        # during a script; stays until acknowledged. See _report_previous_crash.
        self._crash_banner = QFrame()
        self._crash_banner.setStyleSheet(
            "QFrame { background-color: #fff3cd; border: 1px solid #e0a800; }"
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
            "QFrame { background-color: #f8d7da; border: 1px solid #dc3545; }"
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
        _gen_content = QWidget()
        self.general_layout = QGridLayout(_gen_content)
        _gen_scroll.setWidget(_gen_content)
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

        loop_ids = [
            k for k, v in addresses.items()
            if isinstance(v, dict)
               and "temperature_pv" in v  # é um Eurotherm
               and k not in ("Pressure",)  # exclui pressure
        ]

        for i, loop_id in enumerate(loop_ids):
            self.connection_monitor.add(loop_id)
            try:
                ctrl = make_controller(loop_id)
                ctrl.connect()
                self.controllers[loop_id] = ctrl
                shutter_line = addresses[loop_id].get("shutter_line")
                panel = InstrumentPanel(loop_id, ctrl, shutter_line, self.shutter_ctrl)
            except Exception as e:
                self.append_log(f"[{loop_id}] Controller init failed: {e}")
                shutter_line = addresses[loop_id].get("shutter_line")
                panel = InstrumentPanel(loop_id, None, shutter_line, self.shutter_ctrl)

            self.instrument_panels.append(panel)
            self.general_layout.addWidget(panel, i // 3, i % 3)

        if USE_PRESSURE_GAUGE:
            self.connection_monitor.add("Pressure")
            try:
                pg_ctrl = SimPressureGaugeController("Pressure") if SIMULATION else PressureGaugeController("Pressure")
                pg_ctrl.connect()
                pressure_panel = PressureGaugePanel("Pressure", pg_ctrl)
                # Pressure interlock (#8): without this, interlocked shutters stay closed.
                self.shutter_ctrl.pressure_gauge = pg_ctrl
            except Exception as e:
                self.append_log(f"[Pressure] Controller init failed: {e}")
                pressure_panel = PressureGaugePanel("Pressure", None)

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
            self.append_log(f"[Shutter] WARNING: pressure interlock BYPASSED in addresses.py "
                            f"for {sid}: it opens without pressure check.")

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
            lost = daq.state == LOST
            text = panel_text(daq)
            if (lost, text) != self._shutter_frame_mark:
                self._shutter_frame_mark = (lost, text)
                self._shutter_frame.setStyleSheet(
                    frame_style(SHUTTER_FRAME_STYLE, "shutterFrame", lost))
                self._daq_label.setText(f"{DAQ_NAME}: {text}" if text else "")
                self._daq_label.setStyleSheet(
                    f"color: {'#b02a37' if lost else '#146c43'}; font-weight: bold;")
                self._daq_label.setVisible(bool(text))

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
        """Create a small frame with 3 global shutter buttons in the lower-left of the General View."""
        from addresses import addresses  # already imported at top, but safe

        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setObjectName("shutterFrame")
        frame.setStyleSheet(frame_style(SHUTTER_FRAME_STYLE, "shutterFrame", lost=False))
        self._shutter_frame = frame
        self._shutter_frame_mark = None

        layout = QVBoxLayout(frame)
        title = QLabel("<b>Shutters</b>")
        layout.addWidget(title)
        # NI-DAQ connection status (#12)
        self._daq_label = QLabel("")
        self._daq_label.setWordWrap(True)
        self._daq_label.hide()
        layout.addWidget(self._daq_label)

        # Define shutters: label text, ID key in addresses["Shutters"]
        shutters = [
            ("Shutter Liquid N2", "Liquid_N2"),
            ("Shutter e-gun1", "eGun1"),
            ("Shutter e-gun2", "eGun2"),
        ]

        for label_text, shutter_id in shutters:
            btn = QPushButton(f"{label_text}: CLOSED")
            btn.setCheckable(True)
            btn.setStyleSheet("background-color: lightcoral;")

            # Restore last saved state (simulated or hardware)
            initial_state = self.shutter_ctrl.get_last_saved(shutter_id)
            btn.setChecked(initial_state)
            self.update_global_shutter_style(btn, label_text, initial_state)

            btn.clicked.connect(
                lambda checked, sid=shutter_id, prefix=label_text, b=btn: self.toggle_global_shutter(sid, prefix, b, checked)
            )

            layout.addWidget(btn)
            self.global_shutter_buttons[shutter_id] = (btn, label_text)

        #row = self.general_layout.rowCount()
        #self.general_layout.addWidget(frame, row, 0, 1, 1)

        # Put this frame in the lower-left of the grid (new last row, column 0)
        row = self.general_layout.count() // 3
        col = self.general_layout.count() % 3
        self.general_layout.addWidget(frame, row, col)

    def update_global_shutter_style(self, button: QPushButton, label_prefix: str, state: bool):
        """Update text and color of a global shutter button."""
        if state:
            button.setText(f"{label_prefix}: OPEN")
            button.setStyleSheet("background-color: lightgreen;")
        else:
            button.setText(f"{label_prefix}: CLOSED")
            button.setStyleSheet("background-color: lightcoral;")

    def toggle_global_shutter(self, shutter_id: str, label_prefix: str, button: QPushButton, state: bool):
        """Callback when a global shutter button is toggled."""
        from addresses import addresses  # to access line names

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
                    # --- START OF MODIFICATION ---

                    # Check if the panel is a PressureGaugePanel and handle it separately
                    if isinstance(panel, PressureGaugePanel):
                        if "P" in data:
                            panel.update_pressure(data['P'])
                    else:
                        # This is the existing logic for Eurotherm panels
                        if "T" in data:
                            panel.temp_label.setText(f"Temp: {data['T']:.1f} °C")
                        if "P" in data:
                            panel.power_label.setText(f"Power: {data['P']:.1f} %")
                        if "SP_T" in data:
                            panel.setpoint_temp.setText(f"Target Temp: {data['SP_T']:.1f} °C")
                        if "SP_P" in data:
                            panel.setpoint_power.setText(f"Target Power: {data['SP_P']:.1f} %")
                        if "MODE" in data:
                            panel.mode_label.setText(f"Mode: {data['MODE']}")
                        if "Ramp_T" in data:
                            panel.ramp_temp_value.setText(f"Ramp Temp: {data['Ramp_T']:.2f} °C/min")
                        if "Ramp_P" in data:
                            panel.ramp_power_value.setText(f"Ramp Power: {data['Ramp_P']:.2f} %/s")

                    # Universal status dot logic
                    if panel.controller:
                        # For pressure, the dot is handled in update_pressure. For Eurotherm, it's green.
                        if not isinstance(panel, PressureGaugePanel):
                            panel.set_status_dot("green")
                    else:
                        panel.set_status_dot("red")

                    # --- END OF MODIFICATION ---

                    # Update latest_data for logging
                    for key, value in data.items():
                        self.latest_data[f"{name}_{key}"] = value
                    break

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


if __name__ == "__main__":
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MBEControl.2026")
    daq_ok = initialize_daq_outputs()  # must be first DAQ call — sets all lines LOW
    app = QApplication(sys.argv)
    _icon = QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mbe_icon.ico"))
    app.setWindowIcon(_icon)
    window = MainWindow(daq_init_ok=daq_ok)
    window.setWindowIcon(_icon)
    window.show()
    sys.exit(app.exec())
