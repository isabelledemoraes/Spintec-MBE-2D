# pressure_gauge_controller.py
from base_instrument import BaseInstrument
import serial
import threading
from addresses import addresses
import time
import logging

# Shared lock per port
port_locks = {}

class PressureGaugeController(BaseInstrument):
    def __init__(self, loop_id: str):
        self.loop_id = loop_id
        self.port, _ = addresses[loop_id]["device"]
        self.baudrate = 300
        # Gauge channel command, e.g. "DS IG1 \r\n"; optional in addresses.py
        self.command = addresses[loop_id].get("command", "DS IG1 \r\n").encode("ascii")
        self.ser = None
        self.connected = False

    def connect(self):
        global port_locks
        try:
            if self.port not in port_locks:
                port_locks[self.port] = threading.Lock()
            self.port_lock = port_locks[self.port]

            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.SEVENBITS,
                stopbits=serial.STOPBITS_TWO,
                timeout=1
            )
            self.connected = True
            logging.info(f"[PressureGauge] Connected on {self.port}")
        except Exception as e:
            logging.error(f"[PressureGauge] Connection failed: {e}")
            raise

    def disconnect(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.connected = False
        logging.info(f"[PressureGauge] Disconnected from {self.port}")

    def read_value(self, name: str):
        if not self.connected:
            raise ConnectionError("PressureGauge is not connected")
        if name != "pressure":
            raise ValueError(f"Unsupported value: {name}")

        try:
            with self.port_lock:
                self.ser.write(self.command)
                raw = self.ser.read(50)
        except Exception as e:
            raise IOError(f"Serial read error: {e}")

        try:
            text = raw.decode(errors='ignore').strip()
            for token in text.split():
                try:
                    return float(token)
                except ValueError:
                    continue
            raise ValueError(f"Unparsable response: {text}")
        except Exception as e:
            raise ValueError(f"Failed to decode pressure: {e}")
