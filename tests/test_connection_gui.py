"""
Connection check (#12) in the real MainWindow: real worker threads, simulated
controllers (with the "fail" flag), no NI-DAQ, offscreen Qt, files in a temp dir.
Run with: python -m unittest discover tests
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import addresses as addresses_module
import checkpoint_manager
import shutter_controller
import GUIv5
from data_updater_threaded import EurothermWorker, PressureWorker
from sim_controllers import SimEurothermController, SimPressureGaugeController

APP = QApplication.instance() or QApplication([])

LOOP = {"temperature_pv": (1, 1), "temperature_setpoint": (2, 1),
        "output_setpoint": (3, 1), "output_pv": (4, 1), "status": (273, 0),
        "Ramp_temp": (35, 1), "ramp_op_status": (87, 0), "Ramp_OP": (37, 2)}
ADDR = {
    "Au":        {"device": ("COM1", 1), **LOOP, "shutter_line": "Dev1/port0/line2"},
    "Se1":       {"device": ("COM1", 7), **LOOP, "shutter_line": "Dev1/port0/line3"},
    "Pressure":  {"device": ("COM10", 300), "command": "DS IG1 \r\n", "unit": "mbar"},
    "Liquid_N2": {"shutter_line": "Dev1/port0/line7"},
    "eGun1":     {"shutter_line": "Dev1/port1/line0"},
    "eGun2":     {"shutter_line": "Dev1/port1/line1"},
}


class SlowSim(SimEurothermController):
    """Never answers within the test: every read blocks, like a serial timeout."""
    def read_value(self, loop_id, name):
        time.sleep(2)
        raise ConnectionError("timeout")


class ConnectionGuiTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old_cwd = os.getcwd()
        os.chdir(tmp.name)                    # logs/ written here
        self.addCleanup(os.chdir, old_cwd)
        self.sims = {}
        self.gauge = SimPressureGaugeController("Pressure")
        for patcher in (
            mock.patch.dict(addresses_module.addresses, ADDR, clear=True),
            mock.patch.object(GUIv5, "SIMULATION", True),
            mock.patch.object(GUIv5, "make_controller", self.make_controller),
            mock.patch.object(GUIv5, "SimPressureGaugeController",
                              lambda name: self.gauge, create=True),
            mock.patch.object(shutter_controller, "nidaqmx", None),
            mock.patch.object(shutter_controller, "STATE_FILE",
                              os.path.join(tmp.name, "shutter_state.json")),
            mock.patch.object(checkpoint_manager, "_FILE",
                              os.path.join(tmp.name, "checkpoint.json")),
            # Fast polling; N stays the real value (10).
            mock.patch.object(EurothermWorker, "interval_s", 0.02),
            mock.patch.object(PressureWorker, "interval_s", 0.02),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.slow = set()

    def make_controller(self, loop_id):
        cls = SlowSim if loop_id in self.slow else SimEurothermController
        self.sims[loop_id] = cls(loop_id)
        return self.sims[loop_id]

    def open_window(self):
        window = GUIv5.MainWindow()
        self.addCleanup(window.close)
        return window

    def wait_for(self, window, condition, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            APP.processEvents()
            window._update_connection_status()     # the 1 s timer, called sooner
            if condition():
                return
            time.sleep(0.02)
        self.fail("condition not reached")

    def banner(self, window):
        return window._comm_label.text() if not window._comm_banner.isHidden() else None

    def log(self, window):
        return window.log_output.toPlainText()

    def test_answering_normally_no_alert(self):
        w = self.open_window()
        self.wait_for(w, w.connection_monitor.startup_done)
        self.assertTrue(w._checking_label.isHidden())
        self.assertIsNone(self.banner(w))
        self.assertIn("Startup check done: all 4 instruments answer.", self.log(w))
        for panel in w.instrument_panels:
            self.assertTrue(panel.comm_label.isHidden())

    def test_failing_fewer_than_n_then_answering_no_alert(self):
        w = self.open_window()
        self.wait_for(w, w.connection_monitor.startup_done)
        sim, failed = self.sims["Au"], []
        real_read = sim.read_value

        def read_failing_9_polls(loop_id, name):
            # First read of a poll cycle fails, for exactly N - 1 cycles.
            if name == "temperature_pv" and len(failed) < 9:
                failed.append(name)
                raise ConnectionError("no answer")
            return real_read(loop_id, name)
        sim.read_value = read_failing_9_polls
        self.wait_for(w, lambda: len(failed) == 9
                      and w.connection_monitor.snapshot()["Au"].failures == 0)
        self.assertIsNone(self.banner(w))
        self.assertNotIn("LOST", self.log(w))

    def test_lost_during_operation_then_recovered(self):
        w = self.open_window()
        self.wait_for(w, w.connection_monitor.startup_done)
        self.sims["Au"].fail = True
        self.wait_for(w, lambda: self.banner(w) is not None)
        self.assertRegex(self.banner(w), r"^⚠ Communication lost: Au \(since \d\d:\d\d\)$")
        au = next(p for p in w.instrument_panels if p.name == "Au")
        self.assertIn("⚠ No communication — last OK:", au.comm_label.text())
        self.assertIn("border: 2px solid #dc3545", au.styleSheet())
        self.assertIn("[Connection] Au: communication LOST (10 consecutive failed tries", self.log(w))
        self.assertTrue(w._comm_ack_btn.isHidden())      # nothing to acknowledge yet

        self.sims["Au"].fail = False
        self.wait_for(w, lambda: "recovered" in (self.banner(w) or ""))
        self.assertIn("[Connection] Au: communication RECOVERED", self.log(w))
        self.assertIn("✓ Communication recovered at", au.comm_label.text())
        self.assertNotIn("#dc3545", au.styleSheet())
        # The banner stays until acknowledged.
        time.sleep(0.3)
        self.wait_for(w, lambda: self.banner(w) is not None)
        self.assertFalse(w._comm_ack_btn.isHidden())
        w._comm_ack_btn.click()
        self.assertIsNone(self.banner(w))
        self.assertTrue(au.comm_label.isHidden())
        self.assertIn("[Connection] Recovery acknowledged: Au.", self.log(w))

    def test_several_instruments_lost(self):
        w = self.open_window()
        self.wait_for(w, w.connection_monitor.startup_done)
        self.sims["Au"].fail = True
        self.sims["Se1"].fail = True
        self.gauge.fail = True
        self.wait_for(w, lambda: all(n in (self.banner(w) or "") for n in ("Au", "Se1", "Pressure")))
        self.assertIn("No communication", w.instrument_panels[-1].comm_label.text())  # pressure panel

    def test_not_answering_at_startup(self):
        self.gauge.fail = True
        w = self.open_window()
        self.wait_for(w, w.connection_monitor.startup_done)
        self.assertRegex(self.banner(w), r"^⚠ Communication lost: Pressure \(since \d\d:\d\d\)$")
        self.assertIn("Last OK: never since startup", self.log(w))
        self.assertIn("Startup check done: 1 of 4 instruments not answering: Pressure.", self.log(w))

    def test_controller_init_failure_is_lost(self):
        def broken(loop_id):
            raise OSError("could not open port 'COM1'")
        with mock.patch.object(GUIv5, "make_controller", broken):
            w = self.open_window()
        self.wait_for(w, lambda: "Se1" in (self.banner(w) or ""))
        self.assertIn("Au", self.banner(w))
        self.assertIn("controller could not be initialized", self.log(w))

    def test_daq_write_failure_is_lost(self):
        w = self.open_window()
        self.wait_for(w, w.connection_monitor.startup_done)
        failing_daq = mock.MagicMock()
        failing_daq.Task.side_effect = OSError("Device identifier is invalid")
        with mock.patch.object(shutter_controller, "nidaqmx", failing_daq), \
                mock.patch.object(shutter_controller, "LineGrouping", mock.MagicMock()):
            self.assertFalse(w.shutter_ctrl.set_shutter("eGun1", "Dev1/port1/line0", True))
        self.wait_for(w, lambda: "NI-DAQ" in (self.banner(w) or ""))
        self.assertIn("NI-DAQ: ⚠ No communication", w._daq_label.text())
        self.assertIn("Device identifier is invalid", self.log(w))

    def test_daq_missing_on_instrument_pc_is_lost(self):
        with mock.patch.object(GUIv5, "SIMULATION", False), \
                mock.patch.object(GUIv5, "PressureGaugeController", lambda name: self.gauge), \
                mock.patch.object(GUIv5, "EurothermController", SimEurothermController):
            w = self.open_window()
        self.wait_for(w, lambda: "NI-DAQ" in (self.banner(w) or ""))
        self.assertIn("nidaqmx not installed", self.log(w))

    def test_startup_check_does_not_block_gui_thread(self):
        self.slow = {"Au", "Se1"}            # every read blocks 2 s in the worker threads
        start = time.time()
        w = self.open_window()
        self.assertLess(time.time() - start, 1.0)
        fired = []
        QTimer.singleShot(0, lambda: fired.append(time.time()))
        end = time.time() + 0.5
        while not fired and time.time() < end:
            APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(fired, "GUI event loop blocked")
        self.assertFalse(w._checking_label.isHidden())
        self.assertEqual(w._checking_label.text(), "Checking instruments...")


if __name__ == "__main__":
    unittest.main()
