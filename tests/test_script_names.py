"""
Script names: ✏️ Rename button, Save/Load naming, unique names (the name owns the
loop/shutter locks). Offscreen Qt. Run with: python -m unittest discover tests
"""
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

import script_editor_integration as sei
from script_editor_integration import ScriptManager

APP = QApplication.instance() or QApplication([])


class ScriptNameTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        # save_script creates "scripts/" in the working directory.
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.tmp)
        self.warnings = []
        for patcher in (
            mock.patch.object(sei.QMessageBox, "warning",
                              lambda parent, title, text: self.warnings.append(text)),
            mock.patch.object(sei, "addresses_config",
                              SimpleNamespace(MACHINE_NAME="test",
                                              SCRIPT_ARCHIVE_DIR=os.path.join(self.tmp, "a"))),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.log = []
        self.manager = ScriptManager(self.log.append)
        self.first = self.manager.tabs.widget(0)

    def rename_via_dialog(self, editor, answer, ok=True):
        with mock.patch.object(sei.QInputDialog, "getText", return_value=(answer, ok)):
            editor.request_rename()

    def title(self, editor):
        return self.manager.tabs.tabText(self.manager.tabs.indexOf(editor))

    def test_rename_updates_label_tab_and_archive(self):
        self.rename_via_dialog(self.first, "  Au growth: 300°C  ")
        self.assertEqual(self.first.script_name, "Au growth: 300°C")
        self.assertEqual(self.first.name_label.text(), "Au growth: 300°C")
        self.assertEqual(self.title(self.first), "Au growth: 300°C")
        # The archive uses the new name, made safe for Windows.
        self.first.editor.setPlainText("hold(0)")
        self.first.compile_current()
        self.first.run_script()
        self.first.thread.join(timeout=10)
        files = os.listdir(os.path.join(self.tmp, "a"))
        self.assertTrue(any(f.endswith("_Au_growth_300°C.txt") for f in files), files)

    def test_cancel_or_empty_changes_nothing(self):
        self.rename_via_dialog(self.first, "other", ok=False)
        self.rename_via_dialog(self.first, "   ")
        self.assertEqual(self.first.script_name, "Script 1")
        self.assertEqual(self.title(self.first), "Script 1")

    def test_duplicate_name_refused(self):
        second = self.manager.add_new_tab()
        self.rename_via_dialog(second, "script 1")   # case-insensitive
        self.assertEqual(second.script_name, "Script 2")
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("already named", self.warnings[0])

    def test_rename_to_own_name_other_case_allowed(self):
        self.rename_via_dialog(self.first, "SCRIPT 1")
        self.assertEqual(self.first.script_name, "SCRIPT 1")
        self.assertEqual(self.warnings, [])

    def test_no_rename_while_running(self):
        release = threading.Event()
        self.first.thread = threading.Thread(target=release.wait)
        self.first.thread.start()
        self.addCleanup(release.set)
        with mock.patch.object(sei.QInputDialog, "getText") as dialog:
            self.first.request_rename()
        dialog.assert_not_called()
        self.assertFalse(self.first.set_name("other"))
        self.assertEqual(self.first.script_name, "Script 1")
        self.assertTrue(any("Cannot rename while the script is running" in m for m in self.log))

    def test_rename_button_disabled_during_run(self):
        self.first.editor.setPlainText("hold(0)")
        self.first.compile_current()
        self.first.run_script()
        self.assertFalse(self.first.rename_button.isEnabled())
        self.first.thread.join(timeout=10)
        APP.processEvents()   # end-of-run signal re-enables the editor
        self.assertTrue(self.first.rename_button.isEnabled())

    def test_save_renames_tab_after_file(self):
        path = os.path.join(self.tmp, "Au_growth_300C.py")
        with mock.patch.object(sei.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.first.save_script()
        self.assertEqual(self.first.script_name, "Au_growth_300C")
        self.assertEqual(self.title(self.first), "Au_growth_300C")

    def test_save_with_name_used_by_other_tab(self):
        self.manager.open_script_tab(os.path.join(self.tmp, "growth.py"), "hold(1)")
        path = os.path.join(self.tmp, "growth.py")
        with mock.patch.object(sei.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.first.save_script()
        self.assertEqual(self.first.script_name, "growth (2)")

    def test_save_again_under_same_name_keeps_it(self):
        path = os.path.join(self.tmp, "growth.py")
        with mock.patch.object(sei.QFileDialog, "getSaveFileName", return_value=(path, "")):
            self.first.save_script()
            self.first.save_script()
        self.assertEqual(self.first.script_name, "growth")

    def test_loading_same_file_twice_gives_distinct_names(self):
        path = os.path.join(self.tmp, "growth.py")
        a = self.manager.open_script_tab(path, "hold(1)")
        b = self.manager.open_script_tab(path, "hold(1)")
        c = self.manager.open_script_tab(path, "hold(1)")
        self.assertEqual([a.script_name, b.script_name, c.script_name],
                         ["growth", "growth (2)", "growth (3)"])
        # Distinct names = distinct lock owners: the second tab cannot take the first's loop.
        self.assertTrue(sei._try_acquire_loops(a.script_name, {"Au"}))
        self.addCleanup(sei._release_loops, a.script_name)
        self.assertFalse(sei._try_acquire_loops(b.script_name, {"Au"}))

    def test_new_tab_skips_name_taken_by_rename(self):
        self.rename_via_dialog(self.first, "Script 2")
        second = self.manager.add_new_tab()
        self.assertEqual(second.script_name, "Script 3")


if __name__ == "__main__":
    unittest.main()
