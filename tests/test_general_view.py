"""
General View cards: large temperature, "Set temperature" with the script rules,
shutters taken from the configuration. Simulated controllers, offscreen Qt.
Run with: python -m unittest discover tests
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

import machine_config as addresses_module
import checkpoint_manager
import shutter_controller
import GUIv5
from data_updater_threaded import EurothermWorker, PressureWorker
from instrument_panel import CardGrid, InstrumentPanel, parse_number
from sim_controllers import SimEurothermController, SimPressureGaugeController

APP = QApplication.instance() or QApplication([])

LOOP = {"temperature_pv": (1, 1), "temperature_setpoint": (2, 1),
        "output_setpoint": (3, 1), "output_pv": (4, 1), "status": (273, 0),
        "Ramp_temp": (35, 1), "ramp_op_status": (87, 0), "Ramp_OP": (37, 2)}
LIMITS = {"max_temp_rate": 15.0, "power_ramp_allowed": False, "min_power_rate": 0.01}
ADDR = {
    "Au": {"device": ("COM1", 1), **LOOP,
           "limits": {**LIMITS, "max_temp": 1300, "min_temp": 200},
           "critical_zone": {"range": (1014, 1114), "slow_rate": 3.0},
           "shutter_line": "Dev1/port0/line2"},
    "Se1": {"device": ("COM1", 7), **LOOP, "limits": {**LIMITS, "max_temp": 300},
            "script_direct_setpoint": True, "shutter_line": "Dev1/port0/line3"},
    "Substrate": {"device": ("COM1", 3), **LOOP, "limits": {**LIMITS, "max_temp": 900}},
    "Pressure": {"device": ("COM10", 300), "command": "DS IG1 \r\n", "unit": "mbar"},
    "Valve-A": {"shutter_line": "Dev2/port0/line0", "label": "Main valve"},
    "Liquid_N2": {"shutter_line": "Dev2/port0/line7"},
}
DATA = {"T": 400.0, "P": 43.2, "SP_T": 900.0, "SP_P": 43.0, "MODE": "auto",
        "Ramp_T": 5.0, "Ramp_P": 0.1}


def wait_for(condition, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        APP.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


class Sim(SimEurothermController):
    """Simulated controller with a fixed PV that records the writes."""
    def __init__(self, loop_id, pv=400.0):
        super().__init__(loop_id)
        self.connect()
        self._state["temperature_pv"] = self._state["temperature_setpoint"] = pv
        self.writes = []

    def read_value(self, loop_id, name):
        self._check_answer()
        return self._state[name]

    def write_value(self, loop_id, name, value):
        self.writes.append((name, value))
        super().write_value(loop_id, name, value)

    def write_register(self, loop_id, reg_name, value):
        self.writes.append((reg_name, value))
        super().write_register(loop_id, reg_name, value)


class InstrumentPanelTests(unittest.TestCase):
    def panel(self, name="Au", ctrl="sim", pv=400.0):
        self.log = []
        self.ctrl = Sim(name, pv) if ctrl == "sim" else ctrl
        panel = InstrumentPanel(name, self.ctrl, ADDR[name].get("shutter_line"), None,
                                addresses=ADDR, log=self.log.append, use_shutter_hardware=False)
        self.addCleanup(panel.deleteLater)
        return panel

    def type(self, panel, target=None, rate=None):
        if target is not None:
            panel.setpoint_input.setText(target)
        if rate is not None:
            panel.rate_input.setText(rate)
        panel._on_input_edited("")

    def test_values_are_shown(self):
        panel = self.panel()
        self.assertEqual(panel.name_label.text(), "Au")
        self.assertIn(">--<", panel.temp_label.text())
        panel.update_data(DATA)
        self.assertIn(">400.0<", panel.temp_label.text())
        self.assertEqual(panel.setpoint_temp.text(), "900.0 °C")
        self.assertEqual(panel.ramp_temp_value.text(), "5.00 °C/min")
        self.assertEqual(panel.power_label.text(), "43.2 %")
        self.assertEqual(panel.power_bar.value(), 432)
        self.assertEqual(panel.mode_label.text(), "AUTO")
        self.assertEqual(panel.trend_label.text(), "▲ Heating")
        panel.update_data({**DATA, "T": 900.4})
        self.assertEqual(panel.trend_label.text(), "● At setpoint")
        panel.update_data({**DATA, "T": 950.0})
        self.assertEqual(panel.trend_label.text(), "▼ Cooling")
        panel.update_data({**DATA, "MODE": "manual"})
        self.assertEqual(panel.trend_label.text(), "Manual power")
        self.assertEqual(panel.mode_label.text(), "MANUAL")

    def test_rate_starts_from_the_controller_rate_once(self):
        panel = self.panel()
        panel.update_data(DATA)
        self.assertEqual(panel.rate_input.text(), "5")
        panel.rate_input.setText("")
        panel.update_data({**DATA, "Ramp_T": 8.0})
        self.assertEqual(panel.rate_input.text(), "")          # never filled again

    def test_limits_shown_and_set_disabled_until_a_valid_target(self):
        panel = self.panel()
        panel.update_data(DATA)
        self.assertFalse(panel.set_temp_btn.isEnabled())
        self.assertEqual(panel.hint_label.text(), "Allowed 200 – 1300 °C · max 15 °C/min · "
                                                  "3 °C/min max between 1014 and 1114 °C")
        self.type(panel, "900", "10")
        self.assertTrue(panel.set_temp_btn.isEnabled())
        self.assertEqual(panel.hint_label.text(), "Ramp to 900 °C at 10 °C/min — about 50 min")
        self.type(panel, "1301")
        self.assertFalse(panel.set_temp_btn.isEnabled())
        self.assertEqual(panel.hint_label.text(), "Target = 1301 °C, max allowed 1300 °C")
        self.type(panel, "900", "20")
        self.assertFalse(panel.set_temp_btn.isEnabled())
        self.assertEqual(panel.hint_label.text(), "= 20 °C/min, max allowed 15 °C/min")
        self.type(panel, "abc", "10")
        self.assertFalse(panel.set_temp_btn.isEnabled())
        self.assertEqual(panel.hint_label.text(), "Enter a target temperature (°C)")
        self.type(panel, "1200", "10")                           # crosses the critical zone
        self.assertTrue(panel.set_temp_btn.isEnabled())
        self.assertIn("crosses critical zone 1014-1114 °C", panel.hint_label.text())

    def test_refused_change_writes_nothing(self):
        panel = self.panel()
        panel.update_data(DATA)
        self.type(panel, "1301", "10")
        panel.set_temperature()                                  # Enter in the field
        APP.processEvents()
        self.assertEqual(self.ctrl.writes, [])
        self.assertEqual(self.log, [])

    def test_set_runs_the_script_ramp_sequence(self):
        panel = self.panel()
        panel.update_data(DATA)
        self.ctrl._state.update(mode="manual", ramp_status="on")
        self.type(panel, "900", "7,5")                           # decimal comma accepted
        panel.set_temp_btn.click()
        wait_for(lambda: not panel._applying)
        self.assertEqual(self.ctrl.writes, [("Ramp_temp", 7.5), ("temperature_setpoint", 900.0)])
        self.assertEqual(self.ctrl.get_mode("Au"), "auto")
        self.assertEqual(self.ctrl.get_ramp_status("Au"), "off")
        self.assertFalse(panel.follow_up_pending())              # one segment: nothing left to write
        self.assertEqual(panel.setpoint_input.text(), "")        # target not left in the field
        self.assertTrue(panel.hint_label.text().startswith("✓ Ramp to 900 °C at 7.5 °C/min"))
        self.assertIn('[Au] Set temperature (General View): temperature_ramp("Au", 900, 7.5)',
                      self.log[0])
        self.assertIn("[Au] Ramp generator successfully verified as OFF.", self.log)

    def test_direct_setpoint_loop_has_no_rate(self):
        panel = self.panel("Se1", pv=100.0)
        self.assertTrue(panel.rate_input.isHidden())
        panel.update_data({**DATA, "T": 100.0, "SP_T": 100.0})
        self.type(panel, "180")
        self.assertTrue(panel.set_temp_btn.isEnabled())
        panel.set_temperature()
        wait_for(lambda: not panel._applying)
        self.assertEqual(self.ctrl.writes, [("temperature_setpoint", 180.0)])
        self.assertTrue(panel.hint_label.text().startswith("✓ Setpoint 180 °C written directly"))

    def test_locked_by_a_script(self):
        panel = self.panel()
        panel.update_data(DATA)
        self.type(panel, "900", "10")
        panel.set_locked("Growth 12")
        self.assertFalse(panel.setpoint_input.isEnabled())
        self.assertFalse(panel.rate_input.isEnabled())
        self.assertFalse(panel.set_temp_btn.isEnabled())
        self.assertEqual(panel.lock_label.text(), "🔒 Growth 12")
        self.assertEqual(panel.hint_label.text(), "Controlled by script 'Growth 12'.")
        panel.set_temperature()
        APP.processEvents()
        self.assertEqual(self.ctrl.writes, [])
        panel.set_locked(None)
        self.assertTrue(panel.set_temp_btn.isEnabled())
        self.assertTrue(panel.lock_label.isHidden())

    def test_script_lock_stops_the_manual_ramp_follow_up(self):
        panel = self.panel(pv=914.0)
        panel.update_data({**DATA, "T": 914.0})
        self.type(panel, "1200", "10")                           # 3 segments
        panel.set_temperature()
        wait_for(lambda: not panel._applying)
        self.assertEqual(self.ctrl.writes, [("Ramp_temp", 10.0), ("temperature_setpoint", 1014)])
        stop = panel._ramp_stop
        self.assertFalse(stop.is_set())
        self.assertTrue(panel.follow_up_pending())
        panel.set_locked("Growth 12")
        self.assertTrue(stop.is_set())
        self.assertFalse(panel.follow_up_pending())
        self.assertIn("[Au] Manual ramp follow-up stopped: loop taken by script 'Growth 12'.",
                      self.log)

    def test_new_manual_ramp_replaces_the_previous_one(self):
        panel = self.panel(pv=914.0)
        panel.update_data({**DATA, "T": 914.0})
        self.type(panel, "1200", "10")
        panel.set_temperature()
        wait_for(lambda: not panel._applying)
        first = panel._ramp_stop
        self.type(panel, "950", "10")
        panel.set_temperature()
        wait_for(lambda: not panel._applying)
        self.assertTrue(first.is_set())                          # old follow-up thread ended
        self.assertFalse(panel._ramp_stop.is_set())
        self.assertEqual(self.ctrl.writes[-1], ("temperature_setpoint", 950.0))

    def test_lost_instrument_never_shows_a_live_looking_value(self):
        from connection_monitor import ConnectionMonitor
        panel = self.panel()
        panel.update_data(DATA)
        monitor = ConnectionMonitor(["Au"])
        monitor.report_success("Au")
        monitor.mark_lost("Au", "test")
        panel.set_connection(monitor.snapshot()["Au"])
        self.assertIn(">400.0<", panel.temp_label.text())        # last value, greyed
        self.assertIn("#8E9AAB", panel.temp_label.text())
        self.assertTrue(panel.trend_label.isHidden())
        self.type(panel, "1200", "10")                           # plan: start unknown
        self.assertIn("start temperature unknown", panel.hint_label.text())
        monitor.report_success("Au")
        panel.set_connection(monitor.snapshot()["Au"])
        panel.update_data(DATA)
        self.assertNotIn("#8E9AAB", panel.temp_label.text())
        self.assertIn("about", panel.hint_label.text())

    def test_no_controller(self):
        panel = self.panel(ctrl=None)
        self.assertFalse(panel.setpoint_input.isEnabled())
        self.assertFalse(panel.set_temp_btn.isEnabled())
        self.assertIn("No controller", panel.hint_label.text())

    def test_shutter_button_only_when_the_loop_has_a_shutter(self):
        self.assertFalse(self.panel("Au").shutter_btn.isHidden())
        self.assertTrue(self.panel("Substrate").shutter_btn.isHidden())

    def test_parse_number(self):
        self.assertEqual(parse_number(" 2,5 "), 2.5)
        self.assertEqual(parse_number("1100"), 1100.0)
        self.assertIsNone(parse_number(""))
        self.assertIsNone(parse_number("12a"))


class CardGridTests(unittest.TestCase):
    def test_columns_follow_the_width(self):
        grid = CardGrid(min_card_width=330, max_columns=4)
        self.assertEqual([grid.columns_for(w) for w in (300, 700, 1040, 1400, 3000)],
                         [1, 2, 3, 4, 4])


class MainWindowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old_cwd = os.getcwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, old_cwd)
        self.sims = {}
        for patcher in (
            mock.patch.dict(addresses_module.addresses, ADDR, clear=True),
            mock.patch.object(GUIv5, "SIMULATION", True),
            mock.patch.object(GUIv5, "make_controller", self.make_controller),
            mock.patch.object(GUIv5, "SimPressureGaugeController",
                              SimPressureGaugeController, create=True),
            mock.patch.object(shutter_controller, "nidaqmx", None),
            mock.patch.object(shutter_controller, "STATE_FILE",
                              os.path.join(tmp.name, "shutter_state.json")),
            mock.patch.object(checkpoint_manager, "_FILE",
                              os.path.join(tmp.name, "checkpoint.json")),
            mock.patch.object(EurothermWorker, "interval_s", 0.02),
            mock.patch.object(PressureWorker, "interval_s", 0.02),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_controller(self, loop_id):
        self.sims[loop_id] = Sim(loop_id)
        return self.sims[loop_id]

    def open_window(self):
        window = GUIv5.MainWindow()
        self.addCleanup(window.close)
        return window

    def test_cards_and_shutters_come_from_the_configuration(self):
        w = self.open_window()
        cards = [p for p in w.instrument_panels if isinstance(p, InstrumentPanel)]
        self.assertEqual([p.name for p in cards], ["Au", "Se1", "Substrate"])
        # Shutters that belong to no loop: one button each, labelled from the configuration.
        self.assertEqual(list(w.global_shutter_buttons), ["Valve-A", "Liquid_N2"])
        self.assertEqual(w.global_shutter_buttons["Valve-A"][0].text(), "Main valve: CLOSED")
        self.assertEqual(w.global_shutter_buttons["Liquid_N2"][0].text(), "Liquid_N2: CLOSED")
        w.global_shutter_buttons["Valve-A"][0].click()
        self.assertTrue(w.shutter_ctrl.get_state("Valve-A"))
        self.assertEqual(w.global_shutter_buttons["Valve-A"][0].text(), "Main valve: OPEN")

    def test_live_values_and_pressure_reach_the_general_view(self):
        w = self.open_window()
        au = next(p for p in w.instrument_panels if p.name == "Au")
        wait_for(lambda: ">400.0<" in au.temp_label.text())
        wait_for(lambda: "1.50e-09" in w._pressure_chip.text())
        self.assertIn("mbar", w._pressure_chip.text())

    def test_set_temperature_from_the_general_view(self):
        w = self.open_window()
        au = next(p for p in w.instrument_panels if p.name == "Au")
        wait_for(lambda: ">400.0<" in au.temp_label.text())
        au.setpoint_input.setText("650")
        au.rate_input.setText("12")
        au._on_input_edited("")
        au.set_temp_btn.click()
        wait_for(lambda: ("temperature_setpoint", 650.0) in self.sims["Au"].writes)
        wait_for(lambda: "[Au] Ramp to 650.0°C started" in w.log_output.toPlainText())
        self.assertIn('temperature_ramp("Au", 650, 12)', w.log_output.toPlainText())

    def test_configuration_tab_is_locked_again_when_left(self):
        path = os.path.join(os.getcwd(), "machine.yaml")
        addresses_module.save(addresses_module.load(addresses_module.EXAMPLE_PATH), path)
        with mock.patch.object(addresses_module, "CONFIG_PATH", path):
            w = self.open_window()
        w.tabs.setCurrentIndex(w._config_tab_index)
        self.assertFalse(w.config_tab.is_unlocked())
        self.assertTrue(w.config_tab.create_password("growth-2026", "growth-2026"))
        self.assertTrue(w.config_tab.is_unlocked())
        w.tabs.setCurrentIndex(0)
        self.assertFalse(w.config_tab.is_unlocked())
        self.assertIn("[Config] Configuration locked.", w.log_output.toPlainText())

    def test_restart_is_refused_while_a_script_runs(self):
        w = self.open_window()
        with mock.patch.object(w, "running_scripts", return_value=["Growth 12"]), \
                mock.patch.object(GUIv5.QMessageBox, "warning") as warning:
            self.assertFalse(w.request_restart())
        self.assertIn("A script is running (Growth 12)", warning.call_args.args[2])
        self.assertFalse(w.restart_requested)
        self.assertEqual(w.running_scripts(), [])
        au = next(p for p in w.instrument_panels if p.name == "Au")
        with mock.patch.object(au, "follow_up_pending", return_value=True), \
                mock.patch.object(GUIv5.QMessageBox, "warning") as warning:
            self.assertFalse(w.request_restart())
        self.assertIn("crossing its critical zone (Au)", warning.call_args.args[2])
        self.assertTrue(w.request_restart())
        self.assertTrue(w.restart_requested)
        self.assertFalse(w.isVisible())

    def test_no_gauge_configured(self):
        addr = {k: v for k, v in ADDR.items() if k != "Pressure"}
        with mock.patch.dict(addresses_module.addresses, addr, clear=True):
            w = self.open_window()
            self.assertEqual([p.name for p in w.instrument_panels], ["Au", "Se1", "Substrate"])
            self.assertTrue(w._pressure_chip.isHidden())
            self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())],
                             ["General View", "Scripts", "⚙ Configuration"])


if __name__ == "__main__":
    unittest.main()
