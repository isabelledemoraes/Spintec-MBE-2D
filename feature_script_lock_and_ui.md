# Feature: Script close prevention, completion blocking, and instrument lock UI

## Overview

Three related UI/runtime improvements to prevent accidental interference
with running scripts and improve operator awareness.

---

## Feature 1 — Prevent closing a running script window

**Current behavior:** The script window can be closed while the script is running.

**Desired behavior:** If a script is currently running, the close button is
disabled (or hidden). The window can only be closed once the script has fully
completed or has been explicitly stopped by the user.

**Implementation guidance:**
- Find the window close event handler for the script panel/widget.
- Intercept the close event and suppress it if the script state is `running`.
- No confirmation dialog needed — just silently block the close action.
- Re-enable close as soon as the script transitions to `stopped` or `completed`.

---

## Feature 2 — Block script window until all ramp segments physically complete

**Current behavior:** `temperature_ramp()` is fire-and-forget. The script
releases instrument locks immediately after starting the Eurotherm ramp
generator, then exits. The window can be closed even though the hardware
is still executing segments 2/3.

**Desired behavior:** Even after the instrument lock is released, the script
window must remain open and blocked until **all ramp segments have physically
completed on the hardware** — i.e. until the Eurotherm confirms the final
segment is done.

**Implementation guidance:**
- After `temperature_ramp()` fires and releases the lock, do NOT mark the
  script as `completed` yet.
- Instead, track the estimated completion time already computed during ramp
  planning (e.g. `"Estimated completion time: 09:55:35"` in the logs) and
  keep the script in a `waiting_for_hardware` state until that time is reached
  OR until the Eurotherm reports the ramp as finished (whichever is more reliable).
- The script should remain in `waiting_for_hardware` state for ALL instruments
  that were ramped — i.e. wait for the last one to finish.
- During `waiting_for_hardware`, the stop button should still work (allowing
  the user to manually override if needed), but the window close button must
  remain disabled (Feature 1 applies here too).
- Display a status message in the script window indicating remaining wait time,
  e.g. `"Waiting for hardware: Ge ramp completing at 09:52:37"`.

---

## Feature 3 — Lock instrument controls in the general tab during script use

**Current behavior:** Instrument controls in the general tab remain fully
interactive even when an instrument is being used by a running script.

**Desired behavior:** When an instrument is locked by a running script:
- Its controls in the general tab are **greyed out and non-interactive**.
- A **lock icon** is displayed next to the instrument name.
- A **tooltip** on the lock icon shows which script is currently using it,
  e.g. `"Locked by Script 1 (Ge_ramp-20260304.py)"`.

**Implementation guidance:**
- Find where instrument locks are acquired and released in the script runtime.
- On lock acquisition, emit a signal or update shared state that the general
  tab listens to, triggering the greyed-out + lock icon state.
- On lock release (or script completion), revert controls to normal.
- The lock state must update in real time — do not require a UI refresh.
- This must also apply during `waiting_for_hardware` (Feature 2): even though
  the instrument lock is released in the script runtime, the hardware is still
  executing a ramp, so controls should remain greyed out until the ramp
  physically completes.

---

## Constraints

- Do not refactor unrelated code.
- Do not change the `temperature_ramp()` fire-and-forget behavior itself —
  all changes should be in the script lifecycle and UI layers.
- All three features should be implemented together as they share the same
  script state machine (`running` → `waiting_for_hardware` → `completed`).

## Expected deliverable

For each feature:
1. Identify the exact files and functions to modify.
2. Implement the change.
3. Summarize what was changed and why in 2–3 sentences.
