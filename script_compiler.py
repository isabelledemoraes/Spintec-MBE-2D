"""
Script compiler: static analysis of a DSL script before it can be run.

compile_script() parses the script once, runs every function registered in
CHECKS and returns a CompileReport. A script may be run only when the report
has no ERROR.

Adding a check (ramp limits, critical zones, time estimate, ...):
    def check_something(ctx: CompileContext) -> list[CompileMessage]: ...
    CHECKS.append(check_something)
All limits and thresholds must be read from ctx.addresses, never hardcoded.
"""

import ast
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Iterator

from pressure_interlock import config_problem, gauge_name, interlock_for_line, is_bypassed
from ramp_planning import Segment, plan_ramp_segments, time_to_reach


class Level(Enum):
    ERROR = "ERROR"      # blocks Run
    WARNING = "WARNING"  # informs the user, does not block Run
    INFO = "INFO"        # informs the user, does not block Run


@dataclass
class CompileMessage:
    level: Level
    line: int | None     # 1-based script line; None = applies to the whole script
    text: str

    def format(self) -> str:
        where = f" line {self.line}" if self.line is not None else ""
        return f"{self.level.value}{where}: {self.text}"


@dataclass
class CompileContext:
    code: str
    tree: ast.Module
    addresses: dict
    controllers: dict = field(default_factory=dict)  # live hardware, for checks that need it
    summary: dict = field(default_factory=dict)      # label -> value, shown under the summary line
    timeline: list | None = None                     # cache of temperature_timeline(ctx)
    now: datetime | None = None                      # script start for the time estimate
    timing: "ScriptTiming | None" = None             # cache of time_timeline(ctx)


@dataclass
class CompileReport:
    code: str                     # exact text that was compiled
    messages: list[CompileMessage]
    summary: dict = field(default_factory=dict)
    compiled_at: datetime = field(default_factory=datetime.now)
    timing: "ScriptTiming | None" = None   # time estimate; only set when there is no error

    @property
    def errors(self) -> list[CompileMessage]:
        return [m for m in self.messages if m.level is Level.ERROR]

    @property
    def warnings(self) -> list[CompileMessage]:
        return [m for m in self.messages if m.level is Level.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary_line(self) -> str:
        n_err, n_warn = len(self.errors), len(self.warnings)
        status = "OK" if self.ok else "FAILED"
        return (f"Compilation {status}: {n_err} error{'s' if n_err != 1 else ''}, "
                f"{n_warn} warning{'s' if n_warn != 1 else ''}")

    def format_lines(self) -> list[str]:
        """Errors, warnings, infos, then the summary line, summary entries and time estimate."""
        lines = [m.format() for m in self.messages]
        lines.append(self.summary_line())
        lines.extend(f"{label}: {value}" for label, value in self.summary.items())
        if self.timing is not None:
            lines.extend(self.timing.format_lines())
        return lines


Check = Callable[[CompileContext], list[CompileMessage]]


def _error(line, text):
    return CompileMessage(Level.ERROR, line, text)


def iter_calls(tree: ast.Module) -> Iterator[tuple[int, str, ast.Call]]:
    """Yield (line, function_name, call_node) for each top-level command, in execution order."""
    for stmt in tree.body:
        if (isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)):
            yield stmt.lineno, stmt.value.func.id, stmt.value


# ── Checks ───────────────────────────────────────────────────────────────────

# Parameter names of the DSL functions defined in ScriptEditor.run_script().
DSL_PARAMS = {
    "power_ramp":             ("loop_id", "target_power", "rate"),
    "temperature_ramp":       ("loop_id", "target_temp", "normal_rate_min"),
    "hold":                   ("duration_min",),
    "wait_until":             ("datetime_str",),
    "open_shutter":           ("shutter_id",),
    "close_shutter":          ("shutter_id",),
    "wait_until_temperature": ("loop_id", "target_temp", "tolerance", "timeout"),
}


def check_commands(ctx: CompileContext) -> list[CompileMessage]:
    """
    Command names, argument counts and argument types/values.
    Checking logic taken unchanged from the former ScriptEditor._validate_script();
    only the output format changed (CompileMessage instead of "Error on line N: ..." strings).
    """
    addresses = ctx.addresses
    allowed_functions = {
        "power_ramp":            {"min_args": 3, "max_args": 3},
        "temperature_ramp":      {"min_args": 3, "max_args": 3},
        "hold":                  {"min_args": 1, "max_args": 1},
        "wait_until":            {"min_args": 1, "max_args": 1},
        "open_shutter":          {"min_args": 1, "max_args": 1},
        "close_shutter":         {"min_args": 1, "max_args": 1},
        "wait_until_temperature":{"min_args": 2, "max_args": 4},
    }
    errors = []

    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Call):
            func_name = getattr(node.func, 'id', 'N/A')

            if func_name not in allowed_functions:
                errors.append(_error(node.lineno, f"Function '{func_name}' is not allowed."))
                continue

            min_args = allowed_functions[func_name]["min_args"]
            max_args = allowed_functions[func_name]["max_args"]
            n_args   = len(node.args)
            if not (min_args <= n_args <= max_args):
                if min_args == max_args:
                    errors.append(_error(
                        node.lineno,
                        f"'{func_name}' expected {min_args} argument(s), but got {n_args}."
                    ))
                else:
                    errors.append(_error(
                        node.lineno,
                        f"'{func_name}' expected {min_args}–{max_args} argument(s), but got {n_args}."
                    ))
                continue

            try:
                if func_name in ["power_ramp", "temperature_ramp"]:
                    # Arg 1: loop_id (string, must exist in addresses AND be a loop with temperature_pv)
                    arg1 = node.args[0]
                    if not isinstance(arg1, ast.Constant) or not isinstance(arg1.value, str):
                        errors.append(_error(
                            node.lineno,
                            f"First argument of '{func_name}' must be a string "
                            f'(e.g., "Au").'
                        ))
                    elif arg1.value not in addresses or "temperature_pv" not in addresses[arg1.value]:
                        errors.append(_error(
                            node.lineno,
                            f"Loop ID '{arg1.value}' not found as a Eurotherm loop in addresses.py."
                        ))

                    # Args 2 & 3: must be numbers
                    for i in [1, 2]:
                        arg = node.args[i]
                        arg_num = i + 1  # Human-readable argument number (2nd, 3rd)
                        if not isinstance(arg, ast.Constant) or not isinstance(arg.value, (int, float)):
                            errors.append(_error(
                                node.lineno,
                                f"Argument {arg_num} of '{func_name}' must be a number."
                            ))

                if func_name == "hold":
                    # Arg 1: duration (number)
                    arg1 = node.args[0]
                    if not isinstance(arg1, ast.Constant) or not isinstance(arg1.value, (int, float)):
                        errors.append(_error(node.lineno, "Argument of 'hold' must be a number."))

                if func_name == "wait_until":
                    arg1 = node.args[0]
                    if not isinstance(arg1, ast.Constant) or not isinstance(arg1.value, str):
                        errors.append(_error(
                            node.lineno,
                            "Argument of 'wait_until' must be a string "
                            '(e.g., "2026-06-10 08:30").'
                        ))
                    else:
                        try:
                            datetime.strptime(arg1.value, "%Y-%m-%d %H:%M")
                        except ValueError:
                            errors.append(_error(
                                node.lineno,
                                f"'wait_until' date '{arg1.value}' is not in YYYY-MM-DD HH:MM format."
                            ))

                if func_name == "wait_until_temperature":
                    # Arg 1: loop_id (string)
                    arg1 = node.args[0]
                    if not isinstance(arg1, ast.Constant) or not isinstance(arg1.value, str):
                        errors.append(_error(
                            node.lineno,
                            "First argument of 'wait_until_temperature' "
                            'must be a string (e.g., "Au").'
                        ))
                    elif arg1.value not in addresses or "temperature_pv" not in addresses[arg1.value]:
                        errors.append(_error(
                            node.lineno, f"Loop ID '{arg1.value}' not found in addresses.py."
                        ))
                    # Arg 2: target_temp (number)
                    if len(node.args) >= 2:
                        arg2 = node.args[1]
                        if not isinstance(arg2, ast.Constant) or not isinstance(arg2.value, (int, float)):
                            errors.append(_error(
                                node.lineno,
                                "Second argument of 'wait_until_temperature' must be a number."
                            ))

                if func_name in ["open_shutter", "close_shutter"]:
                    # Arg 1: shutter ID (string, must exist and have shutter_line)
                    arg1 = node.args[0]
                    if not isinstance(arg1, ast.Constant) or not isinstance(arg1.value, str):
                        errors.append(_error(
                            node.lineno,
                            f"Argument of '{func_name}' must be a string "
                            f'(e.g., "eGun1").'
                        ))
                    else:
                        shutter_id = arg1.value
                        info = addresses.get(shutter_id)
                        if not info or "shutter_line" not in info:
                            errors.append(_error(
                                node.lineno,
                                f"Shutter ID '{shutter_id}' not found with 'shutter_line' in addresses.py."
                            ))
            except Exception:
                errors.append(_error(
                    node.lineno, f"Could not validate arguments for '{func_name}'."
                ))

    return errors


def check_not_empty(ctx: CompileContext) -> list[CompileMessage]:
    """A script with no command (empty or only comments) would do nothing."""
    if ctx.tree.body:
        return []
    return [CompileMessage(Level.WARNING, None,
                           "The script contains no commands (empty or only comments).")]


def check_statements(ctx: CompileContext) -> list[CompileMessage]:
    """
    Every top-level statement must be a single DSL command call.
    Replaces what the former Check dry-run caught at execution time
    (bare names such as `hold` without parentheses, undefined variables, ...),
    and also rejects assignments, imports, loops and other Python constructs.
    Calls themselves are validated by check_commands.
    """
    errors = []
    for stmt in ctx.tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Name):
            name = stmt.value.id
            if name in DSL_PARAMS:
                text = f"'{name}' is not called (missing parentheses and arguments?)."
            else:
                text = f"'{name}' is not a known command."
        elif isinstance(stmt, ast.Expr):
            text = "Expression is not a command."
        else:
            text = f"'{type(stmt).__name__}' statements are not allowed; only DSL commands are."
        errors.append(_error(stmt.lineno, text))
    return errors


def _is_number(node) -> bool:
    return (isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool))


def check_call_arguments(ctx: CompileContext) -> list[CompileMessage]:
    """
    Keyword and optional arguments, which check_commands does not look at
    (they previously failed only during the Check dry-run or at Run time).
    """
    errors = []
    for node in ast.walk(ctx.tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        func_name = node.func.id
        params = DSL_PARAMS.get(func_name)
        if params is None:
            continue  # unknown command, reported by check_commands

        if any(isinstance(a, ast.Starred) for a in node.args):
            errors.append(_error(node.lineno, f"'*' arguments are not allowed in '{func_name}'."))
            continue

        # Values actually passed to each parameter, positional then keyword.
        passed = dict(zip(params, node.args))
        for kw in node.keywords:
            if kw.arg is None:
                errors.append(_error(node.lineno, f"'**' arguments are not allowed in '{func_name}'."))
            elif kw.arg not in params:
                errors.append(_error(node.lineno, f"'{func_name}' has no argument named '{kw.arg}'."))
            elif kw.arg in passed:
                errors.append(_error(node.lineno, f"'{func_name}' got multiple values for '{kw.arg}'."))
            else:
                passed[kw.arg] = kw.value

        if func_name == "wait_until_temperature":
            tol = passed.get("tolerance")
            if tol is not None and not _is_number(tol):
                errors.append(_error(
                    node.lineno, "'tolerance' of 'wait_until_temperature' must be a number."))
            timeout = passed.get("timeout")
            if (timeout is not None and not _is_number(timeout)
                    and not (isinstance(timeout, ast.Constant) and timeout.value is None)):
                errors.append(_error(
                    node.lineno, "'timeout' of 'wait_until_temperature' must be a number or None."))
    return errors


# ── Hardware limits (#5) ─────────────────────────────────────────────────────
# Limits live in addresses[loop_id]["limits"]; a missing (or None) limit needed
# by the script is an ERROR, never a skipped check. Arguments that are not
# numbers / unknown loops are already reported by check_commands and skipped here.

def _bound_args(node: ast.Call) -> dict:
    """Parameter name -> argument node, positional then keyword (invalid ones ignored)."""
    params = DSL_PARAMS.get(node.func.id, ())
    passed = dict(zip(params, node.args))
    for kw in node.keywords:
        if kw.arg in params and kw.arg not in passed:
            passed[kw.arg] = kw.value
    return passed


def _number_arg(passed: dict, name: str):
    node = passed.get(name)
    return node.value if _is_number(node) else None


def _loop_arg(passed: dict, addresses: dict):
    node = passed.get("loop_id")
    if (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "temperature_pv" in addresses.get(node.value, {})):
        return node.value
    return None


def _required_limit(ctx: CompileContext, loop_id: str, key: str, line: int, errors: list):
    value = ctx.addresses[loop_id].get("limits", {}).get(key)
    if value is None:
        errors.append(_error(line, f"No '{key}' limit defined for {loop_id} in addresses.py"))
    return value


def check_ramp_rates(ctx: CompileContext) -> list[CompileMessage]:
    """
    temperature_ramp: 0 < rate <= max_temp_rate (°C/min).
    power_ramp: only on loops with power_ramp_allowed, rate >= min_power_rate (%/s).
    """
    errors = []
    for line, name, node in iter_calls(ctx.tree):
        if name not in ("temperature_ramp", "power_ramp"):
            continue
        passed = _bound_args(node)
        loop_id = _loop_arg(passed, ctx.addresses)
        if loop_id is None:
            continue

        if name == "temperature_ramp":
            max_rate = _required_limit(ctx, loop_id, "max_temp_rate", line, errors)
            rate = _number_arg(passed, "normal_rate_min")
            if rate is not None and rate <= 0:
                errors.append(_error(line, f"temperature_ramp {loop_id} = {rate:g} °C/min, "
                                           f"must be greater than 0 °C/min"))
            elif max_rate is not None and rate is not None and rate > max_rate:
                errors.append(_error(line, f"temperature_ramp {loop_id} = {rate:g} °C/min, "
                                           f"max allowed {max_rate:g} °C/min"))
        else:
            allowed = _required_limit(ctx, loop_id, "power_ramp_allowed", line, errors)
            if allowed is None:
                continue
            if not allowed:
                errors.append(_error(line, f"power_ramp is not allowed on {loop_id}"))
                continue
            min_rate = _required_limit(ctx, loop_id, "min_power_rate", line, errors)
            rate = _number_arg(passed, "rate")
            if min_rate is not None and rate is not None and rate < min_rate:
                errors.append(_error(line, f"power_ramp {loop_id} = {rate:g} %/s, "
                                           f"min allowed {min_rate:g} %/s"))
    return errors


def check_temperature_targets(ctx: CompileContext) -> list[CompileMessage]:
    """Targets of temperature_ramp and wait_until_temperature within [min_temp (optional), max_temp]."""
    errors = []
    for line, name, node in iter_calls(ctx.tree):
        if name not in ("temperature_ramp", "wait_until_temperature"):
            continue
        passed = _bound_args(node)
        loop_id = _loop_arg(passed, ctx.addresses)
        if loop_id is None:
            continue
        max_temp = _required_limit(ctx, loop_id, "max_temp", line, errors)
        min_temp = ctx.addresses[loop_id].get("limits", {}).get("min_temp")
        target = _number_arg(passed, "target_temp")
        if target is None:
            continue
        if max_temp is not None and target > max_temp:
            errors.append(_error(line, f"{name} {loop_id} target = {target:g} °C, "
                                       f"max allowed {max_temp:g} °C"))
        if min_temp is not None and target < min_temp:
            errors.append(_error(line, f"{name} {loop_id} target = {target:g} °C, "
                                       f"min allowed {min_temp:g} °C"))
    return errors


# ── Temperature tracking (#6, reused by the time estimate #7) ─────────────────

@dataclass
class TemperatureStep:
    line: int
    command: str                # "temperature_ramp" or "wait_until_temperature"
    loop_id: str
    start: float | None         # estimated temperature before the command; None = unknown
    target: float
    rate: float | None = None   # temperature_ramp only (°C/min)
    segments: list[Segment] = field(default_factory=list)  # empty if start unknown / no ramp


def _read_live(ctx: CompileContext, loop_id: str, name: str):
    ctrl = ctx.controllers.get(loop_id)
    if ctrl is None:
        return None
    try:
        return float(ctrl.read_value(loop_id, name))
    except Exception:
        return None


def _read_current_temp(ctx: CompileContext, loop_id: str):
    return _read_live(ctx, loop_id, "temperature_pv")


def temperature_timeline(ctx: CompileContext) -> list[TemperatureStep]:
    """
    Simulate the temperature of each loop command by command. A loop starts at its
    live temperature_pv (unknown without controller), then follows the targets of
    temperature_ramp / wait_until_temperature. power_ramp makes it unknown.
    Computed once per compilation and cached on ctx.
    """
    if ctx.timeline is not None:
        return ctx.timeline

    temps = {}   # loop_id -> estimated temperature (None = unknown)
    steps = []
    for line, name, node in iter_calls(ctx.tree):
        if name not in ("temperature_ramp", "wait_until_temperature", "power_ramp"):
            continue
        passed = _bound_args(node)
        loop_id = _loop_arg(passed, ctx.addresses)
        if loop_id is None:
            continue
        if loop_id not in temps:
            temps[loop_id] = _read_current_temp(ctx, loop_id)
        if name == "power_ramp":
            temps[loop_id] = None
            continue

        target = _number_arg(passed, "target_temp")
        if target is None:
            temps[loop_id] = None
            continue
        step = TemperatureStep(line, name, loop_id, temps[loop_id], target)
        if name == "temperature_ramp":
            step.rate = _number_arg(passed, "normal_rate_min")
            info = ctx.addresses[loop_id]
            # script_direct_setpoint loops only write the setpoint: no ramp segments.
            if (step.start is not None and step.rate is not None and step.rate > 0
                    and not info.get("script_direct_setpoint", False)):
                step.segments = plan_ramp_segments(step.start, target, step.rate,
                                                   info.get("critical_zone"))
        steps.append(step)
        temps[loop_id] = target

    ctx.timeline = steps
    return steps


def check_critical_zones(ctx: CompileContext) -> list[CompileMessage]:
    """WARNING for each temperature_ramp slowed down by the critical zone of its loop."""
    warnings = []
    for step in temperature_timeline(ctx):
        if step.command != "temperature_ramp" or step.rate is None or step.rate <= 0:
            continue
        info = ctx.addresses[step.loop_id]
        cz = info.get("critical_zone")
        if not cz or info.get("script_direct_setpoint", False):
            continue
        cz_start, cz_end = min(cz["range"]), max(cz["range"])
        zone = f"critical zone {cz_start:g}-{cz_end:g} °C"
        slowed = f"ramp slowed to {min(step.rate, cz['slow_rate']):g} °C/min in this zone"
        if step.start is None:
            warnings.append(CompileMessage(
                Level.WARNING, step.line,
                f"{step.loop_id} start temperature unknown; ramp to {step.target:g} °C "
                f"may cross {zone}, {slowed}"))
        elif any(seg.in_critical_zone for seg in step.segments):
            warnings.append(CompileMessage(
                Level.WARNING, step.line,
                f"{step.loop_id} ramp {round(step.start, 1):g} -> {step.target:g} °C "
                f"crosses {zone}, {slowed}"))
    return warnings


# ── Pressure interlock (#8) ──────────────────────────────────────────────────
# The pressure itself is checked at execution time (ShutterController), just
# before each opening; compile only says where, and rejects a bad configuration.

def check_pressure_interlock(ctx: CompileContext) -> list[CompileMessage]:
    """
    INFO for each open_shutter on an interlocked line; ERROR if its configuration
    is invalid; WARNING instead when the interlock is bypassed in addresses.py.
    """
    messages = []
    for line, name, node in iter_calls(ctx.tree):
        if name != "open_shutter":
            continue
        arg = _bound_args(node).get("shutter_id")
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            continue  # reported by check_commands
        line_name = ctx.addresses.get(arg.value, {}).get("shutter_line")
        config = interlock_for_line(ctx.addresses, line_name) if line_name else None
        if config is None:
            continue
        if is_bypassed(config):
            messages.append(CompileMessage(
                Level.WARNING, line,
                f"pressure interlock BYPASSED in addresses.py: {arg.value} opens without "
                f"checking {gauge_name(ctx.addresses)}"))
            continue
        problem = config_problem(ctx.addresses, config)
        if problem:
            messages.append(_error(line, f"{arg.value} pressure interlock: {problem}"))
        else:
            messages.append(CompileMessage(
                Level.INFO, line,
                f"{gauge_name(ctx.addresses)} pressure checked before opening {arg.value}: "
                f"required < {config['max_pressure_mbar']:.1e} mbar, else the script stops"))
    return messages


# ── Time estimation (#7) ─────────────────────────────────────────────────────
# Ramps run in the background, in parallel on different loops; the script clock
# only advances at hold, wait_until and wait_until_temperature. The clock starts
# at compile time. After a step of unknown duration, clock times are minimums.

CALCULATED = "calculated"
UNKNOWN = "unknown"


def format_minutes(minutes: float) -> str:
    if minutes < 1:
        return f"{round(minutes * 60)} s"
    h, m = divmod(round(minutes), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


@dataclass(frozen=True)
class Duration:
    """Time a step takes on the script clock, and where that value comes from."""
    minutes: float | None     # None = unknown
    source: str               # CALCULATED, UNKNOWN, or another source (#10)
    note: str = ""            # why it is unknown / how it was bounded

    def format(self) -> str:
        if self.minutes is None:
            text, details = "unknown", [self.note]
        else:
            text = format_minutes(self.minutes)
            details = [self.source if self.source != CALCULATED else "", self.note]
        details = ", ".join(d for d in details if d)
        return f"{text} ({details})" if details else text


def _calculated(minutes: float, note: str = "") -> Duration:
    return Duration(minutes, CALCULATED, note)


def _unknown(note: str) -> Duration:
    return Duration(None, UNKNOWN, note)


@dataclass
class TimedStep:
    line: int
    name: str                   # DSL command
    loop_id: str | None
    text: str                   # the command as written in the script
    duration: Duration          # time taken on the script clock
    clock_after: datetime | None = None
    clock_is_min: bool = False
    background: str = ""        # ramp started by this step, e.g. "Au ramp 30 min, ends ~22:30"


DurationSource = Callable[[CompileContext, TimedStep], "Duration | None"]

# Other duration sources (#10: user estimate, typical, ...), tried in order for a
# step whose calculated duration is unknown; the first Duration returned is used.
DURATION_SOURCES: list[DurationSource] = []


@dataclass
class _Ramp:
    """Last ramp started on a loop, placed on the script clock."""
    label: str                          # "ramp" or "power ramp"
    start: datetime
    start_is_min: bool
    end: datetime | None = None         # None = unknown
    segments: list[Segment] | None = None   # temperature profile; None = unknown
    final_temp: float | None = None
    power: float | None = None          # output power after a power_ramp
    note: str = ""                      # why the end is unknown
    done: bool = False                  # target reached during a wait_until_temperature


def _format_clock(dt: datetime, start: datetime, is_min: bool) -> str:
    rounded = dt + timedelta(seconds=30)
    text = ("≥ " if is_min else "~") + rounded.strftime("%H:%M")
    days = (rounded.date() - start.date()).days
    return f"{text} (+{days} d)" if days else text


@dataclass
class ScriptTiming:
    start: datetime
    steps: list[TimedStep]
    end: datetime
    end_is_min: bool
    ramps: dict                 # loop_id -> _Ramp, last ramp started on each loop

    def clock(self, dt: datetime, is_min: bool = False) -> str:
        return _format_clock(dt, self.start, is_min)

    def last_ramp(self):
        """(loop_id, _Ramp) of the ramp with the latest known end, or None."""
        known = [(lid, r) for lid, r in self.ramps.items() if r.end is not None]
        return max(known, key=lambda item: item[1].end) if known else None

    def format_lines(self) -> list[str]:
        lines = [f"Time estimate (start {self.start:%H:%M}, compile time):"]
        for s in self.steps:
            text = (f"line {s.line}: {s.text} — {s.duration.format()} "
                    f"→ {self.clock(s.clock_after, s.clock_is_min)}")
            lines.append(f"{text} · {s.background}" if s.background else text)
        total = (self.end - self.start).total_seconds() / 60
        lines.append(f"Total: {'≥ ' if self.end_is_min else ''}{format_minutes(total)}")

        end = f"Script ends {self.clock(self.end, self.end_is_min)}"
        last = self.last_ramp()
        if last is not None and last[1].end > self.end:
            end += (f" · last ramp ({last[0]}) finishes "
                    f"{self.clock(last[1].end, last[1].start_is_min)}")
        unknown = [lid for lid, r in self.ramps.items() if r.end is None and not r.done]
        if unknown:
            end += f" · ramp end unknown: {', '.join(unknown)}"
        lines.append(end)
        return lines


def _step_text(ctx: CompileContext, node: ast.Call) -> str:
    text = ast.get_source_segment(ctx.code, node) or node.func.id
    return " ".join(text.split())


def _datetime_arg(passed: dict):
    node = passed.get("datetime_str")
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            return datetime.strptime(node.value, "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return None


def _still_running(prev: _Ramp | None, clock: datetime) -> str:
    """Note if the previous ramp of the loop may not be finished at `clock`, else ""."""
    if prev is None or prev.done:
        return ""
    if prev.end is None:
        return f"previous {prev.label} may still be running"
    if prev.end > clock:
        return f"previous {prev.label} still running"
    return ""


def _describe_ramp(loop_id: str, ramp: _Ramp, start: datetime) -> str:
    if ramp.end is None:
        return f"{loop_id} {ramp.label} end unknown ({ramp.note})"
    minutes = (ramp.end - ramp.start).total_seconds() / 60
    return (f"{loop_id} {ramp.label} {format_minutes(minutes)}, "
            f"ends {_format_clock(ramp.end, start, ramp.start_is_min)}")


def _start_temperature_ramp(ctx, loop_id, tstep, prev, clock, clock_is_min) -> _Ramp:
    ramp = _Ramp("ramp", clock, clock_is_min, final_temp=tstep.target)
    running = _still_running(prev, clock)
    if ctx.addresses[loop_id].get("script_direct_setpoint", False):
        ramp.note = "setpoint written directly, no ramp rate"
    elif running:
        ramp.note = running
    elif tstep.start is None:
        ramp.note = ("temperature unknown after power_ramp"
                     if prev is not None and prev.label == "power ramp"
                     else "temperature unreadable")
    elif tstep.rate is None or tstep.rate <= 0:
        ramp.note = "invalid rate"
    else:
        ramp.segments = tstep.segments
        ramp.end = clock + timedelta(minutes=sum(s.duration_min for s in tstep.segments))
    return ramp


def _start_power_ramp(ctx, loop_id, passed, prev, clock, clock_is_min) -> _Ramp:
    target, rate = _number_arg(passed, "target_power"), _number_arg(passed, "rate")
    ramp = _Ramp("power ramp", clock, clock_is_min, power=target)
    running = _still_running(prev, clock)
    if running:
        ramp.note = running
    elif target is None or rate is None or rate <= 0:
        ramp.note = "invalid arguments"
    else:
        # Same estimate as the runtime: |target - current output| / rate (%/s).
        power = prev.power if prev is not None else _read_live(ctx, loop_id, "output_pv")
        if power is None:
            ramp.note = "output power unknown" if prev is not None else "output power unreadable"
        else:
            ramp.end = clock + timedelta(minutes=abs(target - power) / rate / 60)
    return ramp


def _wait_for_temperature(loop_id, passed, ramp, tstep, clock) -> tuple[Duration, bool]:
    """(duration, reached): reached is False when the wait ends on its timeout."""
    target = _number_arg(passed, "target_temp")
    if loop_id is None or target is None:
        return _unknown("invalid arguments"), False
    tolerance = _number_arg(passed, "tolerance")
    tolerance = 0.5 if tolerance is None else tolerance
    timeout = _number_arg(passed, "timeout")      # seconds, None = no timeout

    reached_at, why = None, ""
    if ramp is None or ramp.done:
        temp = tstep.start if tstep is not None else None    # steady temperature
        if temp is None:
            why = f"{loop_id} temperature unreadable"
        elif abs(temp - target) <= tolerance:
            reached_at = clock
        else:
            why = f"no ramp to {target:g} °C on {loop_id}"
    elif ramp.label == "power ramp":
        why = f"{loop_id} temperature unknown after power_ramp"
    elif ramp.segments is None:
        why = f"{loop_id} {ramp.note}"
    else:
        if ramp.segments:
            minutes = time_to_reach(ramp.segments, target, tolerance)
        else:
            minutes = 0.0 if abs(ramp.final_temp - target) <= tolerance else None
        if minutes is None:
            why = f"{loop_id} ramp never reaches {target:g} °C"
        else:
            reached_at = ramp.start + timedelta(minutes=minutes)

    wait = None if reached_at is None else max((reached_at - clock).total_seconds() / 60, 0)
    if timeout is not None and (wait is None or timeout / 60 < wait):
        return _calculated(max(timeout, 0) / 60, f"timeout; {why}" if why else "timeout"), False
    return (_unknown(why), True) if wait is None else (_calculated(wait), True)


def time_timeline(ctx: CompileContext) -> ScriptTiming:
    """
    Place every command on the script clock. temperature_ramp / power_ramp take
    0 s and run in the background (ramp end from plan_ramp_segments, via the #6
    temperature_timeline); hold, wait_until and wait_until_temperature advance
    the clock. A second ramp on a loop whose previous ramp may still be running
    has an unknown end. Computed once per compilation and cached on ctx.
    """
    if ctx.timing is not None:
        return ctx.timing

    temp_steps = {s.line: s for s in temperature_timeline(ctx)}
    start = ctx.now or datetime.now()
    clock, clock_is_min = start, False
    ramps: dict[str, _Ramp] = {}
    steps = []
    for line, name, node in iter_calls(ctx.tree):
        if name not in DSL_PARAMS:
            continue
        passed = _bound_args(node)
        loop_id = _loop_arg(passed, ctx.addresses)
        step = TimedStep(line, name, loop_id, _step_text(ctx, node), _calculated(0))

        if name == "temperature_ramp" and loop_id is not None and line in temp_steps:
            ramps[loop_id] = _start_temperature_ramp(ctx, loop_id, temp_steps[line],
                                                     ramps.get(loop_id), clock, clock_is_min)
            step.background = _describe_ramp(loop_id, ramps[loop_id], start)
        elif name == "power_ramp" and loop_id is not None:
            ramps[loop_id] = _start_power_ramp(ctx, loop_id, passed,
                                               ramps.get(loop_id), clock, clock_is_min)
            step.background = _describe_ramp(loop_id, ramps[loop_id], start)
        elif name == "hold":
            minutes = _number_arg(passed, "duration_min")
            step.duration = (_calculated(max(minutes, 0)) if minutes is not None
                             else _unknown("invalid duration"))
        elif name == "wait_until":
            target = _datetime_arg(passed)
            step.duration = (_calculated(max((target - clock).total_seconds() / 60, 0))
                             if target is not None else _unknown("invalid date"))
        elif name == "wait_until_temperature":
            ramp = ramps.get(loop_id)
            step.duration, reached = _wait_for_temperature(loop_id, passed, ramp,
                                                           temp_steps.get(line), clock)
            target = _number_arg(passed, "target_temp")
            if (reached and ramp is not None and ramp.final_temp is not None
                    and target is not None and target == ramp.final_temp):
                ramp.done = True    # ramp target reached: the ramp is over
        # open_shutter / close_shutter: 0 s

        if step.duration.minutes is None:
            for source in DURATION_SOURCES:
                duration = source(ctx, step)
                if duration is not None:
                    step.duration = duration
                    break
        if step.duration.minutes is None:
            clock_is_min = True
        else:
            clock += timedelta(minutes=step.duration.minutes)
        step.clock_after, step.clock_is_min = clock, clock_is_min
        steps.append(step)

    ctx.timing = ScriptTiming(start, steps, clock, clock_is_min, ramps)
    return ctx.timing


def check_time_estimate(ctx: CompileContext) -> list[CompileMessage]:
    """Computes the time estimate shown in the report; adds no message."""
    time_timeline(ctx)
    return []


# Registry: every check runs on each compilation. Append new checks here.
CHECKS: list[Check] = [
    check_not_empty,
    check_statements,
    check_commands,
    check_call_arguments,
    check_ramp_rates,
    check_temperature_targets,
    check_critical_zones,
    check_pressure_interlock,
    check_time_estimate,
]


def compile_script(code: str, addresses: dict | None = None,
                   controllers: dict | None = None,
                   now: datetime | None = None) -> CompileReport:
    """
    Run all CHECKS on `code` and return the report (errors, then warnings, then infos).
    `now` is the script start used by the time estimate (default: compile time).
    """
    if addresses is None:
        from addresses import addresses

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return CompileReport(code, [_error(e.lineno, f"Syntax Error: {e.msg}")])

    ctx = CompileContext(code, tree, addresses, controllers or {}, now=now)
    messages = []
    for check in CHECKS:
        try:
            messages.extend(check(ctx))
        except Exception as e:
            # A broken check must never let a script through.
            messages.append(_error(None, f"Internal failure in {check.__name__}: {e}"))

    level_order = {Level.ERROR: 0, Level.WARNING: 1, Level.INFO: 2}
    messages.sort(key=lambda m: (level_order[m.level],
                                 m.line if m.line is not None else 0))
    report = CompileReport(code, messages, ctx.summary)
    if report.ok:
        report.timing = ctx.timing
    return report
