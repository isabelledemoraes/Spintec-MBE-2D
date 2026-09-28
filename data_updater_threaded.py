# --- START OF data_updater_threaded.py ---
import time
from PyQt6.QtCore import QObject, pyqtSignal


class BaseWorker(QObject):
    """A base worker class that handles the stop signal."""
    data_ready = pyqtSignal(str, dict)

    def __init__(self, name, controller):
        super().__init__()
        self.name = name
        self.controller = controller
        self.stopped = False
        self._last_error_ts = 0.0

    def _log_error_throttled(self, msg: str, interval_sec: float = 5.0):
        """Print at most once per interval to avoid flooding the console."""
        now = time.time()
        if now - self._last_error_ts >= interval_sec:
            print(msg)
            self._last_error_ts = now

    def stop(self):
        self.stopped = True


class EurothermWorker(BaseWorker):
    """Worker specifically for fetching data from Eurotherm controllers."""

    def run(self):
        while not self.stopped:
            data = {}
            if not self.controller:
                break  # Stop if the controller failed to initialize
            try:
                # Read all values for a standard instrument
                data['T'] = self.controller.read_value(self.name, "temperature_pv")
                data['P'] = self.controller.read_value(self.name, "output_pv")
                data['SP_T'] = self.controller.read_value(self.name, "temperature_setpoint")
                data['SP_P'] = self.controller.read_value(self.name, "output_setpoint")
                data['MODE'] = self.controller.get_mode(self.name)
                data['Ramp_T'] = self.controller.read_value(self.name, "Ramp_temp")
                data['Ramp_P'] = self.controller.read_value(self.name, "Ramp_OP")

                self.data_ready.emit(self.name, data)
            except Exception as e:
                # Throttled error logging
                self._log_error_throttled(f"[{self.name}] Worker Error: {e}")

            time.sleep(2)  # 2-second interval for Eurotherms


class PressureWorker(BaseWorker):
    """Worker specifically for fetching data from the Pressure Gauge."""

    def run(self):
        while not self.stopped:
            data = {}
            if not self.controller:
                print("[PressureWorker] No controller, stopping worker.")
                break  # Stop if the controller failed to initialize
            try:
                # Read only the pressure value
                pressure = self.controller.read_value("pressure")
                data = {'P': pressure}
                # DEBUG
                #print(f"[PressureWorker] Emitting pressure value: {pressure}")
                self.data_ready.emit(self.name, data)
            except Exception as e:
                print(f"[{self.name}] Worker Error: {e}")

            time.sleep(1)  # 1-second interval for pressure

# --- END OF data_updater_threaded.py ---
