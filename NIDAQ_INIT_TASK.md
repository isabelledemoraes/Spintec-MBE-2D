# Task: Add NI DAQ USB-6501 Output Initialization at Startup

## Context

The project controls pneumatic shutters via a **NI DAQ USB-6501** digital I/O card using the `nidaqmx` Python library.

**Problem:** The USB-6501 defaults to high-impedance input mode on every power cycle or USB reconnect. This causes all shutter control lines to float, which triggers the shutters to open unintentionally when switching from local to remote control.

**Fix:** All three ports (port0, port1, port2 — 24 lines total) must be explicitly configured as **digital outputs** and set **LOW** before any other DAQ communication occurs.

---

## What to implement

### 1. Create an initialization function

Add the following function to the appropriate module (e.g. `daq.py`, `hardware.py`, or wherever DAQ setup lives):

```python
import nidaqmx
from nidaqmx.constants import LineGrouping

def initialize_daq_outputs(device: str = "Dev1") -> None:
    """
    Configure all USB-6501 ports as digital outputs and set all lines LOW.

    This must be called before any other DAQmx task is created, to prevent
    the card's default high-impedance input state from floating shutter
    control lines and causing unintended shutter openings.

    Args:
        device: NI-DAQmx device name as shown in NI-MAX (default: "Dev1").
    """
    with nidaqmx.Task() as task:
        for port in range(3):  # port0, port1, port2
            task.do_channels.add_do_chan(
                f"{device}/port{port}",
                line_grouping=LineGrouping.CHAN_FOR_ALL_LINES
            )
        task.write([0, 0, 0])  # All 24 lines LOW — shutters closed
```

### 2. Call the function at application startup

Locate the application entry point (e.g. `main.py`, `app.py`, or the `if __name__ == "__main__"` block) and call `initialize_daq_outputs()` as the **first DAQ-related call**, before any task creation, loop start, or GUI initialization that could interact with the DAQ card.

Example placement:

```python
if __name__ == "__main__":
    initialize_daq_outputs(device="Dev1")  # <-- must be first
    # ... rest of startup (GUI, main loop, etc.)
```

If the device name is already defined as a constant or config variable in the codebase, use that instead of hardcoding `"Dev1"`.

---

## Constraints

- The `nidaqmx` package must already be installed (`pip install nidaqmx`). Do not add it if it is already present in `requirements.txt` or `pyproject.toml`.
- Do **not** restructure the existing DAQ task logic — only add the initialization call.
- If a DAQmx task for the shutters is already opened at startup elsewhere, `initialize_daq_outputs()` must be inserted **before** that task is created, not after.
- The function should be placed near other hardware/DAQ setup code, not in a utility or math module.
- If the device name `"Dev1"` is already defined somewhere as a constant (e.g. `DAQ_DEVICE = "Dev1"`), pass that constant to the function rather than duplicating the string.

---

## Acceptance criteria

- [ ] `initialize_daq_outputs()` exists and is importable.
- [ ] It is called before any other DAQmx task is opened at startup.
- [ ] All 3 ports are configured as output and written LOW.
- [ ] The app starts without requiring any manual NI-MAX configuration.
- [ ] No existing shutter control logic is broken.
