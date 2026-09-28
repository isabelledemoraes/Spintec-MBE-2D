import json
import os

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
    def __init__(self):
        self.state = self._load_state()

    def _load_state(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        return {}

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f)

    def set_shutter(self, loop_id, line_name, state: bool):

        if nidaqmx is None:
            print("[Shutter] NI-DAQ not available; simulated state only.")
            self.state[loop_id] = state
            self._save_state()
            return

        try:
            with nidaqmx.Task() as task:
                task.do_channels.add_do_chan(line_name, line_grouping=LineGrouping.CHAN_PER_LINE)
                task.write(state)
            self.state[loop_id] = state
            self._save_state()
            print(f"[Shutter] {loop_id} → {line_name} = {'HIGH' if state else 'LOW'}")
        except Exception as e:
            print(f"[Shutter] ERROR writing to {line_name}: {e}")

    def get_last_saved(self, loop_id) -> bool:
        return self.state.get(loop_id, False)
