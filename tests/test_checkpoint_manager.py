"""
Checkpoint as a crash record (#10): save/load/clear and the startup message.
Run with: python -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import checkpoint_manager
from checkpoint_manager import crash_message

NOW = datetime(2026, 9, 30, 9, 0, 0)


class CheckpointFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "checkpoint.json")
        patcher = mock.patch.object(checkpoint_manager, "_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_save_then_load(self):
        checkpoint_manager.save("growth A", 13, 32)
        cp = checkpoint_manager.load()
        self.assertEqual(cp["script_name"], "growth A")
        self.assertEqual(cp["stmt_index"], 13)
        self.assertEqual(cp["stmt_count"], 32)
        self.assertNotIn("script_text", cp)
        saved_at = datetime.fromisoformat(cp["saved_at"])
        self.assertLess(abs((datetime.now() - saved_at).total_seconds()), 5)

    def test_no_file_is_none(self):
        self.assertIsNone(checkpoint_manager.load())

    def test_corrupted_file_is_none(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(checkpoint_manager.load())

    def test_not_a_dict_is_none(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("[1, 2]")
        self.assertIsNone(checkpoint_manager.load())

    def test_clear(self):
        checkpoint_manager.save("growth A", 0, 1)
        checkpoint_manager.clear()
        self.assertIsNone(checkpoint_manager.load())
        checkpoint_manager.clear()   # no file: no error


class CrashMessageTests(unittest.TestCase):
    def test_full_checkpoint_same_day(self):
        cp = {"script_name": "growth A", "stmt_index": 13, "stmt_count": 32,
              "saved_at": "2026-09-30T03:12:45"}
        self.assertEqual(
            crash_message(cp, now=NOW),
            "⚠ Previous session ended unexpectedly while running growth A "
            "(statement 14 of 32, started 03:12).")

    def test_previous_day_shows_date(self):
        cp = {"script_name": "growth A", "stmt_index": 0, "stmt_count": 2,
              "saved_at": "2026-09-29T23:50:00"}
        self.assertIn("(statement 1 of 2, started 2026-09-29 23:50)", crash_message(cp, now=NOW))

    def test_old_format_checkpoint(self):
        # Before #10: script_text, no stmt_count, no saved_at.
        cp = {"script_name": "growth A", "script_text": "hold(1)\n", "stmt_index": 4}
        self.assertEqual(
            crash_message(cp, now=NOW),
            "⚠ Previous session ended unexpectedly while running growth A (statement 5).")

    def test_bad_fields_are_left_out(self):
        cp = {"stmt_index": "x", "stmt_count": 3, "saved_at": "yesterday"}
        self.assertEqual(
            crash_message(cp, now=NOW),
            "⚠ Previous session ended unexpectedly while running an unnamed script.")


if __name__ == "__main__":
    unittest.main()
