"""Run with: python -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ramp_planning import plan_ramp_segments

CZ = {"range": (1014, 1114), "slow_rate": 3.0}


def plan(start, target, rate, cz=CZ):
    return [(s.start, s.target, s.rate, s.in_critical_zone)
            for s in plan_ramp_segments(start, target, rate, cz)]


class TestPlanRampSegments(unittest.TestCase):
    def test_no_zone(self):
        self.assertEqual(plan(20, 500, 10, None), [(20, 500, 10, False)])

    def test_through_zone_up_and_down(self):
        self.assertEqual(plan(900, 1200, 10),
                         [(900, 1014, 10, False), (1014, 1114, 3.0, True), (1114, 1200, 10, False)])
        self.assertEqual(plan(1200, 900, 10),
                         [(1200, 1114, 10, False), (1114, 1014, 3.0, True), (1014, 900, 10, False)])

    def test_start_or_end_inside_zone(self):
        self.assertEqual(plan(900, 1050, 10), [(900, 1014, 10, False), (1014, 1050, 3.0, True)])
        self.assertEqual(plan(1050, 1200, 10), [(1050, 1114, 3.0, True), (1114, 1200, 10, False)])
        self.assertEqual(plan(1050, 1020, 10), [(1050, 1020, 3.0, True)])

    def test_outside_zone_and_edges(self):
        self.assertEqual(plan(20, 1014, 10), [(20, 1014, 10, False)])
        self.assertEqual(plan(1114, 1300, 10), [(1114, 1300, 10, False)])
        self.assertEqual(plan(1300, 1114, 10), [(1300, 1114, 10, False)])
        self.assertEqual(plan(500, 500, 10), [])

    def test_zone_never_faster_than_requested(self):
        self.assertEqual(plan(1000, 1120, 1),
                         [(1000, 1014, 1, False), (1014, 1114, 1, True), (1114, 1120, 1, False)])

    def test_duration(self):
        segs = plan_ramp_segments(900, 1200, 10, CZ)
        self.assertAlmostEqual(sum(s.duration_min for s in segs), 11.4 + 100 / 3 + 8.6)


if __name__ == "__main__":
    unittest.main()
