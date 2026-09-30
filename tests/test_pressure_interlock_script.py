"""
Pressure interlock (#8) through a real script run: ScriptEditor + ShutterController,
with a fake gauge, no NI-DAQ and offscreen Qt. Run with: python -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

import addresses as addresses_module
import checkpoint_manager
import script_editor_integration
import shutter_controller
from script_editor_integration import ScriptEditor
from shutter_controller import ShutterController
from test_pressure_interlock import ADDR, FakeGauge

APP = QApplication.instance() or QApplication([])

TWO_OPENINGS = """open_shutter("Liquid_N2")
close_shutter("Liquid_N2")
open_shutter("Liquid_N2")
open_shutter("eGun1")
"""


class ScriptRunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Test addresses everywhere; no NI-DAQ; state and checkpoint files in a temp dir.
        for patcher in (
            mock.patch.dict(addresses_module.addresses, ADDR, clear=True),
            mock.patch.object(shutter_controller, "nidaqmx", None),
            mock.patch.object(shutter_controller, "STATE_FILE",
                              os.path.join(tmp.name, "shutter_state.json")),
            mock.patch.object(checkpoint_manager, "_FILE",
                              os.path.join(tmp.name, "checkpoint.json")),
            # Run archive (#9) in the temp dir, not in the real SCRIPT_ARCHIVE_DIR.
            mock.patch.object(script_editor_integration, "addresses_config",
                              SimpleNamespace(MACHINE_NAME="test",
                                              SCRIPT_ARCHIVE_DIR=os.path.join(tmp.name, "archive"))),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_script(self, code, gauge):
        self.log = []
        self.writes = []
        self.shutters = ShutterController(pressure_gauge=gauge, addresses_map=ADDR)
        real_set = self.shutters.set_shutter

        def recording_set(loop_id, line, state):
            ok = real_set(loop_id, line, state)
            self.writes.append((loop_id, state))
            return ok
        self.shutters.set_shutter = recording_set

        editor = ScriptEditor("Test", self.log.append, controllers={},
                              shutter_ctrl=self.shutters)
        editor._interlock_alert.disconnect()   # no modal dialog in tests
        self.alerts = []
        editor._interlock_alert.connect(self.alerts.append)
        editor.editor.setPlainText(code)
        editor.compile_current()
        self.assertTrue(editor._last_report.ok, editor._last_report.format_lines())
        editor.run_script()
        editor.thread.join(timeout=10)
        self.assertFalse(editor.thread.is_alive())
        APP.processEvents()   # deliver the alert signal emitted by the runner thread
        return editor

    def test_check_runs_before_each_opening(self):
        gauge = FakeGauge(5e-9, 4e-9)
        editor = self.run_script(TWO_OPENINGS, gauge)
        self.assertEqual(gauge.reads, 2)
        self.assertEqual(self.writes, [("Liquid_N2", True), ("Liquid_N2", False),
                                       ("Liquid_N2", True), ("eGun1", True)])
        self.assertFalse(editor.stop_event.is_set())
        self.assertEqual(self.alerts, [])

    def test_second_opening_refused_stops_script(self):
        gauge = FakeGauge(5e-9, 3.2e-8)
        editor = self.run_script(TWO_OPENINGS, gauge)
        self.assertEqual(gauge.reads, 2)
        # Line 3 is not written, line 4 never runs.
        self.assertEqual(self.writes, [("Liquid_N2", True), ("Liquid_N2", False)])
        self.assertFalse(self.shutters.get_state("Liquid_N2"))
        msg = "Script stopped at line 3: IG1 = 3.2e-08 mbar, required < 1.0e-08 mbar"
        self.assertIn(f"[Test] {msg}", self.log)
        self.assertEqual(self.alerts, [msg])
        self.assertTrue(editor.stop_event.is_set())       # background ramp segments stop too
        self.assertIsNone(checkpoint_manager.load())       # not a crash: no resume offered

    def test_unavailable_gauge_stops_script(self):
        self.run_script(TWO_OPENINGS, None)
        self.assertEqual(self.writes, [])
        self.assertTrue(any("Script stopped at line 1: IG1 unavailable" in m for m in self.log))


if __name__ == "__main__":
    unittest.main()
