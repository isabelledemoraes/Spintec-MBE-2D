# ui_theme.py
"""
Look of the application: one palette, one style sheet (set on the main window),
plus the few styles that change with the state (shutter buttons, chips).
Widgets opt in to a variant with a dynamic property, e.g.
    button.setProperty("primary", True)
"""

FONT = '"Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif'

# Palette
BG = "#F2F4F7"            # window background
SURFACE = "#FFFFFF"       # cards, inputs
BORDER = "#DFE4EA"
BORDER_STRONG = "#C7CFD9"
TEXT = "#16202C"
MUTED = "#617083"         # secondary text
FAINT = "#8E9AAB"         # captions, placeholders
ACCENT = "#2457D6"
ACCENT_HOVER = "#1C46B0"
ACCENT_SOFT = "#E8EEFC"
OK = "#15803D"
OK_SOFT = "#E3F4E8"
WARN = "#B45309"
WARN_SOFT = "#FDF0D9"
ERROR = "#B42318"
ERROR_SOFT = "#FCE8E6"
LOST = "#dc3545"          # border of an instrument that does not answer (#12)
HEAT = "#C2410C"
HEAT_SOFT = "#FDE9DD"
COOL = "#0369A1"
COOL_SOFT = "#E0F0FA"
NEUTRAL_SOFT = "#EDF0F4"

APP_STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ font-family: {FONT}; font-size: 10pt; color: {TEXT}; }}
QToolTip {{ background: {TEXT}; color: white; border: none; padding: 4px 6px; }}

/* Tabs */
QTabWidget::pane {{ border: none; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; color: {MUTED};
    padding: 8px 16px; margin-right: 2px;
    border: none; border-bottom: 3px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 3px solid {ACCENT}; }}

/* Buttons */
QPushButton {{
    background: {SURFACE}; color: {TEXT};
    border: 1px solid {BORDER_STRONG}; border-radius: 6px;
    padding: 6px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: {NEUTRAL_SOFT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; background: {BG}; border-color: {BORDER}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: white; border: 1px solid {ACCENT}; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[primary="true"]:disabled {{ background: #B9C6E8; border-color: #B9C6E8; color: white; }}
QPushButton[danger="true"] {{ color: {ERROR}; border-color: #E5B4AE; }}
QPushButton[danger="true"]:hover {{ background: {ERROR_SOFT}; }}
QPushButton[flat="true"] {{ border: none; background: transparent; color: {ACCENT}; padding: 4px 8px; }}
QPushButton[flat="true"]:hover {{ background: {ACCENT_SOFT}; }}

/* Inputs */
QLineEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE}; color: {TEXT};
    border: 1px solid {BORDER_STRONG}; border-radius: 6px;
    selection-background-color: {ACCENT}; selection-color: white;
}}
QLineEdit, QComboBox {{ padding: 5px 8px; }}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ background: {BG}; color: {FAINT}; border-color: {BORDER}; }}
QLineEdit[invalid="true"] {{ border: 1px solid {ERROR}; background: {ERROR_SOFT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QCheckBox {{ spacing: 8px; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QStatusBar {{ color: {MUTED}; }}
QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; outline: none; padding: 4px;
}}
QListWidget::item {{ padding: 7px 10px; border-radius: 6px; color: {TEXT}; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {ACCENT}; font-weight: 600; }}
QListWidget::item:disabled {{ color: {FAINT}; font-size: 8pt; font-weight: 700; }}

/* Cards */
QFrame[card="true"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel[caption="true"] {{ color: {FAINT}; font-size: 8pt; font-weight: 700; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[title="true"] {{ font-size: 15pt; font-weight: 700; }}
QLabel[heading="true"] {{ font-size: 11pt; font-weight: 700; }}
"""


def chip_style(fg: str, bg: str) -> str:
    """Small rounded label (mode, trend, lock)."""
    return (f"color: {fg}; background: {bg}; border: none; border-radius: 9px; "
            f"padding: 2px 9px; font-size: 8.5pt; font-weight: 700;")


def shutter_style(is_open: bool) -> str:
    """Shutter button: lit green when open, quiet grey when closed."""
    if is_open:
        return (f"QPushButton {{ background: {OK}; color: white; border: 1px solid {OK}; "
                f"border-radius: 8px; padding: 8px 14px; font-weight: 700; }}"
                f"QPushButton:hover {{ background: #116A32; }}"
                f"QPushButton:disabled {{ background: #9CCFAE; border-color: #9CCFAE; color: white; }}")
    return (f"QPushButton {{ background: {NEUTRAL_SOFT}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; "
            f"border-radius: 8px; padding: 8px 14px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: {BORDER}; }}"
            f"QPushButton:disabled {{ color: {FAINT}; border-color: {BORDER}; }}")


def set_prop(widget, name: str, value):
    """Set a dynamic property used by the style sheet and refresh the widget's style."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
