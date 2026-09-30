#!/usr/bin/env python
"""
watchdog.py — crash-restart wrapper for MBE2026.

Launch this instead of GUIv5.py:
    python watchdog.py

Behaviour:
  - Exit code 0  → user closed the app normally; watchdog exits too.
  - Any other code → app crashed; watchdog waits RESTART_DELAY seconds
    and relaunches.  If a script was running, the checkpoint written by
    checkpoint_manager.py lets the GUI report it on the next start
    (crash record only: the script is not resumed).
"""
import subprocess
import sys
import os
import time

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "GUIv5.py")
RESTART_DELAY = 3   # seconds to wait before restarting after a crash


def main():
    while True:
        print("[Watchdog] Launching GUIv5.py …")
        result = subprocess.run([sys.executable, SCRIPT])

        if result.returncode == 0:
            print("[Watchdog] Application closed normally. Exiting.")
            break

        print(
            f"[Watchdog] App exited with code {result.returncode} (crash). "
            f"Restarting in {RESTART_DELAY} s …"
        )
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    main()
