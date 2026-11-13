"""
❓ Help pop-up (issue #1) + warning for scripts without commands.
Offscreen Qt. Run with: python -m unittest discover tests
"""
import ast
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

import script_editor_integration as sei
from script_compiler import DSL_PARAMS, Level, compile_script
from script_help import HELP_ENTRIES, ScriptHelpDialog, all_syntax_text

APP = QApplication.instance() or QApplication([])


class HelpEntriesTests(unittest.TestCase):
    def test_every_script_function_is_listed_once(self):
        names = [name for name, _, _ in HELP_ENTRIES]
        self.assertCountEqual(names, DSL_PARAMS)

    def test_syntax_matches_the_real_parameters(self):
        for name, syntax, _ in HELP_ENTRIES:
            call = ast.parse(syntax).body[0].value
            self.assertEqual(call.func.id, name)
            params = DSL_PARAMS[name]
            given = len(call.args) + len(call.keywords)
            self.assertEqual(given, len(params), syntax)
            for kw in call.keywords:
                self.assertIn(kw.arg, params, syntax)

    def test_every_description_is_filled(self):
        for _, _, description in HELP_ENTRIES:
            self.assertTrue(description.strip())


class NoCommandWarningTests(unittest.TestCase):
    def test_empty_and_comment_only_scripts_warn_but_do_not_block(self):
        for code in ("", "# only a comment\n"):
            report = compile_script(code, addresses={})
            self.assertTrue(report.ok)
            self.assertEqual([m.level for m in report.messages], [Level.WARNING])
            self.assertIn("no commands", report.messages[0].text)


class HelpDialogTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sei, "addresses_config")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = sei.ScriptManager(lambda msg: None)

    def test_new_tab_is_empty_and_loaded_script_is_untouched(self):
        self.assertEqual(self.manager.add_new_tab().editor.toPlainText(), "")
        loaded = self.manager.open_script_tab("x/loaded.txt", "hold(1)")
        self.assertEqual(loaded.editor.toPlainText(), "hold(1)")

    def test_help_button_opens_one_non_modal_dialog(self):
        editor = self.manager.add_new_tab()
        editor.help_button.click()
        dialog = editor._help_dialog
        self.assertIsInstance(dialog, ScriptHelpDialog)
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.isModal())
        editor.help_button.click()
        self.assertIs(editor._help_dialog, dialog)
        dialog.close()

    def test_copy_buttons_fill_the_clipboard(self):
        dialog = ScriptHelpDialog()
        clipboard = QApplication.clipboard()
        for button, (_, syntax, _) in zip(dialog.copy_buttons, HELP_ENTRIES):
            button.click()
            self.assertEqual(clipboard.text(), syntax)
        dialog.copy_all_button.click()
        self.assertEqual(clipboard.text(), all_syntax_text())
        self.assertEqual(len(dialog.syntax_fields), len(HELP_ENTRIES))


if __name__ == "__main__":
    unittest.main()
