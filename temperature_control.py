# temperature_control.py
"""
Temperature changes on a Eurotherm loop — one implementation for both users:

  * scripts            temperature_ramp("Au", 1100, 10)   (script_editor_integration)
  * General View       "Set temperature" of each cell     (instrument_panel)

start_temperature_ramp() is the run-time sequence (ramp generator interlock,
AUTO mode, critical-zone segments, fire-and-return).
check_manual_ramp() applies to a manual change exactly the rules Compile
applies to a script line: it compiles the equivalent temperature_ramp command
with script_compiler, so limits, critical zones and the time estimate cannot
differ between the two.

No Qt here.
"""
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import machine_config
from ramp_planning import plan_ramp_segments
from script_compiler import compile_script, format_minutes, Level

SEGMENT_TOLERANCE = 3.0     # °C: a segment is finished when the PV is this close to its target
SEGMENT_POLL_S = 2          # s between two PV reads while waiting for a segment


def loop_flag(addresses: dict, loop_id: str, key: str) -> bool:
    """Per-loop boolean option of the configuration (False when absent)."""
    return bool(addresses.get(loop_id, {}).get(key, False))


def start_temperature_ramp(ctrl, loop_id, target_temp, normal_rate_min, *,
                           addresses, log, stop_event, max_rate_cap=None):
    """
    Start a temperature ramp and return at once (the hardware does the ramp).

    Returns the estimated end (datetime) when a ramp was started, else None
    (already at target, direct-setpoint loop, or the sequence was aborted — the
    reason is logged).

    - Loops with "script_direct_setpoint": the setpoint is written, nothing else.
    - Others: ramp generator verified OFF (skipped for "script_keep_ramp_generator"
      loops, whose state is restored at the end), AUTO mode, then the first
      segment is written. If the ramp crosses the critical zone, a daemon thread
      writes the next segments when the PV reaches each boundary; a failed PV
      read never moves on. stop_event stops that thread.
    """
    cap = machine_config.MAX_TEMP_RATE_CAP if max_rate_cap is None else max_rate_cap
    if normal_rate_min > cap:
        log(f"[{loop_id}] WARNING: Requested rate capped at {cap}°C/min.")
        normal_rate_min = cap

    # Special case: loops with "script_direct_setpoint" just get the setpoint
    # written, no ramp generator / OP changes.
    if loop_flag(addresses, loop_id, "script_direct_setpoint"):
        log(f"[{loop_id}] Using simple GUI-style temperature set: "
            f"writing setpoint to {target_temp}°C (no ramp logic, no ramp/OP changes).")
        try:
            ctrl.write_value(loop_id, "temperature_setpoint", target_temp)
            log(f"[{loop_id}] New temperature setpoint = {target_temp}°C.")
        except Exception as e:
            log(f"[{loop_id}] Error setting temperature setpoint: {e}")
        return None

    log(f"[{loop_id}] Planning temperature ramp to {target_temp}°C.")

    # For "script_keep_ramp_generator" loops the OP ramp / ramp generator is not
    # forced OFF: its initial state is remembered and restored at the end.
    skip_ramp_safety = loop_flag(addresses, loop_id, "script_keep_ramp_generator")

    try:
        initial_ramp_status = ctrl.get_ramp_status(loop_id)  # "on" or "off"
    except Exception as e:
        log(f"[{loop_id}] WARNING: Could not read initial ramp status: {e}")
        initial_ramp_status = None

    # --- SAFETY INTERLOCK (skipped for "script_keep_ramp_generator" loops) ---
    if not skip_ramp_safety:
        try:
            log(f"[{loop_id}] Safety Interlock: Ensuring ramp generator is disabled.")
            ramp_disabled = False
            for i in range(3):
                ctrl.set_ramp_status(loop_id, False)
                status = ctrl.get_ramp_status(loop_id)
                if status == "off":
                    ramp_disabled = True
                    log(f"[{loop_id}] Ramp generator successfully verified as OFF.")
                    break
                log(f"[{loop_id}] Verification failed (attempt {i + 1}/3). Retrying...")
                time.sleep(0.3)

            if not ramp_disabled:
                log(f"[{loop_id}] CRITICAL: Failed to disable ramp generator. "
                    f"Aborting temperature ramp.")
                return None

        except Exception as e:
            log(f"[{loop_id}] CRITICAL: Failed to execute safety interlock. "
                f"Aborting ramp. Error: {e}")
            return None
    else:
        log(f"[{loop_id}] Se source detected → skipping ramp safety interlock "
            f"(will restore state at the end).")

    finish_time = None
    try:
        # STEP 1: Ensure controller is in AUTO mode
        mode = ctrl.get_mode(loop_id)
        if mode != "auto":
            ctrl.set_mode(loop_id, "auto")
            log(f"[{loop_id}] Switched to AUTO mode for temperature ramp.")

        current_temp = ctrl.read_value(loop_id, "temperature_pv")
        cz_config = addresses.get(loop_id, {}).get("critical_zone")

        # STEP 3: Plan the ramp segments (slowed inside the critical zone)
        ramp_segments = plan_ramp_segments(current_temp, target_temp,
                                           normal_rate_min, cz_config)

        if not ramp_segments:
            log(f"[{loop_id}] Already at target temperature.")
            return None

        total_duration_min = sum(seg.duration_min for seg in ramp_segments)

        finish_time = datetime.now() + timedelta(minutes=total_duration_min)
        log(f"[{loop_id}] Ramp plan: {len(ramp_segments)} segment(s). "
            f"Estimated duration: {total_duration_min:.1f} min.")
        log(f"[{loop_id}] Estimated completion time: {finish_time.strftime('%H:%M:%S')}.")

        # STEP 4: Enable ramp generator for the temperature ramp
        log(f"[{loop_id}] Enabling ramp generator for temperature ramp.")
        ctrl.set_ramp_status(loop_id, False)

        # ── Fire-and-return: write registers for the first segment, then
        #    immediately hand control back to the caller. If the ramp crosses
        #    a critical zone (multiple segments), a daemon thread watches the
        #    PV and switches rates at each segment boundary.
        first_seg = ramp_segments[0]
        log(f"[{loop_id}] Segment 1/{len(ramp_segments)}: "
            f"Ramping to {first_seg.target}°C at {first_seg.rate}°C/min.")
        ctrl.write_register(loop_id, "Ramp_temp", first_seg.rate)
        ctrl.write_value(loop_id, "temperature_setpoint", first_seg.target)

        if len(ramp_segments) > 1:
            n_total = len(ramp_segments)

            def _continue_segments(segs, prev_target):
                for idx, seg in enumerate(segs, start=2):
                    # Poll PV until the previous segment target is reached.
                    # A failed read never moves on to the next segment:
                    # keep waiting until the PV can be read again.
                    read_failed = False
                    while True:
                        if stop_event.is_set():
                            return
                        try:
                            pv = ctrl.read_value(loop_id, "temperature_pv")
                        except Exception as e:
                            if not read_failed:
                                read_failed = True
                                log(f"[{loop_id}] Cannot read PV ({e}): segment {idx}/{n_total} "
                                    f"held until communication is back.")
                        else:
                            if read_failed:
                                read_failed = False
                                log(f"[{loop_id}] PV readable again ({pv:.1f}°C): "
                                    f"waiting for {prev_target}°C before segment {idx}/{n_total}.")
                            if abs(pv - prev_target) <= SEGMENT_TOLERANCE:
                                break
                        time.sleep(SEGMENT_POLL_S)
                    if stop_event.is_set():
                        return
                    log(f"[{loop_id}] Segment {idx}/{n_total}: "
                        f"Ramping to {seg.target}°C at {seg.rate}°C/min.")
                    ctrl.write_register(loop_id, "Ramp_temp", seg.rate)
                    ctrl.write_value(loop_id, "temperature_setpoint", seg.target)
                    prev_target = seg.target

            threading.Thread(
                target=_continue_segments,
                args=(ramp_segments[1:], first_seg.target),
                daemon=True,
            ).start()

        log(f"[{loop_id}] Ramp to {target_temp}°C started "
            f"(est. {total_duration_min:.1f} min) — moving to next step.")
        return finish_time

    except Exception as e:
        log(f"[{loop_id}] Error during temperature ramp: {e}")
        return None
    finally:
        # FINALIZATION:
        #  - Normal loops: disable ramp generator (safety).
        #  - "script_keep_ramp_generator" loops: restore the state from before the ramp.
        if skip_ramp_safety:
            if initial_ramp_status is not None:
                log(f"[{loop_id}] Finalizing sequence: restoring ramp generator "
                    f"to '{initial_ramp_status}'.")
                ctrl.set_ramp_status(loop_id, initial_ramp_status == "on")
            else:
                log(f"[{loop_id}] Finalizing sequence: no initial ramp state → "
                    f"leaving ramp generator as-is.")
        else:
            log(f"[{loop_id}] Finalizing sequence: Disabling ramp generator.")
            ctrl.set_ramp_status(loop_id, False)


# ── Manual change from the General View: same rules as Compile ───────────────

class _KnownTemperature:
    """Stands for the controller during the check: answers with the last polled PV."""

    def __init__(self, temperature):
        self.temperature = temperature

    def read_value(self, loop_id, name):
        if name != "temperature_pv" or self.temperature is None:
            raise ValueError("unknown")
        return self.temperature


@dataclass
class ManualRampCheck:
    """What Compile says about one manual temperature change."""
    loop_id: str
    target: float
    rate: float | None            # None for a direct-setpoint loop
    direct: bool                  # the loop only gets its setpoint written
    errors: list[str] = field(default_factory=list)      # any error blocks the change
    warnings: list[str] = field(default_factory=list)    # e.g. critical zone crossed
    duration_min: float | None = None                    # estimated ramp time
    script_rate: float = 0.0      # rate of the equivalent script line

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def command(self) -> str:
        """The equivalent script line (written in the log when the change is applied)."""
        return f'temperature_ramp("{self.loop_id}", {self.target:g}, {self.script_rate:g})'

    def summary(self) -> str:
        """One line for the operator: what will happen if the change is applied."""
        if self.errors:
            return self.errors[0]
        if self.direct:
            text = f"Setpoint {self.target:g} °C written directly (no ramp rate on this loop)"
        else:
            text = f"Ramp to {self.target:g} °C at {self.rate:g} °C/min"
            if self.duration_min is not None:
                text += f" — about {format_minutes(self.duration_min)}"
        if self.warnings:
            text += " · ⚠ " + "; ".join(self.warnings)
        return text


def manual_limits_text(addresses: dict, loop_id: str) -> str:
    """Allowed range and rate of a loop, e.g. "Allowed 200 – 1200 °C · max 15 °C/min"."""
    info = addresses.get(loop_id, {})
    limits = info.get("limits", {})
    low, high, rate = limits.get("min_temp"), limits.get("max_temp"), limits.get("max_temp_rate")
    if high is None:
        return f"max_temp is not set for {loop_id} in {machine_config.CONFIG_NAME}"
    parts = [f"Allowed {low:g} – {high:g} °C" if low is not None else f"Allowed up to {high:g} °C"]
    if info.get("script_direct_setpoint", False):
        parts.append("setpoint written directly")
    elif rate is not None:
        parts.append(f"max {rate:g} °C/min")
    zone = info.get("critical_zone")
    if zone and not info.get("script_direct_setpoint", False):
        a, b = min(zone["range"]), max(zone["range"])
        parts.append(f"{zone['slow_rate']:g} °C/min max between {a:g} and {b:g} °C")
    return " · ".join(parts)


def check_manual_ramp(loop_id: str, target, rate, *, addresses: dict,
                      current_temp=None, now=None) -> ManualRampCheck:
    """
    Check a manual temperature change with the script compiler: the command
    temperature_ramp(loop_id, target, rate) is compiled against `addresses`
    (limits, critical zone) from `current_temp` (last measured temperature,
    None = unknown). `rate` is ignored for direct-setpoint loops.
    """
    info = addresses.get(loop_id, {})
    direct = bool(info.get("script_direct_setpoint", False))
    check = ManualRampCheck(loop_id, target, None if direct else rate, direct)

    if not isinstance(target, (int, float)) or isinstance(target, bool):
        check.errors.append("Enter a target temperature (°C)")
        return check
    if direct:
        # The rate is not used on these loops; compile with the highest allowed
        # one so that only the target is judged.
        script_rate = info.get("limits", {}).get("max_temp_rate") or 1.0
    elif not isinstance(rate, (int, float)) or isinstance(rate, bool):
        check.errors.append("Enter a ramp rate (°C/min)")
        return check
    else:
        script_rate = rate
    check.script_rate = script_rate

    report = compile_script(
        f"temperature_ramp({loop_id!r}, {target!r}, {script_rate!r})",
        addresses=addresses,
        controllers={loop_id: _KnownTemperature(current_temp)},
        now=now,
    )
    prefix = f"temperature_ramp {loop_id} "
    for message in report.messages:
        text = message.text
        if text.startswith(prefix):                 # "temperature_ramp Au target = ..." → "target = ..."
            text = text[len(prefix):]
        if message.level is Level.ERROR:
            check.errors.append(text[:1].upper() + text[1:])
        elif message.level is Level.WARNING:
            # "Au ramp 950 -> 1100 °C crosses ..." → "ramp 950 -> 1100 °C crosses ..."
            check.warnings.append(text.removeprefix(f"{loop_id} "))
    if report.timing is not None and not direct:
        ramp = report.timing.ramps.get(loop_id)
        if ramp is not None and ramp.end is not None:
            check.duration_min = (ramp.end - ramp.start).total_seconds() / 60
    return check
