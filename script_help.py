"""
❓ Help pop-up of the script editor (issue #1): every script function with its syntax
(ready to copy and paste) and a one-line description. Scripts themselves stay clean.
HELP_ENTRIES is plain data (no Qt) so tests can check it against script_compiler.DSL_PARAMS.
"""
from PyQt6.QtWidgets import (
    QApplication, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)
from PyQt6.QtGui import QFontDatabase

# (function name, syntax to paste, description)
HELP_ENTRIES = [
    ("temperature_ramp", 'temperature_ramp("Au", 300, 10)',
     "Ramp loop to a temperature: (loop, target °C, rate °C/min). Returns at once; "
     "Se1/Se2 only set the setpoint."),
    ("power_ramp", 'power_ramp("Au", 50, 1)',
     "Ramp output power in manual mode: (loop, target %, rate %/s). Returns at once."),
    ("hold", 'hold(10)',
     "Wait: (duration in minutes). Running ramps continue."),
    ("wait_until_temperature", 'wait_until_temperature("Au", 300, tolerance=0.5, timeout=None)',
     "Wait until within tolerance: (loop, target °C, tolerance °C, timeout in seconds or None)."),
    ("wait_until", 'wait_until("2026-01-31 14:30")',
     'Wait until a date and time: ("YYYY-MM-DD HH:MM").'),
    ("open_shutter", 'open_shutter("eGun1")',
     "Open a shutter (Au, CaF2, Se1, Se2, SnSe, Liquid_N2, eGun1, eGun2). "
     "Liquid_N2 opens only if the pressure is low enough."),
    ("close_shutter", 'close_shutter("eGun1")',
     "Close a shutter (same names as open_shutter)."),
]


def all_syntax_text() -> str:
    """Every syntax line, one per line (what 'Copy all' puts in the clipboard)."""
    return "\n".join(syntax for _, syntax, _ in HELP_ENTRIES)


def _copy(text: str):
    QApplication.clipboard().setText(text)


class ScriptHelpDialog(QDialog):
    """Non-modal, so the user can paste into the editor while it stays open."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Script functions")
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        self.syntax_fields = []
        self.copy_buttons = []
        for row, (_, syntax, description) in enumerate(HELP_ENTRIES):
            field = QLineEdit(syntax)
            field.setReadOnly(True)       # selectable with the mouse, not editable
            field.setFont(mono)
            button = QPushButton("Copy")
            button.clicked.connect(lambda _=False, s=syntax: _copy(s))
            label = QLabel(description)
            label.setWordWrap(True)
            grid.addWidget(field, row * 2, 0)
            grid.addWidget(button, row * 2, 1)
            grid.addWidget(label, row * 2 + 1, 0, 1, 2)
            self.syntax_fields.append(field)
            self.copy_buttons.append(button)

        self.copy_all_button = QPushButton("Copy all")
        self.copy_all_button.clicked.connect(lambda: _copy(all_syntax_text()))
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.copy_all_button)
        bottom.addStretch()
        bottom.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addLayout(grid)
        layout.addLayout(bottom)
        self.setMinimumWidth(560)
