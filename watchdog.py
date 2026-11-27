#!/usr/bin/env python
"""
watchdog.py — crash-restart wrapper for MBE2026.

Launch this instead of GUIv5.py:
    python watchdog.py

Behaviour:
  - Exit code 0  → user closed the app normally; watchdog exits too.
  - Exit code 42 → restart asked by the app (configuration saved in the
    Configuration tab); relaunched at once, this is not a crash.
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
RESTART_EXIT_CODE = 42   # same value as GUIv5.RESTART_EXIT_CODE


def main():
    while True:
        print("[Watchdog] Launching GUIv5.py …")
        # MBE_WATCHDOG tells the app that a restart is done by exiting with code 42.
        result = subprocess.run([sys.executable, SCRIPT],
                                env={**os.environ, "MBE_WATCHDOG": "1"})

        if result.returncode == 0:
            print("[Watchdog] Application closed normally. Exiting.")
            break

        if result.returncode == RESTART_EXIT_CODE:
            print("[Watchdog] Restart requested by the application (new configuration).")
            continue

        print(
            f"[Watchdog] App exited with code {result.returncode} (crash). "
            f"Restarting in {RESTART_DELAY} s …"
        )
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    main()
