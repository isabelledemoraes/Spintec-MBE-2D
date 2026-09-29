"""
Temperature ramp planning, shared by the script runtime (temperature_ramp) and
the compiler (critical zone warnings, time estimation).

Pure functions: no hardware, no Qt.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    start: float             # °C
    target: float            # °C
    rate: float              # °C/min
    in_critical_zone: bool

    @property
    def duration_min(self) -> float:
        return abs(self.target - self.start) / self.rate if self.rate > 0 else 0.0


def plan_ramp_segments(start: float, target: float, rate: float,
                       critical_zone: dict | None = None) -> list[Segment]:
    """
    Split a ramp start -> target into segments. The part inside the critical zone
    (addresses[loop_id]["critical_zone"]: {"range": (a, b), "slow_rate": r}) is ramped
    at min(rate, slow_rate): the zone never makes a ramp faster than requested.
    Returns [] when start == target.
    """
    segments = []
    if start == target:
        return segments

    def add(seg_target, seg_rate, in_zone):
        seg_start = segments[-1].target if segments else start
        segments.append(Segment(seg_start, seg_target, seg_rate, in_zone))

    plan_temp = start
    if critical_zone:
        cz_start, cz_end = min(critical_zone["range"]), max(critical_zone["range"])
        cz_rate = min(rate, critical_zone["slow_rate"])
        if target > start:
            if plan_temp < cz_start and target > cz_start:
                add(cz_start, rate, False)
                plan_temp = cz_start
            if plan_temp < cz_end and target > cz_start:
                plan_temp = min(cz_end, target)
                add(plan_temp, cz_rate, True)
        else:
            if plan_temp > cz_end and target < cz_end:
                add(cz_end, rate, False)
                plan_temp = cz_end
            if plan_temp > cz_start and target < cz_end:
                plan_temp = max(cz_start, target)
                add(plan_temp, cz_rate, True)

    if plan_temp != target:
        add(target, rate, False)
    return segments
