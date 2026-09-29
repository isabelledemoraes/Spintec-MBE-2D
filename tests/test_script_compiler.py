"""Run with: python -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import script_compiler
from script_compiler import compile_script, Level, CompileMessage

ADDR = {
    "Au":    {"temperature_pv": (1, 1), "shutter_line": "Dev1/port0/line1"},
    "eGun1": {"shutter_line": "Dev1/port1/line0"},
}


def compile_(code):
    return compile_script(code, addresses=ADDR)


class TestCompile(unittest.TestCase):
    def test_valid_script(self):
        r = compile_('power_ramp("Au", 60, 2)\nhold(1)\n'
                     'wait_until_temperature("Au", 300, tolerance=1, timeout=None)\n'
                     'open_shutter("eGun1")\nwait_until("2026-10-01 08:30")')
        self.assertTrue(r.ok, r.format_lines())
        self.assertEqual(r.summary_line(), "Compilation OK: 0 errors, 0 warnings")

    def test_misspelled_command(self):
        r = compile_('hold(1)\nhodl(2)')
        self.assertFalse(r.ok)
        self.assertEqual((r.errors[0].line, r.errors[0].text), (2, "Function 'hodl' is not allowed."))

    def test_existing_argument_checks(self):
        r = compile_('power_ramp("Xx", "a", 2)\nhold()\nwait_until("tomorrow")\nclose_shutter("Nope")')
        self.assertEqual([m.line for m in r.errors], [1, 1, 2, 3, 4])

    def test_syntax_error(self):
        r = compile_('hold(1\n')
        self.assertEqual(len(r.errors), 1)
        self.assertTrue(r.errors[0].text.startswith("Syntax Error"))

    def test_non_command_statements(self):
        r = compile_('hold\nfoo\nx = 5\nimport os\nfor i in [1]:\n    hold(1)')
        self.assertEqual([m.line for m in r.errors], [1, 2, 3, 4, 5])
        self.assertIn("missing parentheses", r.errors[0].text)

    def test_keyword_arguments(self):
        r = compile_('wait_until_temperature("Au", 300, tol=1)\n'
                     'wait_until_temperature("Au", 300, 0.5, tolerance=1)\n'
                     'wait_until_temperature("Au", 300, tolerance="x", timeout="y")')
        self.assertEqual([m.line for m in r.errors], [1, 2, 3, 3])

    def test_order_summary_and_warning_only(self):
        def warn(ctx):
            ctx.summary["Estimated duration"] = "1 min"
            return [CompileMessage(Level.WARNING, 1, "careful")]
        script_compiler.CHECKS.append(warn)
        try:
            r = compile_('hold(1)')
            self.assertTrue(r.ok)
            self.assertEqual(r.format_lines(), ["WARNING line 1: careful",
                                                "Compilation OK: 0 errors, 1 warning",
                                                "Estimated duration: 1 min"])
            r = compile_('hold(1)\nbad(1)')
            self.assertEqual([m.level for m in r.messages], [Level.ERROR, Level.WARNING])
        finally:
            script_compiler.CHECKS.remove(warn)

    def test_crashing_check_blocks_run(self):
        def broken(ctx):
            raise RuntimeError("boom")
        script_compiler.CHECKS.append(broken)
        try:
            r = compile_('hold(1)')
            self.assertFalse(r.ok)
            self.assertIn("broken", r.errors[0].text)
        finally:
            script_compiler.CHECKS.remove(broken)


if __name__ == "__main__":
    unittest.main()
