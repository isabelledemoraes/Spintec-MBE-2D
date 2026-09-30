# checkpoint_manager.py
# Written just before each script statement executes.
# If the app crashes, this file survives: it is a crash record only, used on the
# next launch to tell the user which script was running (no resume).
import json
import os
from datetime import datetime

_FILE = "checkpoint.json"


def save(script_name: str, stmt_index: int, stmt_count: int) -> None:
    """Overwrite the checkpoint with the current progress."""
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "script_name": script_name,
                "stmt_index": stmt_index,
                "stmt_count": stmt_count,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
            },
            f,
            indent=2,
        )


def load():  # -> Optional[dict]  (dict | None syntax requires Python 3.10+)
    """Return the saved checkpoint dict, or None if there is none."""
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            cp = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    return cp if isinstance(cp, dict) else None


def clear() -> None:
    """Delete the checkpoint (call after a clean finish or once the crash is reported)."""
    try:
        os.remove(_FILE)
    except FileNotFoundError:
        pass


def crash_message(cp: dict, now: datetime | None = None) -> str:
    """
    One-line message for a checkpoint left by a crash. `saved_at` is when the
    statement STARTED (the crash time itself is unknown). Missing or bad fields
    (old-format checkpoint) are left out of the message.
    """
    now = now or datetime.now()
    name = cp.get("script_name") or "an unnamed script"

    details = []
    index, count = cp.get("stmt_index"), cp.get("stmt_count")
    if isinstance(index, int) and index >= 0:
        stmt = f"statement {index + 1}"
        if isinstance(count, int) and count > index:
            stmt += f" of {count}"
        details.append(stmt)
    try:
        started = datetime.fromisoformat(cp["saved_at"])
    except (KeyError, TypeError, ValueError):
        started = None
    if started is not None:
        fmt = "%H:%M" if started.date() == now.date() else "%Y-%m-%d %H:%M"
        details.append(f"started {started.strftime(fmt)}")

    msg = f"⚠ Previous session ended unexpectedly while running {name}"
    if details:
        msg += f" ({', '.join(details)})"
    return msg + "."
