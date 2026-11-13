"""
Commented function list in new script tabs + warning for scripts without commands.
Offscreen Qt. Run with: python -m unittest discover tests
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

import script_editor_integration as sei
from script_compiler import DSL_PARAMS, Level, compile_script
from script_template import SCRIPT_TEMPLATE

APP = QApplication.instance() or QApplication([])

ADDRESSES = {}


class TemplateTextTests(unittest.TestCase):
    def test_every_line_is_a_comment(self):
        for line in SCRIPT_TEMPLATE.splitlines():
            self.assertTrue(line.startswith("#"), line)

    def test_every_script_function_is_listed_with_its_parameters(self):
        for name, params in DSL_PARAMS.items():
            lines = [l for l in SCRIPT_TEMPLATE.splitlines()
                     if l.startswith(f"# {name}(")]
            self.assertEqual(len(lines), 1, f"{name} must be listed once")
            # Arguments in the same order as the real function: count the commas.
            call = lines[0][len(f"# {name}("):].split(")")[0]
            self.assertEqual(len(call.split(",")), len(params), lines[0])

    def test_template_compiles_with_only_the_no_command_warning(self):
        report = compile_script(SCRIPT_TEMPLATE, addresses=ADDRESSES)
        self.assertTrue(report.ok)
        self.assertEqual([m.level for m in report.messages], [Level.WARNING])
        self.assertIn("no commands", report.messages[0].text)

    def test_empty_script_warns_but_does_not_block(self):
        report = compile_script("", addresses=ADDRESSES)
        self.assertTrue(report.ok)
        self.assertEqual([m.level for m in report.messages], [Level.WARNING])


class NewTabTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(sei, "addresses_config")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = sei.ScriptManager(lambda msg: None)

    def test_new_tab_has_the_template(self):
        editor = self.manager.add_new_tab()
        self.assertEqual(editor.editor.toPlainText(), SCRIPT_TEMPLATE)
        self.assertNotIn('temperature_ramp("Au", 300, 10)\nhold(1)\ntemperature_ramp',
                         editor.editor.toPlainText())

    def test_loaded_script_does_not_get_the_template(self):
        editor = self.manager.open_script_tab("x/loaded.txt", "hold(1)")
        self.assertEqual(editor.editor.toPlainText(), "hold(1)")


if __name__ == "__main__":
    unittest.main()
