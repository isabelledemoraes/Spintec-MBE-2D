# checkpoint_manager.py
# Written just before each script statement executes.
# If the app crashes, this file survives and lets the next launch resume.
import json
import os

_FILE = "checkpoint.json"


def save(script_name: str, script_text: str, stmt_index: int) -> None:
    """Overwrite the checkpoint with the current progress."""
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "script_name": script_name,
                "script_text": script_text,
                "stmt_index": stmt_index,
            },
            f,
            indent=2,
        )


def load():  # -> Optional[dict]  (dict | None syntax requires Python 3.10+)
    """Return the saved checkpoint dict, or None if there is none."""
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def clear() -> None:
    """Delete the checkpoint (call after a clean finish or user discard)."""
    try:
        os.remove(_FILE)
    except FileNotFoundError:
        pass
