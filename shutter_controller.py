import json
import os
import threading

STATE_FILE = "shutter_state.json"

try:
    import nidaqmx
    from nidaqmx.constants import LineGrouping
except Exception as _e:  # broad on purpose
    print(f"[Shutter] nidaqmx import failed: {_e}")
    nidaqmx = None
    LineGrouping = None

def initialize_daq_outputs(device: str = "Dev1") -> None:
    """
    Configure all USB-6501 ports as digital outputs and set all lines LOW.

    Must be called before any other DAQmx task is created, to prevent the
    card's default high-impedance input state from floating shutter control
    lines and causing unintended shutter openings on power cycle or reconnect.
    """
    if nidaqmx is None:
        print("[Shutter] nidaqmx not available — skipping DAQ output initialization.")
        return
    try:
        with nidaqmx.Task() as task:
            for port in range(3):  # port0, port1, port2
                task.do_channels.add_do_chan(
                    f"{device}/port{port}",
                    line_grouping=LineGrouping.CHAN_FOR_ALL_LINES,
                )
            task.write([0, 0, 0])  # All 24 lines LOW — shutters closed
        print(f"[Shutter] DAQ outputs initialized: all ports on {device} set LOW.")
    except Exception as e:
        print(f"[Shutter] WARNING: DAQ output initialization failed: {e}")


class ShutterController:
    """
    Single shared instance (created by MainWindow) used by both the GUI buttons
    and the script engine, so the General View always reflects the real state.
    """
    def __init__(self):
        # Guards self.state, the JSON file and DAQ writes (GUI + script threads).
        self._lock = threading.Lock()
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        return {}

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f)

    def set_shutter(self, loop_id, line_name, state: bool) -> bool:
        """Write the shutter line. Returns True on success; state is only updated on success."""
        with self._lock:
            if nidaqmx is None:
                print("[Shutter] NI-DAQ not available; simulated state only.")
                self.state[loop_id] = state
                self._save_state()
                return True

            try:
                with nidaqmx.Task() as task:
                    task.do_channels.add_do_chan(line_name, line_grouping=LineGrouping.CHAN_PER_LINE)
                    task.write(state)
                self.state[loop_id] = state
                self._save_state()
                print(f"[Shutter] {loop_id} → {line_name} = {'HIGH' if state else 'LOW'}")
                return True
            except Exception as e:
                print(f"[Shutter] ERROR writing to {line_name}: {e}")
                return False

    def get_state(self, loop_id) -> bool:
        with self._lock:
            return bool(self.state.get(loop_id, False))

    def get_last_saved(self, loop_id) -> bool:
        return self.get_state(loop_id)
