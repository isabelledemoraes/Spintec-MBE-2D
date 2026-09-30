"""Connection monitor (#12), pure logic with a fake clock. Run with: python -m unittest discover tests"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from connection_monitor import (ConnectionMonitor, MAX_FAILURES, CHECKING, CONNECTED,
                                FAILING, LOST, RECOVERED, panel_text)

# 2026-09-30 03:12:00 local time
T0 = time.mktime((2026, 9, 30, 3, 12, 0, 0, 0, -1))
ERR = ConnectionError("Read failed after retries.")


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.mon = ConnectionMonitor(["Au", "Pressure"], clock=self.clock)

    def fail(self, name, times):
        for _ in range(times):
            self.mon.report_failure(name, ERR)
            self.clock.advance(2)

    def state(self, name):
        return self.mon.snapshot()[name].state

    def lost_events(self):
        return [e for e in self.mon.drain_events() if "LOST" in e or "RECOVERED" in e]

    def test_default_is_ten_tries(self):
        self.assertEqual(MAX_FAILURES, 10)

    def test_answering_normally_no_alert(self):
        for _ in range(5):
            self.mon.report_success("Au")
            self.mon.report_success("Pressure")
        self.assertEqual(self.state("Au"), CONNECTED)
        self.assertIsNone(self.mon.banner_text())
        self.assertEqual(self.mon.drain_events(),
                         ["Startup check done: all 2 instruments answer."])

    def test_fewer_than_n_failures_then_answer_no_alert(self):
        self.mon.report_success("Au")
        self.fail("Au", MAX_FAILURES - 1)
        self.assertEqual(self.state("Au"), FAILING)
        self.assertIsNone(self.mon.banner_text())
        self.mon.report_success("Au")
        self.assertEqual(self.state("Au"), CONNECTED)
        self.assertIsNone(self.mon.banner_text())
        self.assertEqual(self.lost_events(), [])

    def test_failures_must_be_consecutive(self):
        self.fail("Au", MAX_FAILURES - 1)
        self.mon.report_success("Au")
        self.fail("Au", MAX_FAILURES - 1)
        self.assertNotEqual(self.state("Au"), LOST)

    def test_n_failures_lost_and_alert(self):
        self.mon.report_success("Au")
        self.clock.advance(10)               # first failure at 03:12:10
        self.fail("Au", MAX_FAILURES)
        st = self.mon.snapshot()["Au"]
        self.assertEqual(st.state, LOST)
        self.assertEqual(st.lost_since, T0 + 10)   # since the first failed try
        self.assertEqual(self.mon.banner_text(), "⚠ Communication lost: Au (since 03:12)")
        events = self.lost_events()
        self.assertEqual(len(events), 1)
        self.assertIn("Au: communication LOST (10 consecutive failed tries", events[0])
        self.assertIn("Read failed after retries.", events[0])
        self.assertIn("Last OK: 03:12:00", events[0])
        self.assertEqual(panel_text(st), "⚠ No communication — last OK: 03:12:00")
        # Further failures do not repeat the event.
        self.fail("Au", 5)
        self.assertEqual(self.lost_events(), [])

    def test_not_answering_at_startup(self):
        self.mon.report_success("Pressure")
        self.fail("Au", MAX_FAILURES - 1)
        self.assertFalse(self.mon.startup_done())
        self.assertEqual(self.state("Au"), FAILING)
        self.fail("Au", 1)
        self.assertTrue(self.mon.startup_done())
        events = self.mon.drain_events()
        self.assertIn("Last OK: never since startup.", events[0])
        self.assertEqual(events[-1],
                         "Startup check done: 1 of 2 instruments not answering: Au.")
        self.assertIn("never since startup", panel_text(self.mon.snapshot()["Au"]))

    def test_recovered_banner_stays_until_acknowledged(self):
        self.mon.report_success("Pressure")
        self.fail("Au", MAX_FAILURES)
        self.clock.advance(8 * 60)           # answers again at 03:20:20
        self.mon.report_success("Au")
        st = self.mon.snapshot()["Au"]
        self.assertEqual(st.state, RECOVERED)
        self.assertEqual(self.mon.banner_text(),
                         "⚠ Communication lost: Au (recovered at 03:20)")
        self.assertTrue(self.mon.has_recovered())
        self.assertIn("✓ Communication recovered at 03:20:20", panel_text(st))
        events = self.lost_events()
        self.assertIn("Au: communication RECOVERED (lost since 03:12:00).", events)
        # Still there while answering normally...
        self.mon.report_success("Au")
        self.assertIsNotNone(self.mon.banner_text())
        # ...until acknowledged.
        self.assertEqual(self.mon.acknowledge(), ["Au"])
        self.assertIsNone(self.mon.banner_text())
        self.assertEqual(self.state("Au"), CONNECTED)
        self.assertEqual(panel_text(self.mon.snapshot()["Au"]), "")

    def test_acknowledge_keeps_still_lost(self):
        self.fail("Au", MAX_FAILURES)
        self.fail("Pressure", MAX_FAILURES)
        self.mon.report_success("Au")
        self.assertEqual(self.mon.acknowledge(), ["Au"])
        self.assertEqual(self.mon.banner_text(),
                         "⚠ Communication lost: Pressure (since 03:12)")
        self.assertFalse(self.mon.has_recovered())

    def test_recovered_then_lost_again(self):
        self.fail("Au", MAX_FAILURES)
        self.mon.report_success("Au")
        self.fail("Au", MAX_FAILURES)
        self.assertEqual(self.state("Au"), LOST)
        self.assertFalse(self.mon.has_recovered())
        self.assertNotIn("recovered", self.mon.banner_text())

    def test_several_instruments_lost(self):
        self.mon.add("NI-DAQ", max_failures=1)
        self.fail("Au", MAX_FAILURES)
        self.clock.advance(3 * 60)
        self.fail("Pressure", MAX_FAILURES)
        self.mon.report_failure("NI-DAQ", "write failed")
        self.assertEqual(
            self.mon.banner_text(),
            "⚠ Communication lost: Au (since 03:12), Pressure (since 03:15), NI-DAQ (since 03:15)")

    def test_mark_lost_is_immediate(self):
        self.mon.mark_lost("Au", "controller could not be initialized")
        self.assertEqual(self.state("Au"), LOST)
        self.assertIn("controller could not be initialized", self.lost_events()[0])

    def test_checking_before_first_answer(self):
        self.assertEqual(self.state("Au"), CHECKING)
        self.assertFalse(self.mon.startup_done())
        self.assertIsNone(self.mon.banner_text())


if __name__ == "__main__":
    unittest.main()
