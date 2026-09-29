# sim_controllers.py
# Simulated hardware controllers for development/testing without real serial ports.
import time


class SimEurothermController:
    """Simulates an EurothermController with in-memory state and time-based drift."""

    def __init__(self, loop_id: str):
        self.loop_id = loop_id
        self.connected = False
        self._state = {
            "temperature_pv": 25.0,
            "temperature_setpoint": 25.0,
            "output_pv": 5.0,
            "output_setpoint": 5.0,
            "mode": "auto",
            "ramp_status": "off",
            "Ramp_temp": 5.0,   # °C/min
            "Ramp_OP": 0.0,     # %/s
        }
        self._last_tick = time.time()

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def _tick(self):
        now = time.time()
        dt = now - self._last_tick  # seconds elapsed
        self._last_tick = now

        if self._state["mode"] == "auto":
            # Drift temperature_pv toward temperature_setpoint at Ramp_temp °C/min
            rate = self._state["Ramp_temp"] / 60.0  # convert to °C/s
            pv = self._state["temperature_pv"]
            sp = self._state["temperature_setpoint"]
            delta = sp - pv
            step = rate * dt
            if abs(delta) <= step:
                self._state["temperature_pv"] = sp
            else:
                self._state["temperature_pv"] = pv + step * (1 if delta > 0 else -1)
        else:
            # Manual mode
            if self._state["ramp_status"] == "on":
                # Drift output_pv toward output_setpoint at Ramp_OP %/s
                rate = self._state["Ramp_OP"]  # %/s
                pv = self._state["output_pv"]
                sp = self._state["output_setpoint"]
                delta = sp - pv
                step = rate * dt
                if abs(delta) <= step:
                    self._state["output_pv"] = sp
                else:
                    self._state["output_pv"] = pv + step * (1 if delta > 0 else -1)
            else:
                # Snap instantly
                self._state["output_pv"] = self._state["output_setpoint"]

    def read_value(self, loop_id: str, name: str):
        self._tick()
        return self._state[name]

    def write_value(self, loop_id: str, name: str, value):
        self._state[name] = value

    def write_register(self, loop_id: str, reg_name: str, value):
        self._state[reg_name] = value

    def get_mode(self, loop_id: str) -> str:
        return self._state["mode"]

    def set_mode(self, loop_id: str, mode: str):
        self._state["mode"] = mode

    def get_ramp_status(self, loop_id: str) -> str:
        return self._state["ramp_status"]

    def set_ramp_status(self, loop_id: str, enable: bool):
        self._state["ramp_status"] = "on" if enable else "off"


class SimPressureGaugeController:
    """Simulates a PressureGaugeController, returning self.pressure (mbar)."""

    def __init__(self, loop_id: str):
        self.loop_id = loop_id
        self.connected = False
        self.pressure = 1.5e-9  # mbar; change it to test the Liquid_N2 interlock

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def read_value(self, name: str):
        return self.pressure
