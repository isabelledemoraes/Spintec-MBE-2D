"""
Temperature changes shared by scripts and the General View (temperature_control.py):
rules of a manual change = rules of Compile; run-time sequence. No Qt.
Run with: python -m unittest discover tests
"""
import os
import sys
import threading
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from script_compiler import compile_script
from sim_controllers import SimEurothermController
from temperature_control import (
    check_manual_ramp, manual_limits_text, start_temperature_ramp,
)

LOOP = {"temperature_pv": (1, 1), "temperature_setpoint": (2, 1),
        "output_setpoint": (3, 1), "output_pv": (4, 1), "status": (273, 0),
        "Ramp_temp": (35, 1), "ramp_op_status": (87, 0), "Ramp_OP": (37, 2)}
LIMITS = {"max_temp_rate": 15.0, "power_ramp_allowed": False, "min_power_rate": 0.01}
ADDR = {
    "Au": {"device": ("COM1", 1), **LOOP,
           "limits": {**LIMITS, "max_temp": 1300, "min_temp": 200},
           "critical_zone": {"range": (1014, 1114), "slow_rate": 3.0}},
    "Se1": {"device": ("COM1", 7), **LOOP, "limits": {**LIMITS, "max_temp": 300},
            "script_direct_setpoint": True},
    "Te-Res": {"device": ("COM1", 4), **LOOP, "limits": {**LIMITS, "max_temp": 500},
               "script_keep_ramp_generator": True},
    "NoLim": {"device": ("COM1", 9), **LOOP, "limits": {**LIMITS, "max_temp": None}},
}
NOW = datetime(2026, 5, 4, 10, 0)


def check(loop_id, target, rate, current=25.0):
    return check_manual_ramp(loop_id, target, rate, addresses=ADDR, current_temp=current, now=NOW)


class Recorder(SimEurothermController):
    """Simulated controller that records what is written."""
    def __init__(self, loop_id, pv=25.0):
        super().__init__(loop_id)
        self.connect()
        self._state["temperature_pv"] = pv
        self._state["temperature_setpoint"] = pv
        self.writes = []

    def read_value(self, loop_id, name):
        self._check_answer()
        return self._state[name]          # no drift during the test

    def write_value(self, loop_id, name, value):
        self.writes.append((name, value))
        super().write_value(loop_id, name, value)

    def write_register(self, loop_id, reg_name, value):
        self.writes.append((reg_name, value))
        super().write_register(loop_id, reg_name, value)


class ManualRampRuleTests(unittest.TestCase):
    def test_same_verdict_as_compile(self):
        # Every case: the manual check refuses exactly what Compile refuses.
        cases = [("Au", 900, 10), ("Au", 1301, 10), ("Au", 199, 10), ("Au", 900, 15.1),
                 ("Au", 900, 0), ("Au", 900, -2), ("NoLim", 100, 5), ("Au", 1300, 15)]
        for loop_id, target, rate in cases:
            with self.subTest(loop=loop_id, target=target, rate=rate):
                report = compile_script(f"temperature_ramp({loop_id!r}, {target!r}, {rate!r})",
                                        addresses=ADDR, now=NOW)
                self.assertEqual(check(loop_id, target, rate).ok, report.ok)

    def test_within_limits(self):
        c = check("Au", 900, 10, current=400.0)
        self.assertTrue(c.ok)
        self.assertEqual(c.warnings, [])
        self.assertAlmostEqual(c.duration_min, 50.0)
        self.assertEqual(c.summary(), "Ramp to 900 °C at 10 °C/min — about 50 min")
        self.assertEqual(c.command, 'temperature_ramp("Au", 900, 10)')

    def test_limits(self):
        self.assertEqual(check("Au", 1301, 10).errors, ["Target = 1301 °C, max allowed 1300 °C"])
        self.assertEqual(check("Au", 150, 10).errors, ["Target = 150 °C, min allowed 200 °C"])
        self.assertEqual(check("Au", 900, 20).errors, ["= 20 °C/min, max allowed 15 °C/min"])
        self.assertEqual(check("Au", 900, 0).errors, ["= 0 °C/min, must be greater than 0 °C/min"])
        self.assertEqual(check("NoLim", 100, 5).errors,
                         ["No 'max_temp' limit defined for NoLim in machine.yaml"])
        self.assertFalse(check("Au", 1301, 10).ok)

    def test_missing_input(self):
        self.assertEqual(check("Au", None, 10).errors, ["Enter a target temperature (°C)"])
        self.assertEqual(check("Au", 900, None).errors, ["Enter a ramp rate (°C/min)"])

    def test_critical_zone_warning_and_duration(self):
        c = check("Au", 1200, 10, current=914.0)
        self.assertTrue(c.ok)
        self.assertEqual(c.warnings, ["ramp 914 -> 1200 °C crosses critical zone 1014-1114 °C, "
                                      "ramp slowed to 3 °C/min in this zone"])
        # 100 °C at 10, 100 °C at 3, 86 °C at 10
        self.assertAlmostEqual(c.duration_min, 10 + 100 / 3 + 8.6)
        self.assertIn("⚠", c.summary())

    def test_unknown_start_temperature(self):
        c = check("Au", 1200, 10, current=None)
        self.assertTrue(c.ok)
        self.assertIsNone(c.duration_min)
        self.assertIn("start temperature unknown", c.warnings[0])

    def test_direct_setpoint_loop_ignores_the_rate(self):
        for rate in (None, 0, 999):
            with self.subTest(rate=rate):
                c = check("Se1", 180, rate)
                self.assertTrue(c.ok, c.errors)
                self.assertTrue(c.direct)
                self.assertIsNone(c.duration_min)
                self.assertEqual(c.summary(), "Setpoint 180 °C written directly "
                                              "(no ramp rate on this loop)")
        self.assertEqual(check("Se1", 301, None).errors, ["Target = 301 °C, max allowed 300 °C"])

    def test_limits_text(self):
        self.assertEqual(manual_limits_text(ADDR, "Au"),
                         "Allowed 200 – 1300 °C · max 15 °C/min · "
                         "3 °C/min max between 1014 and 1114 °C")
        self.assertEqual(manual_limits_text(ADDR, "Se1"),
                         "Allowed up to 300 °C · setpoint written directly")
        self.assertEqual(manual_limits_text(ADDR, "NoLim"),
                         "max_temp is not set for NoLim in machine.yaml")


class RampSequenceTests(unittest.TestCase):
    def start(self, ctrl, loop_id, target, rate, **kwargs):
        log = []
        end = start_temperature_ramp(ctrl, loop_id, target, rate, addresses=ADDR,
                                     log=log.append, stop_event=threading.Event(), **kwargs)
        return end, log

    def test_single_segment(self):
        ctrl = Recorder("Au", pv=400.0)
        ctrl._state.update(mode="manual", ramp_status="on")
        end, log = self.start(ctrl, "Au", 900, 10)
        self.assertEqual(ctrl.writes, [("Ramp_temp", 10), ("temperature_setpoint", 900)])
        self.assertEqual(ctrl.get_mode("Au"), "auto")
        self.assertEqual(ctrl.get_ramp_status("Au"), "off")
        self.assertAlmostEqual((end - datetime.now()).total_seconds() / 60, 50, delta=0.1)
        self.assertIn("[Au] Ramp generator successfully verified as OFF.", log)
        self.assertIn("[Au] Switched to AUTO mode for temperature ramp.", log)

    def test_first_segment_stops_at_the_critical_zone(self):
        ctrl = Recorder("Au", pv=914.0)
        stop = threading.Event()
        stop.set()                                   # follow-up thread ends at once
        start_temperature_ramp(ctrl, "Au", 1200, 10, addresses=ADDR, log=lambda m: None,
                               stop_event=stop)
        self.assertEqual(ctrl.writes, [("Ramp_temp", 10), ("temperature_setpoint", 1014)])

    def test_already_at_target(self):
        ctrl = Recorder("Au", pv=900.0)
        end, log = self.start(ctrl, "Au", 900.0, 10)
        self.assertIsNone(end)
        self.assertEqual(ctrl.writes, [])
        self.assertIn("[Au] Already at target temperature.", log)

    def test_rate_capped(self):
        ctrl = Recorder("Au", pv=400.0)
        _, log = self.start(ctrl, "Au", 900, 50, max_rate_cap=20.0)
        self.assertEqual(ctrl.writes[0], ("Ramp_temp", 20.0))
        self.assertIn("[Au] WARNING: Requested rate capped at 20.0°C/min.", log)

    def test_direct_setpoint_loop_only_writes_the_setpoint(self):
        ctrl = Recorder("Se1", pv=100.0)
        ctrl._state.update(mode="manual", ramp_status="on")
        end, _ = self.start(ctrl, "Se1", 180, 5)
        self.assertIsNone(end)
        self.assertEqual(ctrl.writes, [("temperature_setpoint", 180)])
        self.assertEqual(ctrl.get_mode("Se1"), "manual")           # untouched
        self.assertEqual(ctrl.get_ramp_status("Se1"), "on")

    def test_keep_ramp_generator_loop_restores_its_state(self):
        ctrl = Recorder("Te-Res", pv=100.0)
        ctrl._state["ramp_status"] = "on"
        self.start(ctrl, "Te-Res", 300, 5)
        self.assertEqual(ctrl.get_ramp_status("Te-Res"), "on")
        self.assertEqual(ctrl.writes, [("Ramp_temp", 5), ("temperature_setpoint", 300)])

    def test_no_answer_aborts_before_any_write(self):
        ctrl = Recorder("Au", pv=400.0)
        ctrl.fail = True
        end, log = self.start(ctrl, "Au", 900, 10)
        self.assertIsNone(end)
        self.assertEqual(ctrl.writes, [])
        self.assertTrue(any("CRITICAL: Failed to execute safety interlock" in m for m in log))


if __name__ == "__main__":
    unittest.main()
