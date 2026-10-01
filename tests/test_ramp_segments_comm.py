"""
Multi-segment temperature_ramp (critical zone) when the PV cannot be read (#12):
the next segment must never be written until the PV is readable and at the
previous segment's target. Simulation only, offscreen Qt.
Run with: python -m unittest discover tests
"""
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

import addresses as addresses_module
import checkpoint_manager
import script_editor_integration
from script_editor_integration import ScriptEditor
from sim_controllers import SimEurothermController

APP = QApplication.instance() or QApplication([])
REAL_SLEEP = time.sleep

ADDR = {
    "Au": {
        "device": ("COM1", 1),
        "limits": {"max_temp_rate": 15.0, "power_ramp_allowed": False,
                   "min_power_rate": 0.01, "max_temp": 1300},
        "temperature_pv": (1, 1), "temperature_setpoint": (2, 1),
        "output_setpoint": (3, 1), "output_pv": (4, 1), "status": (273, 0),
        "Ramp_temp": (35, 1), "ramp_op_status": (87, 0), "Ramp_OP": (37, 2),
        "critical_zone": {"range": (1014, 1114), "slow_rate": 3.0},
    },
}


class PvController(SimEurothermController):
    """PV set by the test; PV reads fail while read_fails is True. Records writes."""
    def __init__(self):
        super().__init__("Au")
        self.connect()
        self.pv = 25.0
        self.read_fails = False
        self.writes = []

    def read_value(self, loop_id, name):
        if name == "temperature_pv":
            if self.read_fails:
                raise ConnectionError("[Au] Read failed after retries.")
            return self.pv
        return super().read_value(loop_id, name)

    def write_value(self, loop_id, name, value):
        self.writes.append((name, value))
        super().write_value(loop_id, name, value)

    def write_register(self, loop_id, reg_name, value):
        self.writes.append((reg_name, value))
        super().write_register(loop_id, reg_name, value)


class FastTime:
    """script_editor_integration.time with short sleeps (segment poll is 2 s)."""
    def __getattr__(self, name):
        return getattr(time, name)

    @staticmethod
    def sleep(seconds):
        REAL_SLEEP(min(seconds, 0.01))


class SegmentHoldTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for patcher in (
            mock.patch.dict(addresses_module.addresses, ADDR, clear=True),
            mock.patch.object(checkpoint_manager, "_FILE",
                              os.path.join(tmp.name, "checkpoint.json")),
            mock.patch.object(script_editor_integration, "addresses_config",
                              SimpleNamespace(MACHINE_NAME="test",
                                              SCRIPT_ARCHIVE_DIR=os.path.join(tmp.name, "archive"))),
            mock.patch.object(script_editor_integration, "time", FastTime()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def wait_for(self, condition, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if condition():
                return
            REAL_SLEEP(0.01)
        self.fail("condition not reached")

    def test_no_next_segment_while_pv_unreadable(self):
        ctrl = PvController()
        log = []
        editor = ScriptEditor("Test", log.append, controllers={"Au": ctrl})
        self.addCleanup(lambda: (editor.stop_script(), editor.thread.join(5)))
        editor.editor.setPlainText('temperature_ramp("Au", 1200, 10)\n')
        editor.compile_current()
        self.assertTrue(editor._last_report.ok, editor._last_report.format_lines())
        editor.run_script()

        segment_2 = [("Ramp_temp", 3.0), ("temperature_setpoint", 1114)]
        self.wait_for(lambda: ("temperature_setpoint", 1014) in ctrl.writes)   # segment 1

        # Communication lost while ramping to 1014: segment 2 must not start.
        ctrl.read_fails = True
        self.wait_for(lambda: any("Cannot read PV" in m for m in log))
        REAL_SLEEP(0.3)                                    # many poll cycles
        for write in segment_2:
            self.assertNotIn(write, ctrl.writes)
        self.assertEqual(sum("Cannot read PV" in m for m in log), 1)   # logged once

        # Back, but not at 1014 yet: still waiting.
        ctrl.pv = 700.0
        ctrl.read_fails = False
        self.wait_for(lambda: any("PV readable again (700.0°C)" in m for m in log))
        REAL_SLEEP(0.2)
        for write in segment_2:
            self.assertNotIn(write, ctrl.writes)

        # Target of segment 1 reached: segment 2 (slow rate through the zone).
        ctrl.pv = 1013.0
        self.wait_for(lambda: ("temperature_setpoint", 1114) in ctrl.writes)
        self.assertIn(("Ramp_temp", 3.0), ctrl.writes)
        self.assertNotIn(("temperature_setpoint", 1200), ctrl.writes)


if __name__ == "__main__":
    unittest.main()
