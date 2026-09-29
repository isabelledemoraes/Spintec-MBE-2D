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
from datetime import datetime
from enum import Enum
from typing import Callable, Iterator


class Level(Enum):
    ERROR = "ERROR"      # blocks Run
    WARNING = "WARNING"  # informs the user, does not block Run


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


@dataclass
class CompileReport:
    code: str                     # exact text that was compiled
    messages: list[CompileMessage]
    summary: dict = field(default_factory=dict)
    compiled_at: datetime = field(default_factory=datetime.now)

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
        """Errors, then warnings, then the summary line and any summary entries."""
        lines = [m.format() for m in self.messages]
        lines.append(self.summary_line())
        lines.extend(f"{label}: {value}" for label, value in self.summary.items())
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
    "power_ramp":             ("loop_id", "target_power", "duration_min"),
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


# Registry: every check runs on each compilation. Append new checks here.
CHECKS: list[Check] = [
    check_statements,
    check_commands,
    check_call_arguments,
]


def compile_script(code: str, addresses: dict | None = None,
                   controllers: dict | None = None) -> CompileReport:
    """Run all CHECKS on `code` and return the report (errors first, then warnings)."""
    if addresses is None:
        from addresses import addresses

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return CompileReport(code, [_error(e.lineno, f"Syntax Error: {e.msg}")])

    ctx = CompileContext(code, tree, addresses, controllers or {})
    messages = []
    for check in CHECKS:
        try:
            messages.extend(check(ctx))
        except Exception as e:
            # A broken check must never let a script through.
            messages.append(_error(None, f"Internal failure in {check.__name__}: {e}"))

    level_order = {Level.ERROR: 0, Level.WARNING: 1}
    messages.sort(key=lambda m: (level_order[m.level],
                                 m.line if m.line is not None else 0))
    return CompileReport(code, messages, ctx.summary)
