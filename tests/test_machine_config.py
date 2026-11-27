"""
Machine configuration in YAML (machine_config.py). No Qt, files in a temp dir.
Run with: python -m unittest discover tests
"""
import copy
import os
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import machine_config as mc

LEGACY = textwrap.dedent('''
    SIMULATION = False
    MACHINE_NAME = "mbe1"
    SCRIPT_ARCHIVE_DIR = r"D:\\MBE\\run_archive"
    CELL_LIMITS = {"min_power_rate": 0.01, "max_temp_rate": 15.0, "power_ramp_allowed": False}
    addresses = {
        "Au": {
            "device": ("COM1", 1),
            "limits": {**CELL_LIMITS, "max_temp": 1300, "min_temp": None},
            "temperature_pv": (1, 1), "temperature_setpoint": (2, 1),
            "output_setpoint": (3, 1), "output_pv": (4, 1), "status": (273, 0),
            "Ramp_temp": (35, 1), "ramp_op_status": (87, 0), "Ramp_OP": (37, 2),
            "critical_zone": {"range": (1014, 1114), "slow_rate": 3.0},
            "thresholds": {"temp_warning": 300, "temp_critical": 400,
                           "power_warning": 60, "power_critical": 80},
            "shutter_line": "Dev1/port0/line2",
        },
        "Te-Tube": {
            "device": ("COM3", 7),
            "limits": {**CELL_LIMITS, "max_temp": 400},
            "temperature_pv": (1, 1), "temperature_setpoint": (2, 1),
            "output_setpoint": (3, 1), "output_pv": (4, 1), "status": (273, 0),
            "Ramp_temp": (35, 1), "ramp_op_status": (87, 0), "Ramp_OP": (37, 2),
            "script_direct_setpoint": True,
        },
        "Pressure": {"device": ("COM10", 300), "command": "DS IG2 \\r\\n", "unit": "mbar"},
        "Liquid_N2": {"shutter_line": "Dev1/port0/line7",
                      "pressure_interlock": {"max_pressure_mbar": 1.0e-8, "bypass": False}},
        "eGun1": {"shutter_line": "Dev1/port1/line0"},
    }
''')


def example():
    return mc.load(mc.EXAMPLE_PATH)


class ExampleFileTests(unittest.TestCase):
    def test_example_is_valid_and_starts_in_simulation(self):
        config = example()
        self.assertEqual(mc.validate(config), [])
        self.assertIs(config["machine"]["simulation"], True)
        self.assertFalse(mc.has_password(config))

    def test_runtime_dict_layout(self):
        addresses = mc.to_addresses(example())
        self.assertEqual(mc.loop_ids(addresses), ["Au", "CaF2", "Se1", "Se2", "Substrate", "SnSe"])
        au = addresses["Au"]
        self.assertEqual(au["device"], ("COM1", 1))
        self.assertEqual(au["baudrate"], 9600)
        self.assertEqual(au["temperature_pv"], (1, 1))
        self.assertEqual(au["Ramp_OP"], (37, 2))
        self.assertEqual(au["critical_zone"], {"range": (1014, 1114), "slow_rate": 3.0})
        self.assertEqual(au["limits"]["max_temp_rate"], 15.0)
        self.assertEqual(au["shutter_line"], "Dev1/port0/line2")
        self.assertNotIn("critical_zone", addresses["CaF2"])
        self.assertNotIn("shutter_line", addresses["Substrate"])
        self.assertIs(addresses["Se1"]["script_direct_setpoint"], True)
        self.assertNotIn("script_direct_setpoint", au)
        self.assertEqual(addresses["Pressure"]["device"], ("COM10", 300))
        self.assertEqual(addresses["Pressure"]["command"], "DS IG1 \r\n")
        self.assertEqual(addresses["Liquid_N2"]["pressure_interlock"],
                         {"max_pressure_mbar": 1.0e-8, "bypass": False})
        self.assertEqual(mc.global_shutters(addresses),
                         [("Liquid_N2", "Liquid N2"), ("eGun1", "e-gun 1"), ("eGun2", "e-gun 2")])

    def test_gauge_disabled_means_no_pressure_entry(self):
        config = example()
        config["pressure_gauge"]["enabled"] = False
        self.assertNotIn("Pressure", mc.to_addresses(config))


class LegacyConversionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.legacy = os.path.join(self.dir, "addresses.py")
        with open(self.legacy, "w", encoding="utf-8") as f:
            f.write(LEGACY)
        self.yaml = os.path.join(self.dir, "config", "machine.yaml")

    def test_every_legacy_value_is_kept(self):
        legacy = mc._load_legacy_module(self.legacy).addresses
        config = mc.migrate_legacy(self.legacy)
        self.assertEqual(mc.validate(config), [])
        self.assertEqual(config["machine"], {"name": "mbe1", "simulation": False,
                                             "script_archive_dir": r"D:\MBE\run_archive"})
        addresses = mc.to_addresses(config)
        self.assertEqual(list(addresses), list(legacy))          # same names, same order
        for name, entry in legacy.items():
            for key, value in entry.items():
                if key == "limits":      # a limit that was absent is now an explicit None
                    for limit, limit_value in value.items():
                        self.assertEqual(addresses[name]["limits"][limit], limit_value)
                    self.assertTrue(all(v is None for k, v in addresses[name]["limits"].items()
                                        if k not in value))
                else:
                    self.assertEqual(addresses[name][key], value, f"{name}.{key}")

    def test_first_start_converts_addresses_py_once(self):
        config, how = mc.load_or_create(self.yaml, self.legacy, mc.EXAMPLE_PATH)
        self.assertIn("converted from", how)
        self.assertTrue(os.path.exists(self.yaml))
        self.assertTrue(os.path.exists(self.legacy))             # the old file is left alone
        self.assertEqual(config["pressure_gauge"]["command"], "DS IG2 \r\n")
        os.remove(self.legacy)                                   # next start: YAML only
        again, how = mc.load_or_create(self.yaml, self.legacy, mc.EXAMPLE_PATH)
        self.assertIn("loaded from", how)
        self.assertEqual(again, config)

    def test_without_any_file_the_example_is_copied(self):
        config, how = mc.load_or_create(self.yaml, os.path.join(self.dir, "none.py"),
                                        mc.EXAMPLE_PATH)
        self.assertIn("from the example", how)
        self.assertEqual(config, example())
        self.assertIs(config["machine"]["simulation"], True)

    def test_implausible_gauge_baudrate_falls_back_to_300(self):
        addresses = {"Pressure": {"device": ("COM10", 1)}}
        self.assertEqual(mc.from_legacy(addresses)["pressure_gauge"]["baudrate"], 300)


class ValidationTests(unittest.TestCase):
    def errors(self, change):
        config = example()
        change(config)
        return mc.validate(mc.normalize(config))

    def assertOneError(self, change, contains):
        errors = self.errors(change)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn(contains, errors[0])

    def test_wrong_values_are_named(self):
        au = lambda c: c["eurotherms"]["Au"]
        cases = [
            (lambda c: au(c).update(slave=0), "Cell Au: Modbus slave must be an integer 1–247"),
            (lambda c: au(c).update(slave="1"), "Cell Au: Modbus slave"),
            (lambda c: au(c).update(port=""), "Cell Au: serial port is required"),
            (lambda c: au(c)["registers"].update(status=[273.5, 0]), "register 'status': address"),
            (lambda c: au(c)["registers"].pop("Ramp_OP"), "register 'Ramp_OP' is missing"),
            (lambda c: au(c)["limits"].update(max_temp_rate=0), "max_temp_rate must be a number > 0"),
            (lambda c: au(c)["limits"].update(max_temp=500, min_temp=600), "min_temp must be below"),
            (lambda c: au(c).update(critical_zone={"range": [1014, 1014], "slow_rate": 3}),
             "critical zone needs two different temperatures"),
            (lambda c: au(c).update(critical_zone={"range": [1014, 1114], "slow_rate": 0}),
             "critical zone slow rate"),
            (lambda c: c["shutters"]["Liquid_N2"]["pressure_interlock"].update(max_pressure_mbar=0),
             "Shutter Liquid_N2: pressure interlock limit"),
            (lambda c: c["shutters"]["eGun1"].update(line=""), "Shutter eGun1: NI-DAQ line"),
            (lambda c: c["pressure_gauge"].update(baudrate=0), "Pressure gauge: baudrate"),
            (lambda c: c["daq"].update(device=""), "NI-DAQ: device name"),
            (lambda c: c["safety"].update(max_temp_rate_cap=-1), "Safety: max_temp_rate_cap"),
            (lambda c: c["machine"].update(simulation="no"), "Machine: simulation"),
        ]
        for change, contains in cases:
            with self.subTest(contains=contains):
                self.assertOneError(change, contains)

    def test_names(self):
        def rename(old, new, section="eurotherms"):
            def change(config):
                config[section] = {(new if k == old else k): v
                                   for k, v in config[section].items()}
            return change
        self.assertOneError(rename("Au", "My_Cell"), "letters, digits and '-'")
        self.assertOneError(rename("Au", "Au 2"), "letters, digits and '-'")
        self.assertOneError(rename("Au", "se1"), "Cell Se1: name already used by cell se1")
        self.assertOneError(rename("eGun1", "Au", "shutters"), "name already used by cell Au")
        self.assertIn("Cell Pressure: this name is reserved", self.errors(rename("Au", "Pressure")))
        self.assertEqual(self.errors(rename("Au", "Te-Cracker")), [])

    def test_empty_limits_are_allowed(self):
        # A limit left empty is valid here: Compile and Set temperature refuse the command.
        self.assertEqual(self.errors(lambda c: c["eurotherms"]["Au"]["limits"].update(
            max_temp=None, max_temp_rate=None)), [])


class FileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "config", "machine.yaml")

    def test_save_round_trip_and_backup(self):
        config = example()
        mc.save(config, self.path)
        self.assertEqual(mc.load(self.path), config)
        self.assertFalse(os.path.exists(self.path + ".bak"))
        changed = copy.deepcopy(config)
        changed["eurotherms"]["Au"]["limits"]["max_temp"] = 1300
        mc.save(changed, self.path)
        self.assertEqual(mc.load(self.path)["eurotherms"]["Au"]["limits"]["max_temp"], 1300)
        self.assertIsNone(mc.load(self.path + ".bak")["eurotherms"]["Au"]["limits"]["max_temp"])
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("temperature_pv: [1, 1]", text)            # registers stay on one line
        self.assertIn('command: "DS IG1 \\r\\n"', text)

    def test_invalid_configuration_is_not_written(self):
        config = example()
        mc.save(config, self.path)
        config["eurotherms"]["Au"]["slave"] = 999
        with self.assertRaises(mc.ConfigError) as ctx:
            mc.save(config, self.path)
        self.assertIn("Cell Au: Modbus slave", str(ctx.exception))
        self.assertEqual(mc.load(self.path)["eurotherms"]["Au"]["slave"], 1)

    def test_unreadable_files(self):
        os.makedirs(os.path.dirname(self.path))
        for text, contains in (("eurotherms: [1, 2", "not valid YAML"),
                               ("- just\n- a list\n", "must contain a YAML mapping"),
                               ("eurotherms: 3\n", "'eurotherms' must be a mapping")):
            with self.subTest(text=text):
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write(text)
                with self.assertRaises(mc.ConfigError) as ctx:
                    mc.load(self.path)
                self.assertIn(contains, str(ctx.exception))

    def test_file_from_an_older_version_gets_defaults(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("machine: {name: mbe2}\n"
                    "eurotherms:\n  Ga: {port: COM4, slave: 2}\n")
        config = mc.load(self.path)
        self.assertEqual(config["machine"]["name"], "mbe2")
        self.assertIs(config["machine"]["simulation"], True)
        self.assertEqual(config["safety"]["max_temp_rate_cap"], 20.0)
        self.assertEqual(config["eurotherms"]["Ga"]["baudrate"], 9600)
        # Registers are never guessed: missing ones are reported.
        self.assertIn("Cell Ga: register 'temperature_pv' is missing", mc.validate(config))

    def test_changes_are_listed_without_the_password(self):
        old, new = example(), example()
        new["eurotherms"]["Au"]["limits"]["max_temp"] = 1300
        new["shutters"].pop("eGun2")
        mc.set_password(new, "secret")
        self.assertEqual(mc.describe_changes(old, new), [
            "eurotherms.Au.limits.max_temp: None -> 1300",
            "shutters.eGun2.label: removed (was 'e-gun 2')",
            "shutters.eGun2.line: removed (was 'Dev1/port1/line1')",
            "shutters.eGun2.pressure_interlock: removed (was None)",
        ])


class PasswordTests(unittest.TestCase):
    def test_only_a_salted_hash_is_stored(self):
        config = example()
        self.assertFalse(mc.check_password(config, ""))
        mc.set_password(config, "growth-2026")
        self.assertTrue(mc.has_password(config))
        self.assertTrue(mc.check_password(config, "growth-2026"))
        self.assertFalse(mc.check_password(config, "growth-2027"))
        self.assertFalse(mc.check_password(config, None))
        self.assertNotIn("growth-2026", mc.dump(config))
        other = example()
        mc.set_password(other, "growth-2026")                    # same password, other salt
        self.assertNotEqual(other["security"]["password"]["hash"],
                            config["security"]["password"]["hash"])

    def test_too_short(self):
        with self.assertRaises(mc.ConfigError):
            mc.set_password(example(), "abc")

    def test_password_survives_save_and_can_be_reset_by_hand(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "machine.yaml")
            config = example()
            mc.set_password(config, "secret")
            mc.save(config, path)
            loaded = mc.load(path)
            self.assertTrue(mc.check_password(loaded, "secret"))
            loaded["security"]["password"] = None                # documented reset
            mc.save(loaded, path)
            self.assertFalse(mc.has_password(mc.load(path)))


if __name__ == "__main__":
    unittest.main()
