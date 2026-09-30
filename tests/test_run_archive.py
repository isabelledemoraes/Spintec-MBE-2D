"""
Run archive (#9) through the Run button: ScriptEditor + ShutterController,
no NI-DAQ, offscreen Qt. Run with: python -m unittest discover tests
"""
import ast
import os
import sys
import tempfile
import threading
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
from script_editor_integration import ScriptEditor, active_script_locks
from shutter_controller import ShutterController
from test_pressure_interlock import ADDR

APP = QApplication.instance() or QApplication([])

SCRIPT = """# eGun1 test at 20 °C
open_shutter("eGun1")
close_shutter("eGun1")
"""


class RunArchiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.folder = os.path.join(self.tmp, "archive")
        self.config = SimpleNamespace(MACHINE_NAME="mbe-test", SCRIPT_ARCHIVE_DIR=self.folder)
        self.executed = []   # script text parsed by the run thread = text executed
        self.alerts = []
        real_parse = ast.parse

        def parse(source, *args, **kw):
            if threading.current_thread() is not threading.main_thread():
                self.executed.append(source)
            return real_parse(source, *args, **kw)

        for patcher in (
            mock.patch.dict(addresses_module.addresses, ADDR, clear=True),
            mock.patch.object(script_editor_integration, "addresses_config", self.config),
            mock.patch.object(shutter_controller, "nidaqmx", None),
            mock.patch.object(shutter_controller, "STATE_FILE",
                              os.path.join(self.tmp, "shutter_state.json")),
            mock.patch.object(checkpoint_manager, "_FILE",
                              os.path.join(self.tmp, "checkpoint.json")),
            mock.patch.object(script_editor_integration.ast, "parse", parse),
            mock.patch.object(script_editor_integration.QMessageBox, "critical",
                              lambda parent, title, text: self.alerts.append(text)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.log = []
        self.shutters = ShutterController(pressure_gauge=None, addresses_map=ADDR)

    def editor(self, code=SCRIPT, name="Script 1"):
        editor = ScriptEditor(name, self.log.append, controllers={}, shutter_ctrl=self.shutters)
        editor.editor.setPlainText(code)
        editor.compile_current()
        return editor

    def press_run(self, editor):
        editor.run_script()
        if editor.thread is not None:
            editor.thread.join(timeout=10)
            self.assertFalse(editor.thread.is_alive())
        APP.processEvents()

    def archived(self):
        return sorted(os.listdir(self.folder)) if os.path.isdir(self.folder) else []

    def test_run_saves_pair_with_exact_executed_text(self):
        editor = self.editor()
        self.assertTrue(editor._last_report.ok, editor._last_report.format_lines())
        self.press_run(editor)
        files = self.archived()
        self.assertEqual(len(files), 2)
        script, report = files
        stamp = script[:len("YYYY-MM-DD_HH-MM-SS")]
        self.assertEqual(script, f"{stamp}_Script_1.txt")
        self.assertEqual(report, f"{stamp}_Script_1_report.txt")
        with open(os.path.join(self.folder, script), "rb") as f:
            saved = f.read().decode("utf-8")
        self.assertEqual(saved, editor.editor.toPlainText())
        self.assertTrue(self.executed)
        self.assertTrue(all(text == saved for text in self.executed))
        with open(os.path.join(self.folder, report), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Machine:          mbe-test", text)
        self.assertIn(f"Run started:      {stamp[:10]} {stamp[11:].replace('-', ':')}", text)
        self.assertIn("Software version: ", text)
        self.assertIn(editor._last_report.summary_line(), text)
        self.assertTrue(self.shutters.get_state("eGun1") is False)   # the script did run

    def test_report_is_recompiled_at_run(self):
        editor = self.editor()
        compiled_before = editor._last_report
        self.press_run(editor)
        self.assertIsNot(editor._last_report, compiled_before)
        self.assertGreaterEqual(editor._last_report.compiled_at, compiled_before.compiled_at)

    def test_two_runs_two_pairs(self):
        editor = self.editor()
        self.press_run(editor)
        editor.compile_current()
        self.press_run(editor)
        files = self.archived()
        self.assertEqual(len(files), 4)
        self.assertEqual(len([f for f in files if f.endswith("_report.txt")]), 2)

    def test_forbidden_characters_in_tab_name(self):
        self.press_run(self.editor(name='Au: growth / test?*'))
        self.assertTrue(self.archived()[0].endswith("_Au_growth_test.txt"))

    def test_missing_folder_is_created(self):
        self.config.SCRIPT_ARCHIVE_DIR = os.path.join(self.folder, "new", "sub")
        self.press_run(self.editor())
        self.assertEqual(len(os.listdir(self.config.SCRIPT_ARCHIVE_DIR)), 2)

    def assert_blocked(self, editor, reason):
        self.assertIsNone(editor.thread)
        self.assertEqual(self.executed, [])
        self.assertNotIn("Script 1", active_script_locks.values())
        self.assertFalse(editor.editor.isReadOnly())
        self.assertEqual(len(self.alerts), 1)
        self.assertIn(reason, self.alerts[0])
        self.assertTrue(any("Script NOT started" in m for m in self.log))

    def test_folder_not_writable_blocks_run(self):
        blocker = os.path.join(self.tmp, "a_file")
        open(blocker, "w").close()
        self.config.SCRIPT_ARCHIVE_DIR = os.path.join(blocker, "archive")
        editor = self.editor()
        self.press_run(editor)
        self.assert_blocked(editor, "cannot create archive folder")

    def test_write_error_blocks_run(self):
        editor = self.editor()
        with mock.patch("script_archive._write_new",
                        side_effect=PermissionError(13, "Permission denied")):
            self.press_run(editor)
        self.assert_blocked(editor, "Permission denied")
        self.assertEqual(self.archived(), [])

    def test_setting_missing_blocks_run(self):
        del self.config.SCRIPT_ARCHIVE_DIR
        editor = self.editor()
        self.press_run(editor)
        self.assert_blocked(editor, "SCRIPT_ARCHIVE_DIR is not set in addresses.py")

    def test_machine_name_missing_blocks_run(self):
        self.config.MACHINE_NAME = None
        editor = self.editor()
        self.press_run(editor)
        self.assert_blocked(editor, "MACHINE_NAME is not set in addresses.py")

    def test_compile_errors_nothing_saved(self):
        editor = self.editor('open_shutter("eGun1"\n')
        self.assertFalse(editor._last_report.ok)
        self.assertFalse(editor.run_button.isEnabled())
        self.press_run(editor)
        self.assertIsNone(editor.thread)
        self.assertEqual(self.archived(), [])
        self.assertFalse(os.path.exists(self.folder))
        self.assertEqual(self.alerts, [])


if __name__ == "__main__":
    unittest.main()
