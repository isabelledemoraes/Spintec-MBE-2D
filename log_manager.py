import os
import csv
from datetime import datetime, timedelta

class LogManager:
    def __init__(self):
        self.mode = "Standby"  # or "Growth"
        self.growth_filename = None
        self.last_log_time = datetime.min
        self.current_log_file = None
        self.log_dir = "logs"
        os.makedirs(self.log_dir, exist_ok=True)

    def set_mode(self, mode: str, filename: str = None):
        print(f"[DEBUG] Setting mode: {mode}, filename: {filename}")
        self.mode = mode
        if mode == "Standby":
            self.growth_filename = None
            self._rotate_daily_file()
        elif mode == "Growth":
            if not filename or not filename.strip():
                print("[Logging] Growth mode requires a filename.")
                return
            fn = filename.strip()
            if not fn.lower().endswith(".csv"):
                fn += ".csv"
            self.growth_filename = fn
            self.current_log_file = os.path.join(self.log_dir, fn)

    def _rotate_daily_file(self):
        today_str = datetime.now().strftime("%Y%m%d")
        self.current_log_file = os.path.join(self.log_dir, f"standby_log_{today_str}.csv")

    def _should_log(self):
        now = datetime.now()
        interval = timedelta(seconds=5 if self.mode == "Growth" else 60)
        return now - self.last_log_time >= interval

    def log(self, values_dict: dict):
        # print(f"[DEBUG] Logging mode: {self.mode}, file: {self.current_log_file}")

        if not values_dict:
            return  # Do nothing if there's no data to log

        if not self._should_log():
            return

        self.last_log_time = datetime.now()

        if self.mode == "Standby":
            self._rotate_daily_file()

        if not self.current_log_file:
            # No file chosen yet (e.g. Growth mode without filename)
            return

        # Deterministic header/row order
        keys = sorted(values_dict.keys())
        write_header = not os.path.exists(self.current_log_file)

        with open(self.current_log_file, 'a', newline='') as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["timestamp"] + keys)
            writer.writerow(
                [self.last_log_time.strftime("%Y-%m-%d %H:%M:%S")]
                + [values_dict.get(k, "") for k in keys]
            )


