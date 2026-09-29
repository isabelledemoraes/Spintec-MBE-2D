"""Run with: python -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import script_compiler
from script_compiler import compile_script, Level, CompileMessage

ADDR = {
    "Au":    {"temperature_pv": (1, 1), "shutter_line": "Dev1/port0/line1",
              "limits": {"max_temp_rate": 15, "min_power_rate": 0.01, "max_temp": 1100,
                         "power_ramp_allowed": False}},
    "Sub":   {"temperature_pv": (1, 1),
              "limits": {"max_temp_rate": 20, "min_power_rate": 0.01,
                         "max_temp": 900, "min_temp": 20, "power_ramp_allowed": True}},
    "NoLim": {"temperature_pv": (1, 1), "limits": {"max_temp": None}},
    "NoMin": {"temperature_pv": (1, 1), "limits": {"power_ramp_allowed": True}},
    "eGun1": {"shutter_line": "Dev1/port1/line0"},
}


def compile_(code):
    return compile_script(code, addresses=ADDR)


def error_texts(code):
    return [m.format() for m in compile_(code).errors]


class TestCompile(unittest.TestCase):
    def test_valid_script(self):
        r = compile_('power_ramp("Sub", 60, 2)\nhold(1)\n'
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

    # ── Hardware limits (#5) ──────────────────────────────────────────────────
    def test_ramp_rate_limits(self):
        self.assertEqual(error_texts(
            'temperature_ramp("Au", 500, 25)\n'
            'temperature_ramp("Au", 500, 15)\n'       # at the limit: OK
            'temperature_ramp("Sub", 500, 20)\n'      # substrate limit is 20
            'temperature_ramp("Sub", 500, 20.5)\n'
            'power_ramp("Sub", 30, 0.005)\n'
            'power_ramp("Sub", 30, 0.01)\n'           # at the limit: OK
            'power_ramp("Sub", 30, 50)\n'             # no max power rate
            'temperature_ramp("Au", 500, 0)'),
            ["ERROR line 1: temperature_ramp Au = 25 °C/min, max allowed 15 °C/min",
             "ERROR line 4: temperature_ramp Sub = 20.5 °C/min, max allowed 20 °C/min",
             "ERROR line 5: power_ramp Sub = 0.005 %/s, min allowed 0.01 %/s",
             "ERROR line 8: temperature_ramp Au = 0 °C/min, must be greater than 0 °C/min"])

    def test_power_ramp_not_allowed_on_cells(self):
        self.assertEqual(error_texts('power_ramp("Au", 30, 0.001)\npower_ramp("Sub", 30, 0.5)'),
                         ["ERROR line 1: power_ramp is not allowed on Au"])

    def test_temperature_target_limits(self):
        self.assertEqual(error_texts(
            'temperature_ramp("Au", 1250, 5)\n'
            'temperature_ramp("Au", 1100, 5)\n'       # at the limit: OK
            'temperature_ramp("Sub", 10, 5)\n'
            'temperature_ramp("Au", 5, 5)'),          # no min_temp for Au: OK
            ["ERROR line 1: temperature_ramp Au target = 1250 °C, max allowed 1100 °C",
             "ERROR line 3: temperature_ramp Sub target = 10 °C, min allowed 20 °C"])

    def test_keyword_arguments_are_checked(self):
        # check_commands already rejects keyword ramp arguments; limits are still reported.
        errors = error_texts('temperature_ramp("Au", normal_rate_min=30, target_temp=1200)\n'
                             'power_ramp("Sub", 30, rate=0.001)')
        for text in ["ERROR line 1: temperature_ramp Au = 30 °C/min, max allowed 15 °C/min",
                     "ERROR line 1: temperature_ramp Au target = 1200 °C, max allowed 1100 °C",
                     "ERROR line 2: power_ramp Sub = 0.001 %/s, min allowed 0.01 %/s"]:
            self.assertIn(text, errors)

    def test_missing_limit_is_error(self):
        self.assertEqual(error_texts(
            'temperature_ramp("NoLim", 100, 5)\npower_ramp("NoLim", 10, 1)\n'
            'wait_until_temperature("NoLim", 100)\npower_ramp("NoMin", 10, 1)'),
            ["ERROR line 1: No 'max_temp_rate' limit defined for NoLim in addresses.py",
             "ERROR line 1: No 'max_temp' limit defined for NoLim in addresses.py",
             "ERROR line 2: No 'power_ramp_allowed' limit defined for NoLim in addresses.py",
             "ERROR line 3: No 'max_temp' limit defined for NoLim in addresses.py",
             "ERROR line 4: No 'min_power_rate' limit defined for NoMin in addresses.py"])

    def test_wait_until_temperature_limits(self):
        self.assertEqual(error_texts(
            'power_ramp("Sub", 30, 0.5)\n'
            'wait_until_temperature("Au", 1150)\n'
            'temperature_ramp("Au", 200, 5)\n'
            'wait_until_temperature("Au", 300)\n'     # waiting while cooling down: OK
            'wait_until_temperature("Sub", 10)'),
            ["ERROR line 2: wait_until_temperature Au target = 1150 °C, max allowed 1100 °C",
             "ERROR line 5: wait_until_temperature Sub target = 10 °C, min allowed 20 °C"])

    def test_limit_checks_skip_invalid_arguments(self):
        # Wrong types / unknown loops are reported once, by check_commands.
        r = compile_('temperature_ramp("Au", "hot", "fast")\npower_ramp("Xx", 30, 0.001)')
        self.assertEqual([m.line for m in r.errors], [1, 1, 2])

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
