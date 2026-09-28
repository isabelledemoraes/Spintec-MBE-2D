# base_instrument.py
from abc import ABC, abstractmethod

class BaseInstrument(ABC):
    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def read_value(self, name: str):
        """Read a named property like 'temperature_pv' or 'output_pv'"""
        pass
