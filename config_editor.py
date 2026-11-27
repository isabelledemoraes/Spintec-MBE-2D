# config_editor.py
"""
"⚙ Configuration" tab: password-protected editor of config/machine.yaml.

    ConfigTab        lock page  <->  ConfigEditor (after the password)
    ConfigEditor     navigation (Machine, Pressure gauge, NI-DAQ, one entry per
                     cell and per shutter) + one form page per entry

Nothing is applied to the running program: Save validates the whole
configuration (machine_config.validate), writes the file (previous version kept
as machine.yaml.bak), logs every changed value, and the change takes effect at
the next start ("Save and restart").

The password keeps operators out of the settings. It does not protect the file
itself: see machine_config (security section).
"""
import copy
import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

import machine_config as mc
import ui_theme as ui

MAX_LOGGED_CHANGES = 40


# ── Fields: one value of the configuration <-> one widget ────────────────────

class FieldError(ValueError):
    pass


class Field:
    """Base: self.widget shows the value; get() returns it or raises FieldError."""
    def __init__(self, label: str):
        self.label = label
        self.on_change = None

    def _changed(self, *_):
        if self.on_change:
            self.on_change()


class TextField(Field):
    def __init__(self, label, value, optional=False, placeholder=""):
        super().__init__(label)
        self.optional = optional
        self.widget = QLineEdit("" if value is None else str(value))
        self.widget.setPlaceholderText(placeholder)
        self.widget.setMaximumWidth(520)
        self.widget.textEdited.connect(self._changed)

    def get(self):
        text = self.widget.text().strip()
        if not text:
            if self.optional:
                return None
            raise FieldError(f"{self.label} is required")
        return text


class NumberField(Field):
    """Integer or decimal number typed as text (no spin box: no change by mouse wheel)."""
    def __init__(self, label, value, optional=False, integer=False, unit=""):
        super().__init__(label)
        self.optional, self.integer = optional, integer
        self.edit = QLineEdit(self.format(value))
        self.edit.setPlaceholderText("not set" if optional else "")
        self.edit.setMaximumWidth(140)
        self.edit.textEdited.connect(self._changed)
        self.widget = QWidget()
        row = QHBoxLayout(self.widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.edit)
        if unit:
            unit_label = QLabel(unit)
            unit_label.setProperty("muted", True)
            row.addWidget(unit_label)
        row.addStretch()

    @staticmethod
    def format(value) -> str:
        if value is None:
            return ""
        return repr(value) if isinstance(value, float) else str(value)

    def get(self):
        text = self.edit.text().strip().replace(",", ".")
        if not text:
            if self.optional:
                return None
            raise FieldError(f"{self.label} is required")
        try:
            if self.integer or not any(c in text for c in ".eE"):
                return int(text)
            return float(text)
        except ValueError:
            kind = "an integer" if self.integer else "a number"
            raise FieldError(f"{self.label}: '{text}' is not {kind}") from None


class BoolField(Field):
    def __init__(self, label, value, text=""):
        super().__init__(label)
        self.widget = QCheckBox(text)
        self.widget.setChecked(bool(value))
        self.widget.toggled.connect(self._changed)

    def get(self):
        return self.widget.isChecked()


class ChoiceField(Field):
    def __init__(self, label, value, choices):
        super().__init__(label)
        self.choices = list(choices)
        self.widget = _NoWheelCombo()
        self.widget.addItems([str(c) for c in self.choices])
        if value in self.choices:
            self.widget.setCurrentIndex(self.choices.index(value))
        self.widget.setMaximumWidth(140)
        self.widget.currentIndexChanged.connect(self._changed)

    def get(self):
        return self.choices[self.widget.currentIndex()]


class _NoWheelCombo(QComboBox):
    def wheelEvent(self, event):
        event.ignore()          # scrolling the page must never change a setting


class EscapedTextField(TextField):
    """Text with control characters shown as \\r \\n (gauge command)."""
    def __init__(self, label, value):
        super().__init__(label, (value or "").encode("unicode_escape").decode("ascii"))

    def get(self):
        text = self.widget.text()
        if not text.strip():
            raise FieldError(f"{self.label} is required")
        try:
            return text.encode("ascii").decode("unicode_escape")
        except (UnicodeError, ValueError):
            raise FieldError(f"{self.label}: only ASCII characters and \\r \\n escapes") from None


# ── Page building blocks ─────────────────────────────────────────────────────

def _note(text: str, color: str = ui.MUTED) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {color}; font-size: 9pt; border: none;")
    return label


class Page(QScrollArea):
    """One entry of the configuration: cards of labelled fields."""
    changed = pyqtSignal()

    def __init__(self, title: str, subtitle: str = ""):
        super().__init__()
        self.setWidgetResizable(True)
        self.fields = []
        body = QWidget()
        self.setWidget(body)
        self._layout = QVBoxLayout(body)
        self._layout.setContentsMargins(4, 0, 12, 12)
        self._layout.setSpacing(14)
        self.title_label = QLabel(title)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setProperty("title", True)
        self._layout.addWidget(self.title_label)
        if subtitle:
            self._layout.addWidget(_note(subtitle))
        self._layout.addStretch()

    def card(self, heading: str, note: str = "") -> QFormLayout:
        frame = QFrame()
        frame.setProperty("card", True)
        box = QVBoxLayout(frame)
        box.setContentsMargins(18, 14, 18, 16)
        box.setSpacing(8)
        title = QLabel(heading)
        title.setProperty("heading", True)
        title.setStyleSheet("border: none;")
        box.addWidget(title)
        if note:
            box.addWidget(_note(note))
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(form)
        self._layout.insertWidget(self._layout.count() - 1, frame)
        return form

    def add(self, form: QFormLayout, field: Field, show_label=True):
        field.on_change = self.changed.emit
        self.fields.append(field)
        if show_label:
            label = QLabel(field.label)
            label.setStyleSheet("border: none;")
            form.addRow(label, field.widget)
        else:
            form.addRow(field.widget)
        return field


def _get(field: Field, where: str, errors: list):
    try:
        return field.get()
    except FieldError as e:
        errors.append(f"{where}: {e}")
        return None


class MachinePage(Page):
    nav_text = "Machine"

    def __init__(self, config):
        super().__init__("Machine", "Identity of this MBE system and where its runs are archived.")
        m = config["machine"]
        form = self.card("Identity")
        self.name = self.add(form, TextField("Machine name", m["name"], optional=True,
                                             placeholder="e.g. mbe1"))
        form.addRow(_note("Written in the header of every run report. "
                          "Run is blocked until it is set."))
        form = self.card("Operating mode")
        self.simulation = self.add(form, BoolField(
            "Simulation", m["simulation"],
            "Simulation mode (simulated instruments, no hardware)"), show_label=False)
        form.addRow(_note("Unchecked on the instrument PC: the program then drives the real "
                          "Eurotherm controllers, pressure gauge and NI-DAQ shutters.", ui.WARN))
        form = self.card("Run archive")
        self.archive = self.add(form, TextField("Archive folder", m["script_archive_dir"],
                                                optional=True, placeholder=r"e.g. D:\MBE\run_archive"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        form.addRow("", browse)
        form.addRow(_note("The exact script and its compile report are saved there before each "
                          "Run. A relative folder is inside the MBEControl folder. "
                          "Run is blocked until it is set."))
        form = self.card("Safety")
        self.cap = self.add(form, NumberField("Absolute max ramp rate",
                                              config["safety"]["max_temp_rate_cap"], unit="°C/min"))
        form.addRow(_note("Cap applied to every temperature ramp at run time, whatever the "
                          "script or the limit of the cell says."))

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Run archive folder")
        if folder:
            self.archive.widget.setText(os.path.normpath(folder))
            self.changed.emit()

    def collect(self, config, errors):
        w = "Machine"
        config["machine"] = {"name": _get(self.name, w, errors),
                             "simulation": self.simulation.get(),
                             "script_archive_dir": _get(self.archive, w, errors)}
        config["safety"] = {"max_temp_rate_cap": _get(self.cap, w, errors)}


class GaugePage(Page):
    nav_text = "Pressure gauge"

    def __init__(self, config):
        super().__init__("Pressure gauge", "Serial gauge read every second (Pressure tab, "
                                           "CSV log, pressure interlock of the shutters).")
        g = config["pressure_gauge"]
        form = self.card("Gauge")
        self.enabled = self.add(form, BoolField("Enabled", g["enabled"], "A pressure gauge is connected"),
                                show_label=False)
        form.addRow(_note("Without a gauge, shutters with a pressure interlock cannot be opened."))
        form = self.card("Serial link")
        self.port = self.add(form, TextField("Port", g["port"], placeholder="e.g. COM10"))
        self.baudrate = self.add(form, NumberField("Baud rate", g["baudrate"], integer=True))
        self.bytesize = self.add(form, ChoiceField("Data bits", g["bytesize"], (5, 6, 7, 8)))
        self.stopbits = self.add(form, ChoiceField("Stop bits", g["stopbits"], (1, 1.5, 2)))
        form = self.card("Reading")
        self.command = self.add(form, EscapedTextField("Command", g["command"]))
        form.addRow(_note(r"ASCII command sent to read the pressure, e.g.  DS IG1 \r\n"))
        self.unit = self.add(form, TextField("Unit", g["unit"], placeholder="mbar"))
        form.addRow(_note("Must match the setting of the gauge controller. "
                          "The pressure interlock only works in mbar."))

    def collect(self, config, errors):
        w = "Pressure gauge"
        config["pressure_gauge"] = {
            "enabled": self.enabled.get(), "port": _get(self.port, w, errors),
            "baudrate": _get(self.baudrate, w, errors), "bytesize": self.bytesize.get(),
            "stopbits": self.stopbits.get(), "command": _get(self.command, w, errors),
            "unit": _get(self.unit, w, errors)}


class DaqPage(Page):
    nav_text = "NI-DAQ"

    def __init__(self, config):
        super().__init__("NI-DAQ", "Digital output card that drives the shutters.")
        d = config["daq"]
        form = self.card("Device")
        self.device = self.add(form, TextField("Device name", d["device"], placeholder="e.g. Dev1"))
        form.addRow(_note("As shown in NI MAX → Devices and Interfaces."))
        self.ports = self.add(form, NumberField("Ports", d["ports"], integer=True))
        form.addRow(_note("Number of ports set LOW (all shutters closed) when the program "
                          "starts. USB-6501: 3 ports = 24 lines."))

    def collect(self, config, errors):
        config["daq"] = {"device": _get(self.device, "NI-DAQ", errors),
                         "ports": _get(self.ports, "NI-DAQ", errors)}


class LoopPage(Page):
    """One Eurotherm loop (cell, substrate heater...)."""
    renamed = pyqtSignal()

    def __init__(self, name: str, loop: dict):
        super().__init__(name, "Eurotherm temperature controller, read over Modbus RTU.")
        self.original = copy.deepcopy(loop)      # keeps what the form does not show
        form = self.card("Name")
        self.name = self.add(form, TextField("Name", name))
        self.name.widget.textEdited.connect(self._on_name)
        form.addRow(_note("Shown in the General View and used in scripts, e.g. "
                          "temperature_ramp(\"Au\", …), in the CSV columns and in the dashboard. "
                          "Renaming a cell means updating the scripts that use it. "
                          "Letters, digits and '-' only."))

        form = self.card("Communication")
        self.port = self.add(form, TextField("Serial port", loop["port"], placeholder="e.g. COM1"))
        self.slave = self.add(form, NumberField("Modbus slave", loop["slave"], integer=True))
        self.baudrate = self.add(form, NumberField("Baud rate", loop["baudrate"], integer=True))

        form = self.card("Shutter")
        self.shutter = self.add(form, TextField("NI-DAQ line", loop["shutter_line"], optional=True,
                                                placeholder="e.g. Dev1/port0/line2 — empty = no shutter"))

        limits = loop["limits"]
        form = self.card("Limits", "Checked by Compile for scripts and by “Set temperature” in "
                                   "the General View. A limit left empty blocks the commands "
                                   "that need it.")
        self.max_temp = self.add(form, NumberField("Max temperature", limits["max_temp"],
                                                   optional=True, unit="°C"))
        self.min_temp = self.add(form, NumberField("Min temperature", limits["min_temp"],
                                                   optional=True, unit="°C  (optional)"))
        self.max_rate = self.add(form, NumberField("Max ramp rate", limits["max_temp_rate"],
                                                   optional=True, unit="°C/min"))
        self.power_allowed = self.add(form, BoolField(
            "power_ramp", limits["power_ramp_allowed"], "power_ramp allowed in scripts"),
            show_label=False)
        self.min_power_rate = self.add(form, NumberField("Min power ramp rate",
                                                         limits["min_power_rate"],
                                                         optional=True, unit="%/s"))

        zone = loop["critical_zone"] or {}
        rng = zone.get("range") or [None, None]
        form = self.card("Critical zone", "Temperature range crossed slowly (e.g. around a "
                                          "melting point): inside it a ramp never goes faster "
                                          "than the slow rate.")
        self.zone_on = self.add(form, BoolField("Critical zone", bool(loop["critical_zone"]),
                                                "This cell has a critical zone"), show_label=False)
        self.zone_from = self.add(form, NumberField("From", rng[0], optional=True, unit="°C"))
        self.zone_to = self.add(form, NumberField("To", rng[1], optional=True, unit="°C"))
        self.zone_rate = self.add(form, NumberField("Slow rate", zone.get("slow_rate"),
                                                    optional=True, unit="°C/min"))
        self.zone_on.widget.toggled.connect(self._zone_enabled)
        self._zone_enabled(self.zone_on.get())

        form = self.card("Temperature changes")
        self.direct = self.add(form, BoolField(
            "Direct setpoint", loop["script_direct_setpoint"],
            "Write the setpoint directly (no ramp rate, no ramp generator changes)"),
            show_label=False)
        self.keep_ramp = self.add(form, BoolField(
            "Keep ramp generator", loop["script_keep_ramp_generator"],
            "Keep the ramp generator state (restored after a ramp instead of forced OFF)"),
            show_label=False)

        form = self.card("Modbus registers", "Register address and number of decimals of "
                                             "each value (Eurotherm manual).")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for col, text in enumerate(("Value", "Address", "Decimals")):
            head = QLabel(text)
            head.setProperty("caption", True)
            grid.addWidget(head, 0, col)
        self.registers = {}
        for row, (reg, help_text) in enumerate(mc.REGISTERS.items(), start=1):
            pair = loop["registers"].get(reg) or [None, None]
            label = QLabel(f"{help_text}")
            label.setToolTip(reg)
            label.setStyleSheet("border: none;")
            address = NumberField(f"register {reg} address", pair[0], integer=True)
            decimals = NumberField(f"register {reg} decimals", pair[1], integer=True)
            for field in (address, decimals):
                field.on_change = self.changed.emit
                field.edit.setMaximumWidth(90)
                self.fields.append(field)
            grid.addWidget(label, row, 0)
            grid.addWidget(address.widget, row, 1)
            grid.addWidget(decimals.widget, row, 2)
            self.registers[reg] = (address, decimals)
        grid.setColumnStretch(0, 1)
        form.addRow(grid)

    def _on_name(self, text):
        self.title_label.setText(text.strip() or "(no name)")
        self.renamed.emit()

    def _zone_enabled(self, enabled):
        for field in (self.zone_from, self.zone_to, self.zone_rate):
            field.edit.setEnabled(enabled)

    def current_name(self) -> str:
        return self.name.widget.text().strip()

    def collect(self, errors) -> tuple[str, dict]:
        name = self.current_name()
        w = f"Cell {name or '(no name)'}"
        loop = copy.deepcopy(self.original)
        loop.update(port=_get(self.port, w, errors), slave=_get(self.slave, w, errors),
                    baudrate=_get(self.baudrate, w, errors),
                    shutter_line=_get(self.shutter, w, errors),
                    script_direct_setpoint=self.direct.get(),
                    script_keep_ramp_generator=self.keep_ramp.get())
        loop["limits"].update(max_temp=_get(self.max_temp, w, errors),
                              min_temp=_get(self.min_temp, w, errors),
                              max_temp_rate=_get(self.max_rate, w, errors),
                              power_ramp_allowed=self.power_allowed.get(),
                              min_power_rate=_get(self.min_power_rate, w, errors))
        if self.zone_on.get():
            loop["critical_zone"] = {
                "range": [_get(self.zone_from, w, errors), _get(self.zone_to, w, errors)],
                "slow_rate": _get(self.zone_rate, w, errors)}
        else:
            loop["critical_zone"] = None
        for reg, (address, decimals) in self.registers.items():
            loop["registers"][reg] = [_get(address, w, errors), _get(decimals, w, errors)]
        return name, loop


class ShutterPage(Page):
    """A shutter that does not belong to a Eurotherm loop."""
    renamed = pyqtSignal()

    def __init__(self, name: str, shutter: dict):
        super().__init__(name, "NI-DAQ digital output, shown as a button in the General View.")
        form = self.card("Name")
        self.name = self.add(form, TextField("Name", name))
        self.name.widget.textEdited.connect(self._on_name)
        form.addRow(_note("Used in scripts: open_shutter(\"…\") / close_shutter(\"…\"). "
                          "Letters, digits, '-' and '_' only."))
        self.label_field = self.add(form, TextField("Button label", shutter["label"], optional=True,
                                                    placeholder="text of the General View button"))
        form = self.card("Output")
        self.line = self.add(form, TextField("NI-DAQ line", shutter["line"],
                                             placeholder="e.g. Dev1/port0/line7"))
        interlock = shutter["pressure_interlock"]
        if not isinstance(interlock, dict):
            interlock = {}          # absent, or written wrongly in the file (limit to retype)
        form = self.card("Pressure interlock", "The shutter opens only while the gauge reads "
                                               "strictly below the limit (checked at each "
                                               "opening, from a script or from the button).")
        self.interlock_on = self.add(form, BoolField(
            "Interlock", bool(shutter["pressure_interlock"]),
            "This shutter has a pressure interlock"), show_label=False)
        self.limit = self.add(form, NumberField("Max pressure", interlock.get("max_pressure_mbar"),
                                                optional=True, unit="mbar"))
        self.bypass = self.add(form, BoolField(
            "Bypass", interlock.get("bypass", False),
            "Bypass: open WITHOUT any pressure check (maintenance only)"), show_label=False)
        self.bypass.widget.setStyleSheet(f"color: {ui.ERROR};")
        self.interlock_on.widget.toggled.connect(self._interlock_enabled)
        self._interlock_enabled(self.interlock_on.get())

    def _on_name(self, text):
        self.title_label.setText(text.strip() or "(no name)")
        self.renamed.emit()

    def _interlock_enabled(self, enabled):
        self.limit.edit.setEnabled(enabled)
        self.bypass.widget.setEnabled(enabled)

    def current_name(self) -> str:
        return self.name.widget.text().strip()

    def collect(self, errors) -> tuple[str, dict]:
        name = self.current_name()
        w = f"Shutter {name or '(no name)'}"
        shutter = {"label": _get(self.label_field, w, errors) or "",
                   "line": _get(self.line, w, errors), "pressure_interlock": None}
        if self.interlock_on.get():
            shutter["pressure_interlock"] = {"max_pressure_mbar": _get(self.limit, w, errors),
                                             "bypass": self.bypass.get()}
        return name, shutter


# ── Editor ───────────────────────────────────────────────────────────────────

class ConfigEditor(QWidget):
    """Navigation + pages + Save bar, for one configuration file."""
    lock_requested = pyqtSignal()

    def __init__(self, config: dict, path: str, log, scripts_running, request_restart):
        super().__init__()
        self.path = path
        self.log = log
        self.scripts_running = scripts_running
        self.request_restart = request_restart
        self.saved = copy.deepcopy(config)     # content of the file
        self.dirty = False
        self.saved_once = False                # a save happened since the program started

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 10, 4, 4)
        outer.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("System configuration")
        title.setProperty("title", True)
        top.addWidget(title)
        path_label = QLabel(path)
        path_label.setProperty("muted", True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(path_label)
        top.addStretch()
        self.password_btn = QPushButton("Change password…")
        self.password_btn.clicked.connect(self.change_password)
        top.addWidget(self.password_btn)
        self.lock_btn = QPushButton("🔒 Lock")
        self.lock_btn.clicked.connect(self.lock_requested.emit)
        top.addWidget(self.lock_btn)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(14)
        side = QVBoxLayout()
        side.setSpacing(6)
        self.nav = QListWidget()
        self.nav.setFixedWidth(230)
        self.nav.currentItemChanged.connect(self._on_nav)
        side.addWidget(self.nav, 1)
        row = QHBoxLayout()
        self.add_loop_btn = QPushButton("+ Cell")
        self.add_loop_btn.setToolTip("Add a Eurotherm loop")
        self.add_loop_btn.clicked.connect(self.add_loop)
        self.add_shutter_btn = QPushButton("+ Shutter")
        self.add_shutter_btn.clicked.connect(self.add_shutter)
        row.addWidget(self.add_loop_btn)
        row.addWidget(self.add_shutter_btn)
        side.addLayout(row)
        row = QHBoxLayout()
        self.up_btn = QPushButton("↑")
        self.up_btn.setToolTip("Move up (display order)")
        self.up_btn.clicked.connect(lambda: self.move_current(-1))
        self.down_btn = QPushButton("↓")
        self.down_btn.setToolTip("Move down (display order)")
        self.down_btn.clicked.connect(lambda: self.move_current(1))
        self.duplicate_btn = QPushButton("Duplicate")
        self.duplicate_btn.clicked.connect(self.duplicate_current)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setProperty("danger", True)
        self.delete_btn.clicked.connect(self.delete_current)
        for button in (self.up_btn, self.down_btn, self.duplicate_btn, self.delete_btn):
            row.addWidget(button)
        side.addLayout(row)
        body.addLayout(side)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)

        bottom = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        bottom.addWidget(self.status_label, 1)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.setToolTip("Discard the changes and reload the file")
        self.revert_btn.clicked.connect(self.revert)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(lambda: self.save())
        self.restart_btn = QPushButton("Save and restart")
        self.restart_btn.setProperty("primary", True)
        self.restart_btn.clicked.connect(lambda: self.save(restart=True))
        for button in (self.revert_btn, self.save_btn, self.restart_btn):
            bottom.addWidget(button)
        outer.addLayout(bottom)

        self._build(config)

    # ── Pages ────────────────────────────────────────────────────────────

    def _build(self, config, select=None):
        while self.stack.count():
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)
            widget.deleteLater()
        self.general_pages = [MachinePage(config), GaugePage(config), DaqPage(config)]
        self.loop_pages = [LoopPage(n, e) for n, e in config["eurotherms"].items()]
        self.shutter_pages = [ShutterPage(n, e) for n, e in config["shutters"].items()]
        for page in self.general_pages + self.loop_pages + self.shutter_pages:
            self._adopt(page)
        self._refresh_nav(select or self.general_pages[0])
        self._set_dirty(False)

    def _adopt(self, page):
        self.stack.addWidget(page)
        page.changed.connect(lambda: self._set_dirty(True))
        if hasattr(page, "renamed"):
            page.renamed.connect(self._update_nav_texts)

    def _refresh_nav(self, select=None):
        self.nav.blockSignals(True)
        self.nav.clear()

        def header(text):
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.nav.addItem(item)

        def entry(page, text):
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.nav.addItem(item)
            if page is select:
                self.nav.setCurrentItem(item)

        header("GENERAL")
        for page in self.general_pages:
            entry(page, page.nav_text)
        header("CELLS (EUROTHERM)")
        for page in self.loop_pages:
            entry(page, page.current_name() or "(no name)")
        header("SHUTTERS")
        for page in self.shutter_pages:
            entry(page, page.current_name() or "(no name)")
        self.nav.blockSignals(False)
        self._on_nav(self.nav.currentItem())

    def _update_nav_texts(self):
        for i in range(self.nav.count()):
            page = self.nav.item(i).data(Qt.ItemDataRole.UserRole)
            if page in self.loop_pages or page in self.shutter_pages:
                self.nav.item(i).setText(page.current_name() or "(no name)")

    def current_page(self):
        item = self.nav.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select(self, page):
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole) is page:
                self.nav.setCurrentRow(i)
                return

    def _on_nav(self, item, _previous=None):
        page = item.data(Qt.ItemDataRole.UserRole) if item else None
        if page is not None:
            self.stack.setCurrentWidget(page)
        movable = self._list_of(page)
        for button in (self.up_btn, self.down_btn, self.duplicate_btn, self.delete_btn):
            button.setEnabled(movable is not None)

    def _list_of(self, page):
        if page in self.loop_pages:
            return self.loop_pages
        if page in self.shutter_pages:
            return self.shutter_pages
        return None

    def _names(self) -> set:
        return {p.current_name().casefold() for p in self.loop_pages + self.shutter_pages}

    def _free_name(self, base: str, sep: str) -> str:
        used = self._names()
        candidate, n = base, 2
        while candidate.casefold() in used:
            candidate, n = f"{base}{sep}{n}", n + 1
        return candidate

    def add_loop(self, _checked=False, name=None, loop=None):
        page = LoopPage(name or self._free_name("Cell", "-"), loop or mc.default_eurotherm())
        self.loop_pages.append(page)
        self._added(page)
        return page

    def add_shutter(self, _checked=False, name=None, shutter=None):
        page = ShutterPage(name or self._free_name("Shutter", "_"), shutter or mc.default_shutter())
        self.shutter_pages.append(page)
        self._added(page)
        return page

    def _added(self, page):
        self._adopt(page)
        self._refresh_nav(page)
        self._set_dirty(True)
        page.name.widget.setFocus()
        page.name.widget.selectAll()

    def duplicate_current(self):
        page, errors = self.current_page(), []
        if self._list_of(page) is None:
            return
        name, entry = page.collect(errors)
        if page in self.loop_pages:
            self.add_loop(name=self._free_name(f"{name}-copy", "-"), loop=entry)
        else:
            self.add_shutter(name=self._free_name(f"{name}_copy", "_"), shutter=entry)

    def delete_current(self):
        page = self.current_page()
        pages = self._list_of(page)
        if pages is None:
            return
        name = page.current_name() or "(no name)"
        if not self._ask("Delete", f"Remove '{name}' from the configuration?\n\n"
                                   f"Scripts that use '{name}' will no longer compile."):
            return
        index = pages.index(page)
        pages.remove(page)
        self.stack.removeWidget(page)
        page.deleteLater()
        neighbours = pages[index:index + 1] or pages[-1:] or self.general_pages[:1]
        self._refresh_nav(neighbours[0])
        self._set_dirty(True)

    def move_current(self, step: int):
        page = self.current_page()
        pages = self._list_of(page)
        if pages is None:
            return
        i = pages.index(page)
        j = i + step
        if 0 <= j < len(pages):
            pages[i], pages[j] = pages[j], pages[i]
            self._refresh_nav(page)
            self._set_dirty(True)

    # ── Collect / save ───────────────────────────────────────────────────

    def collect(self) -> tuple[dict, list[str]]:
        """(configuration typed in the forms, problems). Problems = wrong input + validation."""
        errors = []
        config = {"version": self.saved.get("version", 1),
                  "eurotherms": {}, "shutters": {},
                  "security": copy.deepcopy(self.saved.get("security", {"password": None}))}
        for page in self.general_pages:
            page.collect(config, errors)
        for kind, pages, section in (("Cell", self.loop_pages, "eurotherms"),
                                     ("Shutter", self.shutter_pages, "shutters")):
            for page in pages:
                name, entry = page.collect(errors)
                if not name:
                    errors.append(f"{kind}: a name is required")
                elif name in config[section]:
                    errors.append(f"{kind} {name}: this name is used twice")
                else:
                    config[section][name] = entry
        if errors:
            return config, errors
        config = mc.normalize(config)
        return config, mc.validate(config)

    @staticmethod
    def sensitive_changes(old: dict, new: dict) -> list[str]:
        """Changes the operator must confirm explicitly before they are saved."""
        notes = []
        if old["machine"]["simulation"] and not new["machine"]["simulation"]:
            notes.append("Simulation mode is switched OFF: after the restart the program "
                         "drives the REAL instruments.")
        for name, shutter in new["shutters"].items():
            # Previous interlock of this name, else of this NI-DAQ line (the interlock
            # itself is matched by line): a rename must not hide the change.
            before = (old["shutters"].get(name) or {}).get("pressure_interlock") or next(
                (s.get("pressure_interlock") for s in old["shutters"].values()
                 if s.get("line") == shutter["line"] and s.get("pressure_interlock")), None)
            now = shutter["pressure_interlock"]
            if now and isinstance(before, dict):
                old_limit, new_limit = before.get("max_pressure_mbar"), now.get("max_pressure_mbar")
                if (mc._is_number(old_limit) and mc._is_number(new_limit)
                        and new_limit > old_limit):
                    notes.append(f"Pressure interlock limit of {name} is RAISED from "
                                 f"{old_limit:g} to {new_limit:g} mbar.")
            if now and now.get("bypass") and not (isinstance(before, dict)
                                                  and before.get("bypass")):
                notes.append(f"Pressure interlock of {name} is BYPASSED: it will open without "
                             f"any pressure check.")
            if before and not now:
                notes.append(f"Pressure interlock of {name} is REMOVED.")
        return notes

    def save(self, restart=False) -> bool:
        """Validate, confirm sensitive changes, write the file, log the changes."""
        config, errors = self.collect()
        if errors:
            self._status(f"Not saved — {len(errors)} problem(s): {errors[0]}", ui.ERROR)
            self._warn("Configuration not saved",
                       "Fix these values first:\n\n• " + "\n• ".join(errors[:25]))
            return False
        changes = mc.describe_changes(self.saved, config)
        if not changes and not restart:
            self._set_dirty(False)
            self._status("No change to save.", ui.MUTED)
            return True
        if changes:
            notes = self.sensitive_changes(self.saved, config)
            if notes and not self._ask("Confirm", "Please confirm:\n\n• " + "\n• ".join(notes)):
                return False
            try:
                mc.save(config, self.path)
            except mc.ConfigError as e:
                self._status(f"Not saved: {e}", ui.ERROR)
                self._warn("Configuration not saved", str(e))
                return False
            self.log(f"[Config] Configuration saved to {self.path}: {len(changes)} change(s), "
                     f"effective at the next start.")
            for line in changes[:MAX_LOGGED_CHANGES]:
                self.log(f"[Config]   {line}")
            if len(changes) > MAX_LOGGED_CHANGES:
                self.log(f"[Config]   … and {len(changes) - MAX_LOGGED_CHANGES} more.")
            self.saved = copy.deepcopy(config)
            self.saved_once = True
            self._set_dirty(False)
        if restart:
            return bool(self.request_restart())
        return True

    def revert(self):
        if self.dirty and not self._ask("Revert", "Discard the unsaved changes?"):
            return
        self.reload()

    def reload(self):
        """Forget the forms and show the content of the file again."""
        try:
            self.saved = mc.load(self.path)
        except mc.ConfigError as e:
            self._warn("Configuration", str(e))
            return
        self._build(self.saved)

    def _set_dirty(self, dirty: bool):
        self.dirty = dirty
        self.revert_btn.setEnabled(dirty)
        if dirty:
            self._status("● Unsaved changes", ui.WARN)
        elif self.saved_once:
            self._status("✓ Saved. The new configuration takes effect at the next start "
                         "of MBE Control.", ui.OK)
        else:
            self._status("Changes take effect at the next start of MBE Control.", ui.MUTED)

    def _status(self, text, color):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    # ── Password ─────────────────────────────────────────────────────────

    def change_password(self):
        dialog = PasswordDialog(self, ask_current=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        current, new = dialog.values()
        self.set_new_password(current, new)

    def set_new_password(self, current: str, new: str) -> bool:
        """Replace the password in the file (only that: unsaved form changes stay unsaved)."""
        try:
            on_disk = mc.load(self.path)
            if not mc.check_password(on_disk, current):
                raise mc.ConfigError("the current password is wrong")
            mc.set_password(on_disk, new)
            mc.save(on_disk, self.path, check=False)     # file as it is, plus the password
        except mc.ConfigError as e:
            self._warn("Password not changed", str(e))
            return False
        self.saved["security"] = copy.deepcopy(on_disk["security"])
        self.log("[Config] Configuration password changed.")
        return True

    # Dialogs (replaced in tests)
    def _ask(self, title, text) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.StandardButton.Yes

    def _warn(self, title, text):
        QMessageBox.warning(self, title, text)


class PasswordDialog(QDialog):
    """Current password (optional) + new password typed twice."""
    def __init__(self, parent=None, ask_current=False):
        super().__init__(parent)
        self.setWindowTitle("Configuration password")
        form = QFormLayout(self)
        self.current = QLineEdit()
        self.current.setEchoMode(QLineEdit.EchoMode.Password)
        if ask_current:
            form.addRow("Current password", self.current)
        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("New password", self.new)
        form.addRow("Repeat", self.confirm)
        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {ui.ERROR};")
        form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept(self):
        problem = password_problem(self.new.text(), self.confirm.text())
        if problem:
            self.error.setText(problem)
            return
        self.accept()

    def values(self):
        return self.current.text(), self.new.text()


def password_problem(new: str, confirm: str) -> str:
    """Why this new password cannot be used ("" if it can)."""
    if len(new) < mc.PASSWORD_MIN_LENGTH:
        return f"At least {mc.PASSWORD_MIN_LENGTH} characters."
    if new != confirm:
        return "The two passwords are different."
    return ""


# ── Tab: lock page <-> editor ────────────────────────────────────────────────

class ConfigTab(QWidget):
    def __init__(self, log=print, scripts_running=lambda: [], request_restart=lambda: False,
                 config_path=None):
        super().__init__()
        self.path = config_path or mc.CONFIG_PATH
        self.log = log
        self.scripts_running = scripts_running
        self.request_restart = request_restart
        self.editor = None

        self.stack = QStackedWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        # ── Lock page ────────────────────────────────────────────────────
        self.lock_page = QWidget()
        centre = QVBoxLayout(self.lock_page)
        centre.addStretch(2)
        card = QFrame()
        card.setProperty("card", True)
        card.setFixedWidth(440)
        box = QVBoxLayout(card)
        box.setContentsMargins(32, 28, 32, 28)
        box.setSpacing(12)
        icon = QLabel("🔒")
        icon.setStyleSheet("font-size: 30pt; border: none;")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(icon)
        title = QLabel("System configuration")
        title.setProperty("title", True)
        title.setStyleSheet("border: none;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(title)
        self.lock_text = _note("")
        self.lock_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.lock_text)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_input.setPlaceholderText("Password")
        self.password_input.returnPressed.connect(self._submit)
        box.addWidget(self.password_input)
        self.confirm_input = QLineEdit()
        self.confirm_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_input.setPlaceholderText("Repeat the password")
        self.confirm_input.returnPressed.connect(self._submit)
        box.addWidget(self.confirm_input)
        self.unlock_btn = QPushButton("Unlock")
        self.unlock_btn.setProperty("primary", True)
        self.unlock_btn.clicked.connect(self._submit)
        box.addWidget(self.unlock_btn)
        self.lock_error = QLabel("")
        self.lock_error.setWordWrap(True)
        self.lock_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lock_error.setStyleSheet(f"color: {ui.ERROR}; border: none; font-weight: 600;")
        box.addWidget(self.lock_error)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(card)
        row.addStretch()
        centre.addLayout(row)
        centre.addStretch(3)
        self.stack.addWidget(self.lock_page)
        self._refresh_lock_page()

    # ── State ────────────────────────────────────────────────────────────

    def is_unlocked(self) -> bool:
        return self.editor is not None and self.stack.currentWidget() is self.editor

    def _load(self):
        """Configuration as it is in the file now, or None (error shown)."""
        try:
            return mc.load(self.path)
        except mc.ConfigError as e:
            self.lock_error.setText(str(e))
            return None

    def _refresh_lock_page(self):
        """First access (no password yet): ask to create it. Else: ask for it."""
        config = self._load()
        self._creating = config is not None and not mc.has_password(config)
        self.confirm_input.setVisible(self._creating)
        if self._creating:
            self.lock_text.setText(
                "Cell names, ports, limits, shutters and interlocks of this machine.\n\n"
                "No password is defined yet: choose the one that will protect this area.")
            self.password_input.setPlaceholderText("New password")
            self.unlock_btn.setText("Create password and open")
        else:
            self.lock_text.setText(
                "Cell names, ports, limits, shutters and interlocks of this machine.\n"
                "Protected area: enter the password.")
            self.password_input.setPlaceholderText("Password")
            self.unlock_btn.setText("Unlock")

    def _submit(self):
        if self._creating:
            self.create_password(self.password_input.text(), self.confirm_input.text())
        else:
            self.unlock(self.password_input.text())

    def create_password(self, password: str, confirm: str) -> bool:
        """First access: define the password, then open the editor."""
        config = self._load()
        if config is None:
            return False
        if mc.has_password(config):          # defined meanwhile: it must be typed
            self._refresh_lock_page()
            return False
        problem = password_problem(password, confirm)
        if problem:
            self.lock_error.setText(problem)
            return False
        try:
            mc.set_password(config, password)
            # The file as it is, plus the password: not validated, so that a file
            # with problems (converted addresses.py) can be opened and fixed here.
            mc.save(config, self.path, check=False)
        except mc.ConfigError as e:
            self.lock_error.setText(str(e))
            return False
        self.log("[Config] Configuration password created.")
        return self._open(config)

    def unlock(self, password: str) -> bool:
        config = self._load()
        if config is None:
            return False
        if not mc.check_password(config, password):
            self.lock_error.setText("Wrong password.")
            self.password_input.selectAll()
            self.log("[Config] Configuration: wrong password.")
            return False
        self.log("[Config] Configuration unlocked.")
        return self._open(config)

    def _open(self, config) -> bool:
        self.password_input.clear()
        self.confirm_input.clear()
        self.lock_error.setText("")
        if self.editor is None:
            self.editor = ConfigEditor(config, self.path, self.log,
                                       self.scripts_running, self.request_restart)
            self.editor.lock_requested.connect(self.lock)
            self.stack.addWidget(self.editor)
        elif not self.editor.dirty:
            self.editor.reload()             # nothing pending: show the file as it is now
        self.stack.setCurrentWidget(self.editor)
        return True

    def lock(self):
        """Back to the password page. Unsaved edits stay in the forms."""
        if self.stack.currentWidget() is self.lock_page:
            return
        self.stack.setCurrentWidget(self.lock_page)
        self._refresh_lock_page()
        self.log("[Config] Configuration locked.")
