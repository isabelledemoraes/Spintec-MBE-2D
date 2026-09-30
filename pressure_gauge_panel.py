# pressure_gauge_panel.py
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel
from collections import deque
import pyqtgraph as pg
import time

from connection_monitor import LOST, panel_text, frame_style

PANEL_STYLE = "background-color: #f0f0f0; padding: 5px; border-radius: 8px;"


class PressureGaugePanel(QFrame):
    def __init__(self, name, controller=None):
        super().__init__()
        self.name = name
        self.controller = controller
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setObjectName("pressurePanel")
        self._conn_mark = None   # last connection mark shown (#12)
        self.setStyleSheet(frame_style(PANEL_STYLE, "pressurePanel", lost=False))

        layout = QVBoxLayout(self)

        self.name_label = QLabel(f"<b>{name}</b>")
        self.pressure_label = QLabel("Pressure: --- mbar")
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(12, 12)

        if self.controller:
            self.status_dot.setStyleSheet("background-color: grey; border-radius: 6px;")
        else:
            self.status_dot.setStyleSheet("background-color: red; border-radius: 6px;")

        # Connection status (#12): "⚠ No communication — last OK ..." / "Recovered at ..."
        self.comm_label = QLabel("")
        self.comm_label.hide()

        layout.addWidget(self.name_label)
        layout.addWidget(self.pressure_label)
        layout.addWidget(self.comm_label)
        layout.addWidget(self.status_dot)

        # DateAxisItem formats Unix timestamps as HH:MM:SS / DD/MM etc.
        date_axis = pg.DateAxisItem(orientation='bottom')
        self.plot_widget = pg.PlotWidget(axisItems={'bottom': date_axis})
        self.plot_widget.getPlotItem().layout.setContentsMargins(10, 10, 10, 35)
        self.plot_curve = self.plot_widget.plot(pen='y')
        self.plot_widget.setLabel('left', 'Pressure', units='mbar')
        self.plot_widget.setLabel('bottom', 'Time')
        self.plot_widget.setLogMode(y=True)
        self.plot_widget.showGrid(x=True, y=True)
        layout.addWidget(self.plot_widget)

        # Rolling buffers — 600 samples ≈ 10 min at 1 s/sample
        self.data = deque(maxlen=600)
        self.time_data = deque(maxlen=600)

    def update_pressure(self, pressure: float):
        """Called from the main GUI to update the panel."""
        self.pressure_label.setText(f"Pressure: {pressure:.2e} mbar")
        self.status_dot.setStyleSheet("background-color: green; border-radius: 6px;")

        self.time_data.append(time.time())   # Unix timestamp for DateAxisItem
        self.data.append(pressure)
        self._update_plot()

    def set_status_dot(self, color):
        self.status_dot.setStyleSheet(f"background-color: {color}; border-radius: 6px;")

    def set_connection(self, status):
        """Red border + ⚠ + last OK time while lost; "Recovered" until acknowledged (#12)."""
        lost = status.state == LOST
        text = panel_text(status)
        if (lost, text) == self._conn_mark:
            return
        self._conn_mark = (lost, text)
        self.setStyleSheet(frame_style(PANEL_STYLE, "pressurePanel", lost))
        self.comm_label.setText(text)
        self.comm_label.setStyleSheet(f"color: {'#b02a37' if lost else '#146c43'}; font-weight: bold;")
        self.comm_label.setVisible(bool(text))
        if lost:
            self.set_status_dot("red")

    def _update_plot(self):
        if len(self.data) >= 2:
            self.plot_curve.setData(list(self.time_data), list(self.data))
