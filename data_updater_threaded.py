# --- START OF data_updater_threaded.py ---
import time
from PyQt6.QtCore import QObject, pyqtSignal

from pressure_interlock import is_valid_pressure


class BaseWorker(QObject):
    """A base worker class that handles the stop signal."""
    data_ready = pyqtSignal(str, dict)
    interval_s = 1.0   # polling interval = delay between connection tries (#12)

    def __init__(self, name, controller, monitor=None):
        super().__init__()
        self.name = name
        self.controller = controller
        # ConnectionMonitor (issue #12): each poll cycle is reported as one try.
        self.monitor = monitor
        self.stopped = False
        self._last_error_ts = 0.0

    def _log_error_throttled(self, msg: str, interval_sec: float = 5.0):
        """Print at most once per interval to avoid flooding the console."""
        now = time.time()
        if now - self._last_error_ts >= interval_sec:
            print(msg)
            self._last_error_ts = now

    def _report(self, error=None):
        if self.monitor is None:
            return
        if error is None:
            self.monitor.report_success(self.name)
        else:
            self.monitor.report_failure(self.name, error)

    def _no_controller(self):
        """Controller failed to initialize: the worker stops, the instrument is lost."""
        if self.monitor is not None:
            self.monitor.mark_lost(self.name, "controller could not be initialized")

    def stop(self):
        self.stopped = True


class EurothermWorker(BaseWorker):
    """Worker specifically for fetching data from Eurotherm controllers."""
    interval_s = 2.0

    def run(self):
        while not self.stopped:
            data = {}
            if not self.controller:
                self._no_controller()
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
                self._report()
            except Exception as e:
                self._report(e)
                # Throttled error logging
                self._log_error_throttled(f"[{self.name}] Worker Error: {e}")

            time.sleep(self.interval_s)


class PressureWorker(BaseWorker):
    """Worker specifically for fetching data from the Pressure Gauge."""
    interval_s = 1.0

    def run(self):
        while not self.stopped:
            data = {}
            if not self.controller:
                print("[PressureWorker] No controller, stopping worker.")
                self._no_controller()
                break  # Stop if the controller failed to initialize
            try:
                # Read only the pressure value
                pressure = self.controller.read_value("pressure")
                # Same validity rule as the pressure interlock (#8).
                if not is_valid_pressure(pressure):
                    raise ValueError(f"invalid pressure reading {pressure!r}")
                data = {'P': pressure}
                # DEBUG
                #print(f"[PressureWorker] Emitting pressure value: {pressure}")
                self.data_ready.emit(self.name, data)
                self._report()
            except Exception as e:
                self._report(e)
                print(f"[{self.name}] Worker Error: {e}")

            time.sleep(self.interval_s)

# --- END OF data_updater_threaded.py ---
