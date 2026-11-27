"""
"⚙ Configuration" tab: password, form editing, validation, save. Offscreen Qt,
configuration file in a temp dir (never the real config/machine.yaml).
Run with: python -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication

import machine_config as mc
from config_editor import ConfigTab, password_problem

APP = QApplication.instance() or QApplication([])
PASSWORD = "growth-2026"


class ConfigTabTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "config", "machine.yaml")
        mc.save(mc.load(mc.EXAMPLE_PATH), self.path)
        self.log = []
        self.restarts = []
        self.answers = []            # answers given to the confirmation questions
        self.questions = []
        self.warnings = []

    def tab(self, restart_ok=True):
        def restart():
            self.restarts.append(True)
            return restart_ok
        tab = ConfigTab(log=self.log.append, request_restart=restart, config_path=self.path)
        self.addCleanup(tab.deleteLater)
        return tab

    def opened(self):
        """Tab with the password created and the editor open; dialogs answered by the test."""
        tab = self.tab()
        self.assertTrue(tab.create_password(PASSWORD, PASSWORD))
        editor = tab.editor

        def ask(title, text):
            self.questions.append(text)
            return self.answers.pop(0) if self.answers else True
        editor._ask = ask
        editor._warn = lambda title, text: self.warnings.append(text)
        return tab, editor

    def on_disk(self):
        return mc.load(self.path)

    def loop(self, editor, name):
        return next(p for p in editor.loop_pages if p.current_name() == name)

    @staticmethod
    def edit(field, text):
        widget = getattr(field, "edit", field.widget)
        widget.setText(text)
        field.on_change()

    # ── Password ─────────────────────────────────────────────────────────

    def test_first_access_creates_the_password(self):
        tab = self.tab()
        self.assertFalse(tab.is_unlocked())
        self.assertFalse(tab.confirm_input.isHidden())
        self.assertEqual(tab.unlock_btn.text(), "Create password and open")
        self.assertFalse(tab.create_password("abc", "abc"))
        self.assertEqual(tab.lock_error.text(), "At least 4 characters.")
        self.assertFalse(tab.create_password(PASSWORD, "growth-2027"))
        self.assertEqual(tab.lock_error.text(), "The two passwords are different.")
        self.assertFalse(mc.has_password(self.on_disk()))
        self.assertTrue(tab.create_password(PASSWORD, PASSWORD))
        self.assertTrue(tab.is_unlocked())
        self.assertTrue(mc.check_password(self.on_disk(), PASSWORD))
        with open(self.path, encoding="utf-8") as f:
            self.assertNotIn(PASSWORD, f.read())
        self.assertIn("[Config] Configuration password created.", self.log)

    def test_password_is_required_afterwards(self):
        tab, _ = self.opened()
        tab.lock()
        self.assertFalse(tab.is_unlocked())
        self.assertTrue(tab.confirm_input.isHidden())
        self.assertEqual(tab.unlock_btn.text(), "Unlock")
        self.assertFalse(tab.unlock("wrong"))
        self.assertEqual(tab.lock_error.text(), "Wrong password.")
        self.assertFalse(tab.is_unlocked())
        self.assertFalse(tab.create_password("other-1", "other-1"))   # cannot be redefined
        self.assertTrue(mc.check_password(self.on_disk(), PASSWORD))
        tab.password_input.setText(PASSWORD)
        tab.unlock_btn.click()
        self.assertTrue(tab.is_unlocked())
        self.assertEqual(tab.password_input.text(), "")
        # A second tab (program restarted) asks for the same password.
        other = self.tab()
        self.assertFalse(other.is_unlocked())
        self.assertFalse(other.unlock(""))
        self.assertTrue(other.unlock(PASSWORD))

    def test_lock_keeps_unsaved_edits(self):
        tab, editor = self.opened()
        self.edit(self.loop(editor, "Au").max_temp, "1300")
        self.assertTrue(editor.dirty)
        tab.lock()
        self.assertIsNone(self.on_disk()["eurotherms"]["Au"]["limits"]["max_temp"])
        self.assertTrue(tab.unlock(PASSWORD))
        self.assertEqual(self.loop(tab.editor, "Au").max_temp.edit.text(), "1300")
        self.assertTrue(tab.editor.dirty)

    def test_change_password(self):
        _, editor = self.opened()
        self.assertFalse(editor.set_new_password("wrong", "new-secret"))
        self.assertIn("the current password is wrong", self.warnings[-1])
        self.assertTrue(editor.set_new_password(PASSWORD, "new-secret"))
        self.assertTrue(mc.check_password(self.on_disk(), "new-secret"))
        self.assertFalse(mc.check_password(self.on_disk(), PASSWORD))
        # A later Save must not bring the old password back.
        self.edit(self.loop(editor, "Au").max_temp, "1300")
        self.assertTrue(editor.save())
        self.assertTrue(mc.check_password(self.on_disk(), "new-secret"))

    def test_password_problem(self):
        self.assertEqual(password_problem("abcd", "abcd"), "")
        self.assertNotEqual(password_problem("abc", "abc"), "")
        self.assertNotEqual(password_problem("abcd", "abce"), "")

    # ── Editing ──────────────────────────────────────────────────────────

    def test_navigation_lists_every_entry(self):
        _, editor = self.opened()
        texts = [editor.nav.item(i).text() for i in range(editor.nav.count())]
        self.assertEqual(texts, [
            "GENERAL", "Machine", "Pressure gauge", "NI-DAQ",
            "CELLS (EUROTHERM)", "Au", "CaF2", "Se1", "Se2", "Substrate", "SnSe",
            "SHUTTERS", "Liquid_N2", "eGun1", "eGun2"])

    def test_untouched_forms_give_back_the_file(self):
        _, editor = self.opened()
        config, errors = editor.collect()
        self.assertEqual(errors, [])
        self.assertEqual(mc.describe_changes(self.on_disk(), config), [])

    def test_save_writes_the_file_and_logs_each_change(self):
        _, editor = self.opened()
        machine, gauge, _daq = editor.general_pages
        self.edit(machine.name, "mbe1")
        self.edit(machine.archive, r"D:\MBE\run_archive")
        self.edit(gauge.command, r"DS IG2 \r\n")
        au = self.loop(editor, "Au")
        self.edit(au.max_temp, "1300")
        self.edit(au.max_rate, "12,5")
        self.edit(au.registers["temperature_pv"][0], "5")
        self.edit(au.zone_from, "1000")
        self.assertEqual(editor.status_label.text(), "● Unsaved changes")
        self.assertTrue(editor.save())
        self.assertFalse(editor.dirty)
        self.assertIn("✓ Saved", editor.status_label.text())

        config = self.on_disk()
        self.assertEqual(config["machine"]["name"], "mbe1")
        self.assertEqual(config["pressure_gauge"]["command"], "DS IG2 \r\n")
        loop = config["eurotherms"]["Au"]
        self.assertEqual(loop["limits"]["max_temp"], 1300)
        self.assertEqual(loop["limits"]["max_temp_rate"], 12.5)
        self.assertEqual(loop["registers"]["temperature_pv"], [5, 1])
        self.assertEqual(loop["critical_zone"], {"range": [1000, 1114], "slow_rate": 3.0})
        self.assertEqual(loop["thresholds"]["temp_warning"], 300)     # not in the form: kept
        self.assertTrue(mc.check_password(config, PASSWORD))
        self.assertTrue(os.path.exists(self.path + ".bak"))
        self.assertEqual(self.restarts, [])

        log = "\n".join(self.log)
        self.assertIn("7 change(s), effective at the next start.", log)
        self.assertIn("[Config]   machine.name: None -> 'mbe1'", log)
        self.assertIn("[Config]   eurotherms.Au.limits.max_temp: None -> 1300", log)
        self.assertIn("[Config]   eurotherms.Au.registers.temperature_pv: [1, 1] -> [5, 1]", log)
        self.assertNotIn("password", log.split("7 change(s)")[1])

    def test_wrong_input_is_refused_and_nothing_is_written(self):
        _, editor = self.opened()
        before = self.on_disk()
        au = self.loop(editor, "Au")
        self.edit(au.slave, "abc")
        self.edit(au.max_temp, "hot")
        self.assertFalse(editor.save())
        self.assertEqual(self.on_disk(), before)
        self.assertIn("Cell Au: Modbus slave: 'abc' is not an integer", self.warnings[-1])
        self.assertIn("Cell Au: Max temperature: 'hot' is not a number", self.warnings[-1])
        self.assertIn("Not saved — 2 problem(s)", editor.status_label.text())
        # Valid types, invalid values: refused by the validation.
        self.edit(au.slave, "300")
        self.edit(au.max_temp, "1300")
        self.edit(au.min_temp, "1400")
        self.assertFalse(editor.save())
        self.assertIn("Cell Au: Modbus slave must be an integer 1–247", self.warnings[-1])
        self.assertIn("Cell Au: min_temp must be below max_temp", self.warnings[-1])
        self.assertEqual(self.on_disk(), before)

    def test_rename_keeps_the_position(self):
        _, editor = self.opened()
        caf2 = self.loop(editor, "CaF2")
        caf2.name.widget.setText("Ge")
        caf2.name.widget.textEdited.emit("Ge")
        self.assertEqual(editor.nav.item(6).text(), "Ge")
        self.assertTrue(editor.save())
        self.assertEqual(list(self.on_disk()["eurotherms"]),
                         ["Au", "Ge", "Se1", "Se2", "Substrate", "SnSe"])
        self.assertEqual(self.on_disk()["eurotherms"]["Ge"]["registers"]["temperature_pv"], [1025, 1])

    def test_names_are_checked(self):
        _, editor = self.opened()
        for name, problem in (("Se1", "this name is used twice"),
                              ("eGun1", "Shutter eGun1: name already used by cell eGun1"),
                              ("My_Cell", "letters, digits and '-'"),
                              ("", "a name is required")):
            with self.subTest(name=name):
                editor.reload()
                self.edit(self.loop(editor, "CaF2").name, name)
                self.assertFalse(editor.save())
                self.assertIn(problem, self.warnings[-1])

    def test_add_duplicate_move_delete(self):
        _, editor = self.opened()
        page = editor.add_loop()
        self.assertEqual(page.current_name(), "Cell")
        self.assertIs(editor.current_page(), page)
        self.edit(page.name, "Ga")
        self.edit(page.port, "COM4")
        self.edit(page.slave, "2")

        editor.select(self.loop(editor, "Au"))
        editor.duplicate_current()
        copy_page = editor.current_page()
        self.assertEqual(copy_page.current_name(), "Au-copy")
        self.assertEqual(copy_page.zone_from.edit.text(), "1014")

        editor.select(page)
        for _ in range(10):
            editor.move_current(-1)                      # stops at the top of its section
        editor.select(self.loop(editor, "Se2"))
        editor.delete_current()
        self.assertIn("Remove 'Se2'", self.questions[-1])

        shutter = editor.add_shutter()
        self.edit(shutter.name, "Valve_A")
        self.edit(shutter.label_field, "Main valve")
        self.edit(shutter.line, "Dev2/port0/line0")
        self.assertTrue(editor.save(), self.warnings)

        config = self.on_disk()
        self.assertEqual(list(config["eurotherms"]),
                         ["Ga", "Au", "CaF2", "Se1", "Substrate", "SnSe", "Au-copy"])
        self.assertEqual(config["eurotherms"]["Ga"]["port"], "COM4")
        self.assertEqual(config["eurotherms"]["Ga"]["registers"], mc.DEFAULT_REGISTERS)
        self.assertEqual(config["eurotherms"]["Au-copy"], config["eurotherms"]["Au"])
        self.assertEqual(config["shutters"]["Valve_A"],
                         {"label": "Main valve", "line": "Dev2/port0/line0",
                          "pressure_interlock": None})
        self.assertEqual(mc.validate(config), [])

    def test_delete_can_be_cancelled(self):
        _, editor = self.opened()
        editor.select(self.loop(editor, "Se2"))
        self.answers = [False]
        editor.delete_current()
        self.assertEqual(len(editor.loop_pages), 6)
        self.assertFalse(editor.dirty)

    def test_sensitive_changes_must_be_confirmed(self):
        _, editor = self.opened()
        machine = editor.general_pages[0]
        machine.simulation.widget.setChecked(False)
        ln2 = next(p for p in editor.shutter_pages if p.current_name() == "Liquid_N2")
        ln2.bypass.widget.setChecked(True)
        self.answers = [False]
        self.assertFalse(editor.save())
        self.assertIn("drives the REAL instruments", self.questions[-1])
        self.assertIn("Pressure interlock of Liquid_N2 is BYPASSED", self.questions[-1])
        self.assertIs(self.on_disk()["machine"]["simulation"], True)
        self.answers = [True]
        self.assertTrue(editor.save())
        config = self.on_disk()
        self.assertIs(config["machine"]["simulation"], False)
        self.assertIs(config["shutters"]["Liquid_N2"]["pressure_interlock"]["bypass"], True)
        # Removing an interlock is confirmed too.
        ln2.interlock_on.widget.setChecked(False)
        self.answers = [True]
        self.assertTrue(editor.save())
        self.assertIn("Pressure interlock of Liquid_N2 is REMOVED", self.questions[-1])
        self.assertIsNone(self.on_disk()["shutters"]["Liquid_N2"]["pressure_interlock"])

    def test_critical_zone_can_be_removed_and_added(self):
        _, editor = self.opened()
        self.loop(editor, "Au").zone_on.widget.setChecked(False)
        caf2 = self.loop(editor, "CaF2")
        self.assertFalse(caf2.zone_from.edit.isEnabled())
        caf2.zone_on.widget.setChecked(True)
        self.assertFalse(editor.save())                         # zone without values
        self.edit(caf2.zone_from, "1300")
        self.edit(caf2.zone_to, "1360")
        self.edit(caf2.zone_rate, "2")
        self.assertTrue(editor.save())
        config = self.on_disk()
        self.assertIsNone(config["eurotherms"]["Au"]["critical_zone"])
        self.assertEqual(config["eurotherms"]["CaF2"]["critical_zone"],
                         {"range": [1300, 1360], "slow_rate": 2})

    def test_revert(self):
        _, editor = self.opened()
        self.edit(self.loop(editor, "Au").max_temp, "1300")
        editor.revert()
        self.assertFalse(editor.dirty)
        self.assertEqual(self.loop(editor, "Au").max_temp.edit.text(), "")

    def test_save_and_restart(self):
        _, editor = self.opened()
        self.edit(editor.general_pages[0].name, "mbe1")
        self.assertTrue(editor.save(restart=True))
        self.assertEqual(self.restarts, [True])
        self.assertEqual(self.on_disk()["machine"]["name"], "mbe1")
        # Not saved because of an error: no restart.
        self.edit(self.loop(editor, "Au").slave, "0")
        self.assertFalse(editor.save(restart=True))
        self.assertEqual(self.restarts, [True])

    def test_saved_file_is_what_the_program_loads(self):
        _, editor = self.opened()
        self.edit(self.loop(editor, "Au").max_temp, "1300")
        self.edit(self.loop(editor, "Au").shutter, "")           # no shutter any more
        self.assertTrue(editor.save())
        addresses = mc.to_addresses(self.on_disk())
        self.assertEqual(addresses["Au"]["limits"]["max_temp"], 1300)
        self.assertNotIn("shutter_line", addresses["Au"])


if __name__ == "__main__":
    unittest.main()
