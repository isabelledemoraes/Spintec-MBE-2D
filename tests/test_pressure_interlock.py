"""Pressure interlock (#8), simulation only. Run with: python -m unittest discover tests"""
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import shutter_controller
from pressure_gauge_controller import PressureGaugeController
from pressure_interlock import InterlockError, check_pressure, interlock_for_line
from script_compiler import compile_script, Level
from shutter_controller import ShutterController

LIMIT = 1.0e-8
N2_LINE = "Dev1/port0/line7"

ADDR = {
    "Pressure":  {"device": ("COM10", 300), "command": "DS IG1 \r\n", "unit": "mbar"},
    "Liquid_N2": {"shutter_line": N2_LINE, "pressure_interlock": {"max_pressure_mbar": LIMIT}},
    "N2_alias":  {"shutter_line": N2_LINE},   # same physical line, no interlock of its own
    "eGun1":     {"shutter_line": "Dev1/port1/line0"},
}
CONFIG = ADDR["Liquid_N2"]["pressure_interlock"]


class FakeGauge:
    """Returns the given values in turn (an Exception instance is raised instead)."""
    def __init__(self, *values, connected=True):
        self.values = list(values)
        self.connected = connected
        self.reads = 0

    def read_value(self, name):
        assert name == "pressure"
        self.reads += 1
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        if isinstance(value, Exception):
            raise value
        return value


def addr_with(**changes):
    """Copy of ADDR with some entries replaced."""
    addr = {k: dict(v) for k, v in ADDR.items()}
    addr.update(changes)
    return addr


class CheckPressureTests(unittest.TestCase):
    def assertBlocked(self, gauge, addresses=ADDR, config=CONFIG, contains=""):
        with self.assertRaises(InterlockError) as cm:
            check_pressure(gauge, addresses, config)
        self.assertIn(contains, str(cm.exception))
        return str(cm.exception)

    def test_below_threshold_allowed(self):
        self.assertEqual(check_pressure(FakeGauge(9.9e-9), ADDR, CONFIG), 9.9e-9)

    def test_above_threshold_blocked_with_value_and_limit(self):
        msg = self.assertBlocked(FakeGauge(3.2e-8))
        self.assertEqual(msg, "IG1 = 3.2e-08 mbar, required < 1.0e-08 mbar")

    def test_equal_threshold_blocked(self):
        self.assertBlocked(FakeGauge(1.0e-8), contains="IG1 = 1.0e-08 mbar")

    def test_unavailable_blocked(self):
        self.assertBlocked(None, contains="unavailable")
        self.assertBlocked(FakeGauge(1e-9, connected=False), contains="unavailable")

    def test_read_error_blocked(self):
        for error in (IOError("Serial read error: timeout"),
                      ValueError("Unparsable response: "),
                      ConnectionError("PressureGauge is not connected")):
            self.assertBlocked(FakeGauge(error), contains="reading failed")

    def test_invalid_value_blocked(self):
        for value in (float("nan"), float("inf"), -1e-9, 0.0, 0, None, "1e-9", True):
            with self.subTest(value=value):
                self.assertBlocked(FakeGauge(value), contains="reading invalid")

    def test_bad_configuration_blocked(self):
        gauge = FakeGauge(1e-12)
        for config in ({}, {"max_pressure_mbar": None}, {"max_pressure_mbar": -1},
                       {"max_pressure_mbar": "1e-8"}, None):
            with self.subTest(config=config):
                self.assertBlocked(gauge, config=config, contains="configuration invalid")
        for unit in ("Torr", "Pa", None):
            with self.subTest(unit=unit):
                addr = addr_with(Pressure={**ADDR["Pressure"], "unit": unit})
                self.assertBlocked(gauge, addresses=addr, contains="['unit'] must be 'mbar'")
        self.assertEqual(gauge.reads, 0)   # configuration refused before reading

    def test_interlock_found_by_line(self):
        self.assertIs(interlock_for_line(ADDR, N2_LINE), CONFIG)
        self.assertIsNone(interlock_for_line(ADDR, "Dev1/port1/line0"))


class FakeSerial:
    """Serial port holding a late reply to an earlier command in its input buffer."""
    def __init__(self, stale, reply):
        self.buffer = stale
        self.reply = reply

    def reset_input_buffer(self):
        self.buffer = b""

    def write(self, data):
        self.buffer += self.reply

    def read(self, n):
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data


class GaugeReadTests(unittest.TestCase):
    """The existing PressureGaugeController.read_value, fed by a fake serial port."""
    def gauge(self, stale, reply):
        g = PressureGaugeController.__new__(PressureGaugeController)
        g.command, g.connected, g.port_lock = b"DS IG1 \r\n", True, threading.Lock()
        g.ser = FakeSerial(stale, reply)
        return g

    def test_scientific_notation(self):
        self.assertEqual(self.gauge(b"", b"3.2E-08\r\n").read_value("pressure"), 3.2e-8)
        self.assertEqual(self.gauge(b"", b" 9.5e-9 \r\n").read_value("pressure"), 9.5e-9)

    def test_stale_reply_discarded(self):
        g = self.gauge(stale=b"5.0E-09\r\n", reply=b"3.2E-08\r\n")
        self.assertEqual(g.read_value("pressure"), 3.2e-8)

    def test_no_reply_is_an_error(self):
        with self.assertRaises(ValueError):
            self.gauge(b"", b"").read_value("pressure")


class ShutterControllerTests(unittest.TestCase):
    def setUp(self):
        # Never touch the NI-DAQ or the real shutter_state.json.
        self._saved = shutter_controller.nidaqmx, shutter_controller.STATE_FILE
        self._tmp = tempfile.TemporaryDirectory()
        shutter_controller.nidaqmx = None
        shutter_controller.STATE_FILE = os.path.join(self._tmp.name, "shutter_state.json")

    def tearDown(self):
        shutter_controller.nidaqmx, shutter_controller.STATE_FILE = self._saved
        self._tmp.cleanup()

    def ctrl(self, gauge):
        return ShutterController(pressure_gauge=gauge, addresses_map=ADDR)

    def test_opens_below_threshold(self):
        ctrl = self.ctrl(FakeGauge(5e-9))
        self.assertTrue(ctrl.set_shutter("Liquid_N2", N2_LINE, True))
        self.assertTrue(ctrl.get_state("Liquid_N2"))

    def test_not_opened_at_or_above_threshold_or_unknown(self):
        for gauge in (FakeGauge(3.2e-8), FakeGauge(LIMIT), None,
                      FakeGauge(IOError("timeout")), FakeGauge(float("nan"))):
            with self.subTest(gauge=getattr(gauge, "values", gauge)):
                ctrl = self.ctrl(gauge)
                with self.assertRaises(InterlockError):
                    ctrl.set_shutter("Liquid_N2", N2_LINE, True)
                self.assertFalse(ctrl.get_state("Liquid_N2"))

    def test_alias_on_same_line_is_interlocked(self):
        ctrl = self.ctrl(FakeGauge(3.2e-8))
        with self.assertRaises(InterlockError):
            ctrl.set_shutter("N2_alias", N2_LINE, True)
        self.assertFalse(ctrl.get_state("N2_alias"))

    def test_bypass_opens_without_reading(self):
        addr = addr_with(Liquid_N2={"shutter_line": N2_LINE, "pressure_interlock":
                                    {"max_pressure_mbar": LIMIT, "bypass": True}})
        for gauge in (None, FakeGauge(3.2e-8)):
            ctrl = ShutterController(pressure_gauge=gauge, addresses_map=addr)
            self.assertTrue(ctrl.set_shutter("Liquid_N2", N2_LINE, True))
            self.assertTrue(ctrl.get_state("Liquid_N2"))
        self.assertEqual(gauge.reads, 0)

    def test_only_literal_true_bypasses(self):
        for flag in (False, None, 1, "yes", "True"):
            with self.subTest(flag=flag):
                addr = addr_with(Liquid_N2={"shutter_line": N2_LINE, "pressure_interlock":
                                            {"max_pressure_mbar": LIMIT, "bypass": flag}})
                ctrl = ShutterController(pressure_gauge=FakeGauge(3.2e-8), addresses_map=addr)
                with self.assertRaises(InterlockError):
                    ctrl.set_shutter("Liquid_N2", N2_LINE, True)

    def test_closing_and_other_shutters_not_interlocked(self):
        ctrl = self.ctrl(None)
        self.assertTrue(ctrl.set_shutter("Liquid_N2", N2_LINE, False))
        self.assertTrue(ctrl.set_shutter("eGun1", "Dev1/port1/line0", True))


class CompileTests(unittest.TestCase):
    NOW = datetime(2026, 9, 29, 8, 0)

    def test_info_line_for_each_interlocked_opening(self):
        code = ('open_shutter("Liquid_N2")\nclose_shutter("Liquid_N2")\n'
                'open_shutter("eGun1")\nopen_shutter("N2_alias")')
        report = compile_script(code, ADDR, now=self.NOW)
        self.assertTrue(report.ok)
        infos = [m for m in report.messages if m.level is Level.INFO]
        self.assertEqual([m.line for m in infos], [1, 4])
        self.assertEqual(infos[0].format(),
                         "INFO line 1: IG1 pressure checked before opening Liquid_N2: "
                         "required < 1.0e-08 mbar, else the script stops")
        self.assertEqual(report.summary_line(), "Compilation OK: 0 errors, 0 warnings")

    def test_bypass_is_a_warning(self):
        addr = addr_with(Liquid_N2={"shutter_line": N2_LINE, "pressure_interlock":
                                    {"max_pressure_mbar": None, "bypass": True}})
        report = compile_script('open_shutter("Liquid_N2")', addr, now=self.NOW)
        self.assertTrue(report.ok)
        self.assertEqual([m.format() for m in report.messages],
                         ["WARNING line 1: pressure interlock BYPASSED in addresses.py: "
                          "Liquid_N2 opens without checking IG1"])

    def test_bad_configuration_is_an_error(self):
        addr = addr_with(Liquid_N2={"shutter_line": N2_LINE,
                                    "pressure_interlock": {"max_pressure_mbar": None}})
        report = compile_script('open_shutter("Liquid_N2")', addr, now=self.NOW)
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0].line, 1)
        report = compile_script('open_shutter("Liquid_N2")',
                                addr_with(Pressure={"unit": "Torr"}), now=self.NOW)
        self.assertIn("['unit'] must be 'mbar'", report.errors[0].text)


if __name__ == "__main__":
    unittest.main()
