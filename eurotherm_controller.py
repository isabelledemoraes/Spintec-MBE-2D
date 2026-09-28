# eurotherm_controller.py
import minimalmodbus
import logging
import time
from threading import Lock
from base_instrument import BaseInstrument
from addresses import addresses

# Global lock manager per port
port_locks = {}

class EurothermController(BaseInstrument):
    def __init__(self, loop_id: str):
        self.loop_id = loop_id
        self.port, self.slave_address = addresses[loop_id]["device"]
        self.baudrate = 9600
        self.instrument = None
        self.connected = False

    def connect(self):
        global port_locks
        try:
            if self.port not in port_locks:
                port_locks[self.port] = Lock()
            self.port_lock = port_locks[self.port]

            self.instrument = minimalmodbus.Instrument(self.port, self.slave_address)
            self.instrument.serial.baudrate = self.baudrate
            self.instrument.serial.timeout = 0.5
            self.instrument.serial.write_timeout = 0.2
            self.instrument.clear_buffers_before_each_transaction = True
            self.instrument.close_port_after_each_call = False
            self.instrument.handle_local_echo = False
            self.connected = True
            logging.info(f"Connected to Eurotherm on {self.port} (slave {self.slave_address}).")
        except Exception as e:
            logging.error(f"Connection failed: {e}")
            raise

    def disconnect(self):
        self.instrument = None
        self.connected = False
        logging.info("Disconnected from Eurotherm.")

    def safe_read_register(self, reg_addr, decimals=0, retries=3):
        for _ in range(retries):
            try:
                with self.port_lock:
                    return self.instrument.read_register(reg_addr, decimals, signed=True)
            except Exception as e:
                logging.warning(f"[{self.loop_id}] Read retry due to: {e}")
                time.sleep(0.2)
        raise ConnectionError(f"[{self.loop_id}] Read failed after retries.")

    def safe_write_register(self, reg_addr, value, decimals=0, retries=3):
        for _ in range(retries):
            try:
                with self.port_lock:
                    self.instrument.write_register(reg_addr, value, number_of_decimals=decimals)
                return
            except Exception as e:
                logging.warning(f"[{self.loop_id}] Write retry due to: {e}")
                time.sleep(0.2)
        raise ConnectionError(f"[{self.loop_id}] Write failed after retries.")

    def read_value(self, loop_id: str, name: str):
        if not self.connected:
            raise ConnectionError("Not connected.")
        try:
            reg_info = addresses[loop_id][name]
            reg_addr, decimals = reg_info if isinstance(reg_info, tuple) else (reg_info, 0)
            return self.safe_read_register(reg_addr, decimals)
        except Exception as e:
            logging.error(f"[{loop_id}] Error reading '{name}': {e}")
            raise

    def write_value(self, loop_id: str, name: str, value):
        if not self.connected:
            raise ConnectionError("Not connected.")
        try:
            reg_info = addresses[loop_id][name]
            reg_addr, decimals = reg_info if isinstance(reg_info, tuple) else (reg_info, 0)
            self.safe_write_register(reg_addr, value, decimals)
            logging.info(f"[{loop_id}] Wrote {value} to '{name}'")
        except Exception as e:
            logging.error(f"[{loop_id}] Error writing '{name}': {e}")
            raise

    def write_register(self, loop_id, register_name, value):
        reg_info = addresses.get(loop_id, {}).get(register_name)
        if reg_info is None:
            raise KeyError(f"[{loop_id}] Register '{register_name}' not found.")
        reg_addr, decimals = reg_info if isinstance(reg_info, tuple) else (reg_info, 1)
        self.safe_write_register(reg_addr, value, decimals)

    def get_ramp_status(self, loop_id: str) -> str:
        try:
            reg_info = addresses[loop_id]["ramp_op_status"]
            reg_addr = reg_info[0] if isinstance(reg_info, tuple) else reg_info
            return "on" if self.safe_read_register(reg_addr) else "off"
        except Exception as e:
            logging.error(f"[{loop_id}] Error reading ramp status: {e}")
            raise

    def set_ramp_status(self, loop_id: str, enable: bool):
        try:
            reg_info = addresses[loop_id]["ramp_op_status"]
            reg_addr = reg_info[0] if isinstance(reg_info, tuple) else reg_info
            self.safe_write_register(reg_addr, 1 if enable else 0)
            logging.info(f"[{loop_id}] Set ramp status to {'on' if enable else 'off'}.")
        except Exception as e:
            logging.error(f"[{loop_id}] Error setting ramp status: {e}")
            raise

    def get_mode(self, loop_id: str) -> str:
        try:
            reg_info = addresses[loop_id]["status"]
            reg_addr = reg_info[0] if isinstance(reg_info, tuple) else reg_info
            val = self.safe_read_register(reg_addr)
            return "manual" if (val & 0b1) else "auto"
        except Exception as e:
            logging.error(f"[{loop_id}] Error reading control mode: {e}")
            raise

    def set_mode(self, loop_id: str, mode: str):
        assert mode in ("manual", "auto"), "Mode must be 'manual' or 'auto'"
        try:
            reg_info = addresses[loop_id]["status"]
            reg_addr = reg_info[0] if isinstance(reg_info, tuple) else reg_info
            current_val = self.safe_read_register(reg_addr)
            new_val = (current_val | 0b1) if mode == "manual" else (current_val & ~0b1)
            self.safe_write_register(reg_addr, new_val)
            logging.info(f"[{loop_id}] Set mode to {mode}.")
        except Exception as e:
            logging.error(f"[{loop_id}] Error setting control mode: {e}")
            raise
