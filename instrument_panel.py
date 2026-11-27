# instrument_panel.py
"""
General View cards.

InstrumentPanel   one Eurotherm loop: name, large measured temperature, setpoint,
                  ramp rate, power, "Set temperature" and the cell shutter.
ShutterPanel      the shutters that do not belong to a loop (Liquid N2, e-guns...).
CardGrid          lays the cards out in as many columns as the window allows.

"Set temperature" follows the script rules: the change is checked by the script
compiler (limits, critical zone, time estimate) and executed by the same sequence
as temperature_ramp (temperature_control.py).
"""
import threading
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

import ui_theme as ui
from connection_monitor import LOST, panel_text
from pressure_interlock import InterlockError
from ramp_planning import plan_ramp_segments
from temperature_control import (
    check_manual_ramp, manual_limits_text, start_temperature_ramp,
)

AT_SETPOINT_TOLERANCE = 1.0     # °C: below this difference the loop is shown "At setpoint"
CARD_MIN_WIDTH = 330


def parse_number(text: str):
    """Number typed by the operator ("1100", "2,5", " 3.0 "), or None."""
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("caption", True)
    return label


def _separator() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {ui.BORDER}; border: none;")
    return line


class InstrumentPanel(QFrame):
    # From the thread that applies a temperature change.
    _log_line = pyqtSignal(str)
    _ramp_finished = pyqtSignal(object, str)      # (estimated end or None, message)

    def __init__(self, name, controller=None, shutter_line=None, shutter_ctrl=None,
                 addresses=None, log=None, use_shutter_hardware=True):
        super().__init__()
        self.name = name
        self.controller = controller
        if addresses is None:
            from machine_config import addresses
        self.addresses = addresses
        self.log = log or print
        self.use_shutter_hardware = use_shutter_hardware
        self.direct_setpoint = bool(addresses.get(name, {}).get("script_direct_setpoint", False))

        self._conn_mark = None        # last connection mark shown (#12)
        self._last_pv = None          # last measured temperature (start of a manual ramp)
        self._lost = False            # no communication: the values shown are not current
        self._locked_by = None
        self._applying = False        # a temperature change is being written
        self._rate_prefilled = False
        self._ramp_stop = threading.Event()   # stops the segment follow-up of the manual ramp
        self._follow_up_until = None  # end of a manual ramp that still has segments to write
        self._applied = None          # (text, color) shown after a change was applied
        self._log_line.connect(self.log)
        self._ramp_finished.connect(self._on_ramp_finished)

        self.setObjectName("instrumentPanel")
        self.setProperty("card", True)
        self.setMinimumWidth(CARD_MIN_WIDTH)
        # Preferred height: the cards of one row stretch to the same height.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(8)

        # ── Header: status dot, name, lock, mode ─────────────────────────
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(12, 12)
        self.set_status_dot("grey")
        self.name_label = QLabel(name)
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.name_label.setStyleSheet("font-size: 17pt; font-weight: 700; border: none;")
        self.lock_label = QLabel("")
        self.lock_label.setStyleSheet(ui.chip_style(ui.WARN, ui.WARN_SOFT))
        self.lock_label.hide()
        self.mode_label = QLabel("--")
        self.mode_label.setStyleSheet(ui.chip_style(ui.MUTED, ui.NEUTRAL_SOFT))
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(self.status_dot)
        header.addWidget(self.name_label)
        header.addStretch()
        header.addWidget(self.lock_label)
        header.addWidget(self.mode_label)
        layout.addLayout(header)

        # ── Measured temperature (large) and, beside it, where it is going ─
        self.temp_label = QLabel()
        self.temp_label.setTextFormat(Qt.TextFormat.RichText)
        self.temp_label.setStyleSheet("border: none;")
        self._show_temperature(None)
        self.trend_label = QLabel("")
        self.trend_label.hide()
        left = QVBoxLayout()
        left.setSpacing(4)
        left.addWidget(self.temp_label)
        trend_row = QHBoxLayout()
        trend_row.addWidget(self.trend_label)
        trend_row.addStretch()
        left.addLayout(trend_row)
        left.addStretch()

        self.setpoint_temp = self._value_label()
        self.ramp_temp_value = self._value_label()
        right = QVBoxLayout()
        right.setSpacing(1)
        right.addWidget(_caption("SETPOINT"))
        right.addWidget(self.setpoint_temp)
        right.addSpacing(6)
        right.addWidget(_caption("RAMP RATE"))
        right.addWidget(self.ramp_temp_value)
        right.addStretch()
        temp_row = QHBoxLayout()
        temp_row.setSpacing(12)
        temp_row.addLayout(left, 1)
        temp_row.addLayout(right)
        layout.addLayout(temp_row)

        # ── Output power: value, bar, target and power ramp rate ─────────
        self.power_label = self._value_label()
        self.power_label.setMinimumWidth(58)
        self.setpoint_power = QLabel("--")
        self.ramp_power_value = QLabel("")
        for label in (self.setpoint_power, self.ramp_power_value):
            label.setProperty("muted", True)
            label.setStyleSheet("border: none; font-size: 9pt;")
        self.setpoint_power.setToolTip("Output power setpoint (manual mode)")
        self.ramp_power_value.setToolTip("Output power ramp rate")
        self.power_bar = QProgressBar()
        self.power_bar.setRange(0, 1000)
        self.power_bar.setTextVisible(False)
        self.power_bar.setFixedHeight(6)
        self.power_bar.setStyleSheet(
            f"QProgressBar {{ background: {ui.NEUTRAL_SOFT}; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {ui.ACCENT}; border-radius: 3px; }}")
        power_row = QHBoxLayout()
        power_row.setSpacing(8)
        power_row.addWidget(_caption("POWER"))
        power_row.addWidget(self.power_label)
        power_row.addWidget(self.power_bar, 1)
        power_row.addWidget(self.setpoint_power)
        power_row.addWidget(self.ramp_power_value)
        layout.addLayout(power_row)

        # Connection status (#12): "⚠ No communication — last OK ..." / "Recovered at ..."
        self.comm_label = QLabel("")
        self.comm_label.setWordWrap(True)
        self.comm_label.hide()
        layout.addWidget(self.comm_label)

        # ── Set temperature ──────────────────────────────────────────────
        layout.addWidget(_separator())
        layout.addWidget(_caption("SET TEMPERATURE"))
        self.setpoint_input = QLineEdit()
        self.setpoint_input.setPlaceholderText("Target")
        self.setpoint_input.setToolTip("Target temperature (°C)")
        self.rate_input = QLineEdit()
        self.rate_input.setPlaceholderText("Rate")
        self.rate_input.setToolTip("Ramp rate (°C/min)")
        self.rate_unit = QLabel("°C/min")
        self.rate_unit.setProperty("muted", True)
        self.rate_unit.setStyleSheet("border: none;")
        target_unit = QLabel("°C")
        target_unit.setProperty("muted", True)
        target_unit.setStyleSheet("border: none;")
        self.set_temp_btn = QPushButton("Set")
        self.set_temp_btn.setProperty("primary", True)
        self.set_temp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_temp_btn.clicked.connect(self.set_temperature)
        self.setpoint_input.returnPressed.connect(self.set_temperature)
        self.rate_input.returnPressed.connect(self.set_temperature)
        self.setpoint_input.textEdited.connect(self._on_input_edited)
        self.rate_input.textEdited.connect(self._on_input_edited)
        set_row = QHBoxLayout()
        set_row.setSpacing(6)
        set_row.addWidget(self.setpoint_input, 3)
        set_row.addWidget(target_unit)
        set_row.addSpacing(4)
        set_row.addWidget(self.rate_input, 2)
        set_row.addWidget(self.rate_unit)
        set_row.addSpacing(4)
        set_row.addWidget(self.set_temp_btn)
        layout.addLayout(set_row)
        if self.direct_setpoint:
            self.rate_input.hide()
            self.rate_unit.hide()
        # What the change will do (limits, plan, warnings) or why it is refused.
        self.hint_label = QLabel("")
        self.hint_label.setWordWrap(True)
        self.hint_label.setMinimumHeight(30)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.hint_label)
        layout.addStretch()

        # ── Cell shutter ─────────────────────────────────────────────────
        self.shutter_line = shutter_line
        self.shutter_ctrl = shutter_ctrl
        self.shutter_state = False
        self.shutter_btn = QPushButton("Shutter: CLOSED")
        self.shutter_btn.setCheckable(True)
        self.shutter_btn.setChecked(False)
        self.shutter_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shutter_btn.clicked.connect(self.toggle_shutter)
        layout.addWidget(self.shutter_btn)
        if not shutter_line:
            self.shutter_btn.hide()       # this loop has no shutter
        elif self.use_shutter_hardware and self.shutter_ctrl:
            self.shutter_state = self.shutter_ctrl.get_last_saved(self.name)
            self.shutter_btn.setChecked(self.shutter_state)
        self.update_shutter_style()
        self._refresh_set_temperature()

    # ── Display ──────────────────────────────────────────────────────────

    @staticmethod
    def _value_label() -> QLabel:
        label = QLabel("--")
        label.setStyleSheet("border: none; font-size: 11pt; font-weight: 600;")
        return label

    def _show_temperature(self, value, stale=False):
        """Large measured temperature; greyed when it is the last value of a lost instrument."""
        number = "--" if value is None else f"{value:.1f}"
        color = ui.FAINT if stale else ui.TEXT
        self.temp_label.setToolTip("Last value received: no communication" if stale else "")
        self.temp_label.setText(
            f'<span style="font-size:32pt; font-weight:600; color:{color};">{number}</span>'
            f'<span style="font-size:15pt; color:{ui.MUTED};"> °C</span>')

    def update_data(self, data: dict):
        """Show the values of one poll ({"T", "P", "SP_T", "SP_P", "MODE", "Ramp_T", "Ramp_P"})."""
        if "T" in data:
            self._last_pv = data["T"]
            self._lost = False
            self._show_temperature(data["T"])
        if "P" in data:
            self.power_label.setText(f"{data['P']:.1f} %")
            self.power_bar.setValue(int(max(0.0, min(100.0, data["P"])) * 10))
        if "SP_T" in data:
            self.setpoint_temp.setText(f"{data['SP_T']:.1f} °C")
        if "SP_P" in data:
            self.setpoint_power.setText(f"target {data['SP_P']:.1f} %")
        if "MODE" in data:
            manual = data["MODE"] == "manual"
            self.mode_label.setText(str(data["MODE"]).upper())
            self.mode_label.setStyleSheet(ui.chip_style(ui.WARN, ui.WARN_SOFT) if manual
                                          else ui.chip_style(ui.ACCENT, ui.ACCENT_SOFT))
        if "Ramp_T" in data:
            self.ramp_temp_value.setText(f"{data['Ramp_T']:.2f} °C/min")
            if not self._rate_prefilled and not self.rate_input.text() and data["Ramp_T"] > 0:
                # Start from the rate the controller currently uses.
                self._rate_prefilled = True
                self.rate_input.setText(f"{data['Ramp_T']:g}")
        if "Ramp_P" in data:
            self.ramp_power_value.setText(f"· {data['Ramp_P']:.2f} %/s")
        if {"T", "SP_T", "MODE"} <= data.keys():
            self._show_trend(data["T"], data["SP_T"], data["MODE"])
        if "T" in data and self.setpoint_input.text():
            self._refresh_set_temperature()      # the plan starts from the measured temperature

    def _show_trend(self, pv, setpoint, mode):
        if mode == "manual":
            text, style = "Manual power", ui.chip_style(ui.WARN, ui.WARN_SOFT)
        elif abs(setpoint - pv) <= AT_SETPOINT_TOLERANCE:
            text, style = "● At setpoint", ui.chip_style(ui.OK, ui.OK_SOFT)
        elif setpoint > pv:
            text, style = "▲ Heating", ui.chip_style(ui.HEAT, ui.HEAT_SOFT)
        else:
            text, style = "▼ Cooling", ui.chip_style(ui.COOL, ui.COOL_SOFT)
        self.trend_label.setText(text)
        self.trend_label.setStyleSheet(style)
        self.trend_label.show()

    def set_status_dot(self, color):
        colors = {"green": "#22A559", "red": ui.LOST, "grey": "#B6BFCB"}
        self.status_dot.setStyleSheet(
            f"background-color: {colors.get(color, color)}; border: none; border-radius: 6px;")

    def set_connection(self, status):
        """Red border + ⚠ + last OK time while lost; "Recovered" until acknowledged (#12)."""
        lost = status.state == LOST
        text = panel_text(status)
        if (lost, text) == self._conn_mark:
            return
        self._conn_mark = (lost, text)
        self.setStyleSheet(
            f"QFrame#instrumentPanel {{ border: 2px solid {ui.LOST}; }}" if lost else "")
        self.comm_label.setText(text)
        self.comm_label.setStyleSheet(
            f"color: {'#b02a37' if lost else '#146c43'}; font-weight: bold; border: none;")
        self.comm_label.setVisible(bool(text))
        if lost:
            self.set_status_dot("red")
            self.trend_label.hide()
            self._lost = True
            self._show_temperature(self._last_pv, stale=True)   # never look like a live value
            self._refresh_set_temperature()

    # ── Lock by a running script ─────────────────────────────────────────

    def set_locked(self, owner: str | None):
        """Disable "Set temperature" and show who holds the loop while a script uses it."""
        if owner == self._locked_by:
            return
        self._locked_by = owner
        # The shutter button is locked separately (set_shutter_display), only
        # when the script actually opens/closes this shutter.
        if owner is not None:
            self.lock_label.setText(f"🔒 {owner}")
            self.lock_label.setToolTip(f"Locked by {owner}")
            self.lock_label.show()
            if self.follow_up_pending():
                # The script now drives this loop: the manual ramp must not write
                # its next segment over the script's own commands.
                self.stop_follow_up()
                self.log(f"[{self.name}] Manual ramp follow-up stopped: "
                         f"loop taken by script '{owner}'.")
        else:
            self.lock_label.setText("")
            self.lock_label.setToolTip("")
            self.lock_label.hide()
        self._refresh_set_temperature()

    # ── Set temperature ──────────────────────────────────────────────────

    def follow_up_pending(self) -> bool:
        """
        True while a manual ramp across the critical zone is in progress: its next
        segments are written by a background thread of this program.
        """
        return self._follow_up_until is not None and datetime.now() < self._follow_up_until

    def stop_follow_up(self):
        """Stop writing the next segments of the manual ramp (the hardware keeps its setpoint)."""
        self._ramp_stop.set()
        self._follow_up_until = None

    def _has_several_segments(self, check) -> bool:
        zone = self.addresses.get(self.name, {}).get("critical_zone")
        if check.direct or not zone:
            return False
        if self._last_pv is None or self._lost:
            return True         # unknown start: assume the zone is crossed
        return len(plan_ramp_segments(self._last_pv, check.target, check.script_rate, zone)) > 1

    def _on_input_edited(self, _text):
        self._applied = None
        self._refresh_set_temperature()

    def _check(self):
        """Compile the change typed in the fields (None when no target is typed)."""
        if not self.setpoint_input.text().strip():
            return None
        return check_manual_ramp(
            self.name, parse_number(self.setpoint_input.text()),
            parse_number(self.rate_input.text()),
            addresses=self.addresses,
            current_temp=None if self._lost else self._last_pv)   # lost: start unknown

    def _refresh_set_temperature(self):
        """Enable/disable the fields and say what "Set" will do, or why it cannot."""
        locked = self._locked_by is not None
        usable = self.controller is not None and not locked and not self._applying
        self.setpoint_input.setEnabled(usable)
        self.rate_input.setEnabled(usable)
        check = self._check() if usable else None
        self.set_temp_btn.setEnabled(usable and check is not None and check.ok)
        ui.set_prop(self.setpoint_input, "invalid", bool(check is not None and not check.ok))

        if self.controller is None:
            self._hint("No controller: this instrument could not be initialized.", ui.ERROR)
        elif locked:
            self._hint(f"Controlled by script '{self._locked_by}'.", ui.WARN)
        elif self._applying:
            self._hint("Writing to the controller…", ui.MUTED)
        elif check is None:
            if self._applied:
                self._hint(*self._applied)
            else:
                self._hint(manual_limits_text(self.addresses, self.name), ui.FAINT)
        elif not check.ok:
            self._hint(check.summary(), ui.ERROR)
        else:
            self._hint(check.summary(), ui.WARN if check.warnings else ui.TEXT)

    def _hint(self, text: str, color: str):
        self.hint_label.setText(text)
        self.hint_label.setStyleSheet(f"color: {color}; border: none; font-size: 9pt;")

    def set_temperature(self):
        """
        Apply the typed temperature change: checked like a script line, then
        executed by the script's temperature_ramp sequence in a background thread.
        """
        if self.controller is None or self._locked_by is not None or self._applying:
            return
        check = self._check()
        if check is None or not check.ok:
            self._refresh_set_temperature()
            return

        # A new ramp replaces the previous manual one: stop its segment follow-up.
        self.stop_follow_up()
        self._ramp_stop = stop_event = threading.Event()
        self._several_segments = self._has_several_segments(check)
        self._applying = True
        self._applied = None
        self._refresh_set_temperature()
        self.log(f"[{self.name}] Set temperature (General View): {check.command} — {check.summary()}")

        ctrl, name, addresses = self.controller, self.name, self.addresses

        def apply():
            try:
                finish = start_temperature_ramp(
                    ctrl, name, check.target, check.script_rate,
                    addresses=addresses, log=self._log_line.emit, stop_event=stop_event)
            except Exception as e:
                self._log_line.emit(f"[{name}] Set temperature failed: {e}")
                self._ramp_finished.emit(None, f"Not applied: {e}")
                return
            if finish is None and not check.direct:
                # Aborted by the sequence, or already at target: the log says which.
                self._ramp_finished.emit(None, "No ramp started (see the log below).")
            else:
                self._ramp_finished.emit(finish, "")

        self._pending_summary = "✓ " + check.summary()
        threading.Thread(target=apply, daemon=True).start()

    def _on_ramp_finished(self, finish, problem):
        self._applying = False
        self._follow_up_until = finish if self._several_segments else None
        if problem:
            self._applied = (problem, ui.ERROR if problem.startswith("Not applied") else ui.WARN)
        else:
            self._applied = (self._pending_summary, ui.OK)
        self.setpoint_input.clear()         # never leave a target ready to be re-applied
        self._refresh_set_temperature()

    # ── Shutter ──────────────────────────────────────────────────────────

    def set_shutter_display(self, state: bool, owner: str | None):
        """Show the real shutter state; disable the button while a script controls it."""
        self.shutter_state = state
        self.shutter_btn.setChecked(state)
        self.update_shutter_style()
        self.shutter_btn.setEnabled(owner is None)
        self.shutter_btn.setToolTip(f"🔒 Controlled by {owner}" if owner else "")

    def toggle_shutter(self):
        requested = self.shutter_btn.isChecked()
        if self.use_shutter_hardware and self.shutter_ctrl and self.shutter_line:
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
        text = "Shutter: OPEN" if self.shutter_state else "Shutter: CLOSED"
        style = ui.shutter_style(self.shutter_state)
        if self.shutter_btn.text() != text:
            self.shutter_btn.setText(text)
        if self.shutter_btn.styleSheet() != style:
            self.shutter_btn.setStyleSheet(style)


class ShutterPanel(QFrame):
    """Card with one button per shutter that does not belong to a loop."""

    def __init__(self):
        super().__init__()
        self.setObjectName("shutterFrame")
        self.setProperty("card", True)
        self._conn_mark = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("Shutters")
        title.setStyleSheet("font-size: 13pt; font-weight: 700; border: none;")
        layout.addWidget(title)
        # NI-DAQ connection status (#12)
        self.daq_label = QLabel("")
        self.daq_label.setWordWrap(True)
        self.daq_label.hide()
        layout.addWidget(self.daq_label)
        self.buttons_layout = QGridLayout()
        self.buttons_layout.setSpacing(8)
        layout.addLayout(self.buttons_layout)
        self._count = 0

    def add_button(self, button: QPushButton):
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumWidth(200)
        self.buttons_layout.addWidget(button, self._count // 4, self._count % 4)
        self._count += 1

    def set_connection(self, daq_name: str, status):
        lost = status.state == LOST
        text = panel_text(status)
        if (lost, text) == self._conn_mark:
            return
        self._conn_mark = (lost, text)
        self.setStyleSheet(f"QFrame#shutterFrame {{ border: 2px solid {ui.LOST}; }}" if lost else "")
        self.daq_label.setText(f"{daq_name}: {text}" if text else "")
        self.daq_label.setStyleSheet(
            f"color: {'#b02a37' if lost else '#146c43'}; font-weight: bold; border: none;")
        self.daq_label.setVisible(bool(text))


class CardGrid(QWidget):
    """Cards in a grid whose number of columns follows the available width."""

    def __init__(self, min_card_width=CARD_MIN_WIDTH, max_columns=4):
        super().__init__()
        self._min = min_card_width
        self._max_columns = max_columns
        self._cards = []          # (widget, full_width)
        self._columns = 0
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(14)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

    def add_card(self, widget, full_width=False):
        self._cards.append((widget, full_width))
        self._relayout(force=True)

    def columns_for(self, width: int) -> int:
        spacing = self._layout.spacing()
        return max(1, min(self._max_columns, (width + spacing) // (self._min + spacing)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self, force=False):
        columns = self.columns_for(self.width())
        if columns == self._columns and not force:
            return
        self._columns = columns
        for widget, _ in self._cards:
            self._layout.removeWidget(widget)
        row = col = 0
        for widget, full_width in self._cards:
            if full_width:
                if col:
                    row, col = row + 1, 0
                self._layout.addWidget(widget, row, 0, 1, columns)
                row += 1
            else:
                self._layout.addWidget(widget, row, col)
                col += 1
                if col == columns:
                    row, col = row + 1, 0
        for c in range(self._max_columns):
            self._layout.setColumnStretch(c, 1 if c < columns else 0)
