"""
Run archive (#9): script_archive.py alone, in temp folders.
Run with: python -m unittest discover tests
"""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import script_archive
from script_archive import ArchiveError, safe_filename, save_run

NOW = datetime(2026, 9, 28, 14, 32, 5)
CODE = 'temperature_ramp("Au", 300, 10)\n# Au at 300 °C\nhold(1)'


class FakeReport:
    compiled_at = datetime(2026, 9, 28, 14, 32, 4)

    def format_lines(self):
        return ["WARNING line 1: something", "Compilation OK: 0 errors, 1 warning"]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.folder = os.path.join(self.tmp, "archive")
        self.config = SimpleNamespace(MACHINE_NAME="mbe1", SCRIPT_ARCHIVE_DIR=self.folder)

    def save(self, name="growth A", **kw):
        kw.setdefault("now", NOW)
        kw.setdefault("version", "abc1234")
        return save_run(CODE, FakeReport(), name, self.config, **kw)

    def read_bytes(self, path):
        with open(path, "rb") as f:
            return f.read()

    def test_saves_pair_with_same_timestamp_and_exact_text(self):
        script_path, report_path = self.save()
        self.assertEqual(os.path.basename(script_path), "2026-09-28_14-32-05_growth_A.txt")
        self.assertEqual(os.path.basename(report_path), "2026-09-28_14-32-05_growth_A_report.txt")
        # Byte for byte: UTF-8, no \r\n conversion.
        self.assertEqual(self.read_bytes(script_path), CODE.encode("utf-8"))
        with open(report_path, encoding="utf-8") as f:
            report = f.read().splitlines()
        self.assertIn("Machine:          mbe1", report)
        self.assertIn("Run started:      2026-09-28 14:32:05", report)
        self.assertIn("Software version: abc1234", report)
        self.assertIn("Script name:      growth A", report)
        self.assertIn("Script file:      2026-09-28_14-32-05_growth_A.txt", report)
        self.assertIn("Compiled at 2026-09-28 14:32:04", report)
        self.assertEqual(report[-2:], FakeReport().format_lines())

    def test_two_runs_same_second_never_overwrite(self):
        first = self.save()
        second = self.save()
        self.assertEqual(os.path.basename(second[0]), "2026-09-28_14-32-05_growth_A_2.txt")
        self.assertEqual(os.path.basename(second[1]),
                         "2026-09-28_14-32-05_growth_A_2_report.txt")
        self.assertEqual(len(os.listdir(self.folder)), 4)
        self.assertEqual(self.read_bytes(first[0]), self.read_bytes(second[0]))

    def test_two_runs_different_times(self):
        self.save()
        self.save(now=datetime(2026, 9, 28, 15, 0, 0))
        self.assertEqual(sorted(os.listdir(self.folder)), [
            "2026-09-28_14-32-05_growth_A.txt", "2026-09-28_14-32-05_growth_A_report.txt",
            "2026-09-28_15-00-00_growth_A.txt", "2026-09-28_15-00-00_growth_A_report.txt"])

    def test_name_colliding_with_a_report_file(self):
        # Script "x_report" must not take the report name of script "x" (same second).
        self.save("x")
        path, _ = self.save("x_report")
        self.assertEqual(os.path.basename(path), "2026-09-28_14-32-05_x_report_2.txt")
        self.assertEqual(len(os.listdir(self.folder)), 4)

    def test_safe_filename(self):
        cases = {
            "Script 1": "Script_1",
            'a:b/c\\d?e*f<g>h|i"j': "a_b_c_d_e_f_g_h_i_j",
            "  growth  test.  ": "growth_test",
            "Au\tCaF2\n": "Au_CaF2",
            "CON": "_CON",
            "com1.txt": "_com1.txt",
            "": "untitled",
            None: "untitled",
            "???": "untitled",
            "...": "untitled",
        }
        for name, expected in cases.items():
            self.assertEqual(safe_filename(name), expected, name)
        self.assertEqual(len(safe_filename("x" * 300)), script_archive.MAX_NAME_LEN)

    def test_forbidden_characters_give_a_valid_file(self):
        path, _ = self.save('run: Au/CaF2 \\ 50% ? *final*')
        self.assertEqual(os.path.basename(path), "2026-09-28_14-32-05_run_Au_CaF2_50%_final.txt")
        self.assertTrue(os.path.isfile(path))

    def test_new_unsaved_script_default_tab_name(self):
        path, _ = self.save("Script 1")
        self.assertEqual(os.path.basename(path), "2026-09-28_14-32-05_Script_1.txt")

    def test_missing_folder_is_created(self):
        self.config.SCRIPT_ARCHIVE_DIR = os.path.join(self.folder, "deep", "er")
        path, _ = self.save()
        self.assertTrue(os.path.isfile(path))

    def test_relative_folder_is_relative_to_project(self):
        _, folder = script_archive.archive_settings(
            SimpleNamespace(MACHINE_NAME="m", SCRIPT_ARCHIVE_DIR="run_archive"))
        self.assertEqual(folder, os.path.join(script_archive.PROJECT_DIR, "run_archive"))

    def test_folder_not_creatable(self):
        blocker = os.path.join(self.tmp, "a_file")
        open(blocker, "w").close()
        self.config.SCRIPT_ARCHIVE_DIR = os.path.join(blocker, "archive")
        with self.assertRaisesRegex(ArchiveError, "cannot create archive folder"):
            self.save()

    def test_write_failure_leaves_nothing(self):
        # Script written, then the disk is full while writing the report.
        disk_full = [None, OSError(28, "No space left on device")]
        with mock.patch.object(script_archive.os, "fsync", side_effect=disk_full):
            with self.assertRaisesRegex(ArchiveError, "No space left"):
                self.save()
        self.assertEqual(os.listdir(self.folder), [])   # script file removed too

    def test_permission_error_on_script(self):
        with mock.patch.object(script_archive, "_write_new",
                               side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaisesRegex(ArchiveError, "Permission denied"):
                self.save()

    def test_missing_settings(self):
        for config in (SimpleNamespace(SCRIPT_ARCHIVE_DIR=self.folder),
                       SimpleNamespace(MACHINE_NAME=None, SCRIPT_ARCHIVE_DIR=self.folder),
                       SimpleNamespace(MACHINE_NAME="  ", SCRIPT_ARCHIVE_DIR=self.folder)):
            self.config = config
            with self.assertRaisesRegex(ArchiveError, "MACHINE_NAME is not set"):
                self.save()
        for config in (SimpleNamespace(MACHINE_NAME="mbe1"),
                       SimpleNamespace(MACHINE_NAME="mbe1", SCRIPT_ARCHIVE_DIR=None),
                       SimpleNamespace(MACHINE_NAME="mbe1", SCRIPT_ARCHIVE_DIR="")):
            self.config = config
            with self.assertRaisesRegex(ArchiveError, "SCRIPT_ARCHIVE_DIR is not set"):
                self.save()

    def test_version_unknown_without_git(self):
        with mock.patch.object(script_archive.subprocess, "run",
                               side_effect=FileNotFoundError("git")):
            self.assertEqual(script_archive.detect_version(), "unknown")
        with mock.patch.object(script_archive.subprocess, "run",
                               side_effect=subprocess.CalledProcessError(128, "git")):
            self.assertEqual(script_archive.detect_version(), "unknown")

    def test_version_dirty(self):
        outputs = iter(["abc1234\n", " M script_archive.py\n"])
        with mock.patch.object(script_archive.subprocess, "run",
                               side_effect=lambda *a, **kw: SimpleNamespace(stdout=next(outputs))):
            self.assertEqual(script_archive.detect_version(), "abc1234-dirty")


if __name__ == "__main__":
    unittest.main()
