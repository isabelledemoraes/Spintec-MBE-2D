# script_archive.py
# Run archive (issue #9): when Run is pressed, the exact script text and its
# compile report are saved BEFORE execution starts, for traceability:
#   <SCRIPT_ARCHIVE_DIR>/YYYY-MM-DD_HH-MM-SS_<script>.txt
#   <SCRIPT_ARCHIVE_DIR>/YYYY-MM-DD_HH-MM-SS_<script>_report.txt
# Any failure raises ArchiveError and the script must not start.
# Nothing is ever overwritten. No Qt here, so it can be tested alone.
import itertools
import os
import re
import subprocess
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Characters Windows refuses in file names (plus whitespace, for readability).
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\s]+')
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_NAME_LEN = 80
DEFAULT_NAME = "untitled"


class ArchiveError(Exception):
    """The run could not be archived; the message says why (shown to the user)."""


def safe_filename(name) -> str:
    """Script name usable in a Windows file name; DEFAULT_NAME if nothing is left."""
    safe = _FORBIDDEN.sub("_", str(name or "")).strip(" ._")
    safe = safe[:MAX_NAME_LEN].rstrip(" ._")
    if not safe:
        return DEFAULT_NAME
    if safe.split(".")[0].upper() in _RESERVED:
        safe = "_" + safe
    return safe


def _git(*args) -> str:
    return subprocess.run(
        ["git", *args], cwd=PROJECT_DIR, capture_output=True, text=True,
        timeout=5, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()


def detect_version() -> str:
    """Short git commit hash, '-dirty' if tracked files are modified, else 'unknown'."""
    try:
        commit = _git("rev-parse", "--short", "HEAD")
        dirty = _git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if not commit:
        return "unknown"
    return f"{commit}-dirty" if dirty else commit


# Read once at import: this is the code actually running, even if files change later.
SOFTWARE_VERSION = detect_version()


def archive_settings(config) -> tuple[str, str]:
    """
    (machine name, absolute archive folder) from the addresses.py module `config`.
    A relative folder is relative to the project folder, not the working directory.
    """
    machine = getattr(config, "MACHINE_NAME", None)
    if not isinstance(machine, str) or not machine.strip():
        raise ArchiveError("MACHINE_NAME is not set in addresses.py "
                           "(see config/addresses.py.example).")
    folder = getattr(config, "SCRIPT_ARCHIVE_DIR", None)
    if not isinstance(folder, str) or not folder.strip():
        raise ArchiveError("SCRIPT_ARCHIVE_DIR is not set in addresses.py "
                           "(see config/addresses.py.example).")
    folder = os.path.join(PROJECT_DIR, os.path.expanduser(folder.strip()))
    return machine.strip(), os.path.normpath(folder)


def format_report(report, *, machine, run_at, version, script_name, script_file) -> str:
    """Report file text: header, then the compile report as shown in the GUI."""
    lines = [
        "MBE Control - script run report",
        f"Machine:          {machine}",
        f"Run started:      {run_at:%Y-%m-%d %H:%M:%S}",
        f"Software version: {version}",
        f"Script name:      {script_name}",
        f"Script file:      {script_file}",
    ]
    lines.append("-" * 60)
    lines.append(f"Compiled at {report.compiled_at:%Y-%m-%d %H:%M:%S}")
    lines.extend(report.format_lines())
    return "\n".join(lines) + "\n"


def _write_new(path: str, text: str):
    """Write a NEW file (FileExistsError if it exists); no partial file left on error."""
    f = open(path, "x", encoding="utf-8", newline="")   # newline="": text written as is
    try:
        with f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _remove_quietly(path)
        raise


def _remove_quietly(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def save_run(code: str, report, script_name: str, config, *, now: datetime | None = None,
             version: str = SOFTWARE_VERSION) -> tuple[str, str]:
    """
    Save the executed `code` and its compile `report` with one timestamp.
    Returns (script_path, report_path); raises ArchiveError on any failure.
    If the names are taken (same script, same second), both get a _2, _3... suffix.
    """
    machine, folder = archive_settings(config)
    now = now or datetime.now()
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise ArchiveError(f"cannot create archive folder {folder}: {e}") from e

    stem = f"{now:%Y-%m-%d_%H-%M-%S}_{safe_filename(script_name)}"
    for n in itertools.count(1):
        base = stem if n == 1 else f"{stem}_{n}"
        script_path = os.path.join(folder, base + ".txt")
        report_path = os.path.join(folder, base + "_report.txt")
        if os.path.exists(report_path):
            continue
        try:
            _write_new(script_path, code)
        except FileExistsError:
            continue
        except OSError as e:
            raise ArchiveError(f"cannot write {script_path}: {e}") from e
        break

    text = format_report(report, machine=machine, run_at=now, version=version,
                         script_name=script_name, script_file=os.path.basename(script_path))
    try:
        _write_new(report_path, text)
    except OSError as e:
        _remove_quietly(script_path)   # no script without its report
        raise ArchiveError(f"cannot write {report_path}: {e}") from e
    return script_path, report_path
