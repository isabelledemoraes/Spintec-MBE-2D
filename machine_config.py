# machine_config.py
"""
Machine configuration — single source of truth, stored in YAML.

    config/machine.yaml          this machine (NOT in Git: ports, device names)
    config/machine.example.yaml  commented template (in Git)

Everything that differs between two MBE machines lives in that file: cell
names, COM ports, Modbus slaves and registers, script limits, critical zones,
shutter lines, pressure gauge, NI-DAQ device. The code itself is generic.

At import the file is loaded once and exposed as:

    addresses            dict used by the whole program (same layout as the
                         former addresses.py, see to_addresses)
    SIMULATION, MACHINE_NAME, SCRIPT_ARCHIVE_DIR, DAQ_DEVICE, DAQ_PORTS,
    MAX_TEMP_RATE_CAP    scalar settings
    config               the YAML content (normalized dict)

First start on a machine that still has the old addresses.py: it is converted
to config/machine.yaml automatically (addresses.py itself is left untouched).
With neither file, the example is copied (simulation mode).

The file is edited from the GUI (⚙ Configuration tab, password protected) or
by hand. A change takes effect at the next start. No Qt here.
"""
import copy
import hashlib
import hmac
import importlib.util
import math
import os
import re
import secrets
import shutil
from datetime import datetime

import yaml

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("MBE_CONFIG") or os.path.join(PROJECT_DIR, "config", "machine.yaml")
EXAMPLE_PATH = os.path.join(PROJECT_DIR, "config", "machine.example.yaml")
LEGACY_PATH = os.path.join(PROJECT_DIR, "addresses.py")
CONFIG_NAME = "machine.yaml"      # name used in messages shown to the user

PRESSURE_KEY = "Pressure"         # fixed name of the gauge (CSV column, interlock, monitor)
RESERVED_NAMES = (PRESSURE_KEY, "NI-DAQ", "timestamp")

# Eurotherm registers: name -> what it is (shown in the configuration editor).
REGISTERS = {
    "temperature_pv":       "Measured temperature (PV)",
    "temperature_setpoint": "Temperature setpoint",
    "output_setpoint":      "Output power setpoint (manual mode)",
    "output_pv":            "Output power (measured)",
    "status":               "Status word (bit 0 = manual mode)",
    "Ramp_temp":            "Temperature ramp rate (°C/min)",
    "ramp_op_status":       "Output ramp generator on/off",
    "Ramp_OP":              "Output power ramp rate (%/s)",
}
DEFAULT_REGISTERS = {
    "temperature_pv": [1, 1], "temperature_setpoint": [2, 1],
    "output_setpoint": [3, 1], "output_pv": [4, 1], "status": [273, 0],
    "Ramp_temp": [35, 1], "ramp_op_status": [87, 0], "Ramp_OP": [37, 2],
}
LIMIT_KEYS = ("max_temp_rate", "power_ramp_allowed", "min_power_rate", "max_temp", "min_temp")
THRESHOLD_KEYS = ("temp_warning", "temp_critical", "power_warning", "power_critical")
SCRIPT_FLAGS = ("script_direct_setpoint", "script_keep_ramp_generator")

LOOP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]*$")       # no "_": CSV columns are <name>_<value>
SHUTTER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")

PASSWORD_MIN_LENGTH = 4
_PBKDF2_ITERATIONS = 200_000


class ConfigError(Exception):
    """The configuration file cannot be read or used; the message says why."""


# ── Defaults ─────────────────────────────────────────────────────────────────

def default_eurotherm() -> dict:
    """A new Eurotherm loop as created by "Add" in the configuration editor."""
    return {
        "port": "COM1",
        "slave": 1,
        "baudrate": 9600,
        "shutter_line": None,
        "registers": copy.deepcopy(DEFAULT_REGISTERS),
        "limits": {"max_temp_rate": 15.0, "power_ramp_allowed": False,
                   "min_power_rate": 0.01, "max_temp": None, "min_temp": None},
        "critical_zone": None,
        "thresholds": {"temp_warning": 300, "temp_critical": 400,
                       "power_warning": 60, "power_critical": 80},
        "script_direct_setpoint": False,
        "script_keep_ramp_generator": False,
    }


def default_shutter() -> dict:
    return {"label": "", "line": "Dev1/port0/line0", "pressure_interlock": None}


def default_config() -> dict:
    """Empty machine: no instrument, simulation mode."""
    return {
        "version": 1,
        "machine": {"name": None, "simulation": True, "script_archive_dir": None},
        "safety": {"max_temp_rate_cap": 20.0},
        "daq": {"device": "Dev1", "ports": 3},
        "pressure_gauge": {"enabled": True, "port": "COM10", "baudrate": 300,
                           "bytesize": 7, "stopbits": 2,
                           "command": "DS IG1 \r\n", "unit": "mbar"},
        "eurotherms": {},
        "shutters": {},
        "security": {"password": None},
    }


# ── Normalization: any loaded file -> complete dict in a fixed key order ─────

def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _pair(value, default_decimals=0) -> list:
    """Register as [address, decimals] (a bare number means 0 decimals)."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [value[0], value[1]]
    return [value, default_decimals]


def _normalize_eurotherm(raw) -> dict:
    raw, out = _dict(raw), default_eurotherm()
    for key in ("port", "slave", "baudrate", "shutter_line", *SCRIPT_FLAGS):
        if key in raw:
            out[key] = raw[key]
    out["shutter_line"] = out["shutter_line"] or None
    registers = _dict(raw.get("registers"))
    # Known registers first (missing ones stay None and fail validation), then extras.
    out["registers"] = {name: (_pair(registers[name]) if name in registers else None)
                        for name in REGISTERS}
    out["registers"].update({name: _pair(value) for name, value in registers.items()
                             if name not in REGISTERS})
    limits = _dict(raw.get("limits"))
    out["limits"] = {key: limits.get(key) for key in LIMIT_KEYS}
    out["limits"].update({k: v for k, v in limits.items() if k not in LIMIT_KEYS})
    zone = raw.get("critical_zone")
    if isinstance(zone, dict) and zone.get("range") is not None:
        out["critical_zone"] = {"range": list(zone["range"]) if isinstance(
            zone["range"], (list, tuple)) else zone["range"], "slow_rate": zone.get("slow_rate")}
    else:
        out["critical_zone"] = None
    thresholds = _dict(raw.get("thresholds"))
    out["thresholds"] = {key: thresholds.get(key) for key in THRESHOLD_KEYS}
    if isinstance(raw.get("extra"), dict) and raw["extra"]:
        out["extra"] = copy.deepcopy(raw["extra"])
    return out


def _normalize_shutter(raw) -> dict:
    raw, out = _dict(raw), default_shutter()
    out["label"] = raw.get("label") or ""
    out["line"] = raw.get("line", raw.get("shutter_line", out["line"]))
    interlock = raw.get("pressure_interlock")
    if isinstance(interlock, dict):
        out["pressure_interlock"] = {
            "max_pressure_mbar": interlock.get("max_pressure_mbar"),
            "bypass": interlock.get("bypass", False),
        }
    return out


def normalize(raw) -> dict:
    """
    Complete configuration from the content of a YAML file: missing sections and
    keys get their default value, so a file written by an older version still loads.
    Values are kept as they are (validate() judges them).
    """
    if not isinstance(raw, dict):
        raise ConfigError("the file must contain a YAML mapping (machine:, eurotherms:, ...)")
    out = default_config()
    out["version"] = raw.get("version", 1)
    for section in ("machine", "safety", "daq", "pressure_gauge"):
        given = raw.get(section)
        if given is not None and not isinstance(given, dict):
            raise ConfigError(f"'{section}' must be a mapping")
        out[section].update(_dict(given))
    for section, normalizer in (("eurotherms", _normalize_eurotherm),
                                ("shutters", _normalize_shutter)):
        given = raw.get(section) or {}
        if not isinstance(given, dict):
            raise ConfigError(f"'{section}' must be a mapping of name -> settings")
        out[section] = {str(name): normalizer(entry) for name, entry in given.items()}
    password = _dict(raw.get("security")).get("password")
    out["security"] = {"password": copy.deepcopy(password) if isinstance(password, dict) else None}
    return out


# ── Validation ───────────────────────────────────────────────────────────────

def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate(config: dict) -> list[str]:
    """Every problem of a normalized configuration, as "where: what" lines ([] = valid)."""
    errors = []

    def check(ok, where, what):
        if not ok:
            errors.append(f"{where}: {what}")

    machine = config["machine"]
    check(machine["name"] is None or _text(machine["name"]), "Machine", "name must be text (or empty)")
    check(isinstance(machine["simulation"], bool), "Machine", "simulation must be true or false")
    check(machine["script_archive_dir"] is None or _text(machine["script_archive_dir"]),
          "Machine", "script archive folder must be text (or empty)")
    cap = config["safety"]["max_temp_rate_cap"]
    check(_is_number(cap) and cap > 0, "Safety", "max_temp_rate_cap must be a number > 0 (°C/min)")

    daq = config["daq"]
    check(_text(daq["device"]), "NI-DAQ", "device name is required (e.g. Dev1)")
    check(_is_int(daq["ports"]) and 1 <= daq["ports"] <= 12, "NI-DAQ", "ports must be an integer 1–12")

    gauge = config["pressure_gauge"]
    check(isinstance(gauge["enabled"], bool), "Pressure gauge", "enabled must be true or false")
    check(_text(gauge["port"]), "Pressure gauge", "serial port is required (e.g. COM10)")
    check(_is_int(gauge["baudrate"]) and gauge["baudrate"] > 0, "Pressure gauge",
          "baudrate must be a positive integer")
    check(gauge["bytesize"] in (5, 6, 7, 8), "Pressure gauge", "bytesize must be 5, 6, 7 or 8")
    check(gauge["stopbits"] in (1, 1.5, 2), "Pressure gauge", "stopbits must be 1, 1.5 or 2")
    check(isinstance(gauge["command"], str) and gauge["command"].strip(), "Pressure gauge",
          "command is required (e.g. \"DS IG1 \\r\\n\")")
    check(_text(gauge["unit"]), "Pressure gauge", "unit is required (the interlock needs mbar)")

    seen = {}
    for kind, names in (("Cell", config["eurotherms"]), ("Shutter", config["shutters"])):
        for name in names:
            key = name.casefold()
            check(key not in seen, f"{kind} {name}", f"name already used by {seen.get(key)}")
            seen.setdefault(key, f"{kind.lower()} {name}")
            check(name not in RESERVED_NAMES, f"{kind} {name}", "this name is reserved")

    for name, loop in config["eurotherms"].items():
        where = f"Cell {name}"
        check(LOOP_NAME_RE.match(name), where,
              "name may only contain letters, digits and '-' (no space, no '_')")
        check(_text(loop["port"]), where, "serial port is required (e.g. COM1)")
        check(_is_int(loop["slave"]) and 1 <= loop["slave"] <= 247, where,
              "Modbus slave must be an integer 1–247")
        check(_is_int(loop["baudrate"]) and loop["baudrate"] > 0, where,
              "baudrate must be a positive integer")
        check(loop["shutter_line"] is None or _text(loop["shutter_line"]), where,
              "shutter line must be text (or empty for no shutter)")
        for reg, pair in loop["registers"].items():
            check(pair is not None, where, f"register '{reg}' is missing")
            if pair is not None:
                check(_is_int(pair[0]) and 0 <= pair[0] <= 65535, where,
                      f"register '{reg}': address must be an integer 0–65535")
                check(_is_int(pair[1]) and 0 <= pair[1] <= 3, where,
                      f"register '{reg}': decimals must be 0–3")
        limits = loop["limits"]
        check(limits["max_temp_rate"] is None or (_is_number(limits["max_temp_rate"])
                                                  and limits["max_temp_rate"] > 0),
              where, "max_temp_rate must be a number > 0 (or empty)")
        check(limits["power_ramp_allowed"] in (None, True, False), where,
              "power_ramp_allowed must be true or false")
        check(limits["min_power_rate"] is None or (_is_number(limits["min_power_rate"])
                                                   and limits["min_power_rate"] > 0),
              where, "min_power_rate must be a number > 0 (or empty)")
        for key in ("max_temp", "min_temp"):
            check(limits[key] is None or _is_number(limits[key]), where,
                  f"{key} must be a number (or empty)")
        if _is_number(limits["max_temp"]) and _is_number(limits["min_temp"]):
            check(limits["min_temp"] < limits["max_temp"], where, "min_temp must be below max_temp")
        zone = loop["critical_zone"]
        if zone is not None:
            rng = zone.get("range")
            ok = (isinstance(rng, (list, tuple)) and len(rng) == 2
                  and all(_is_number(v) for v in rng) and rng[0] != rng[1])
            check(ok, where, "critical zone needs two different temperatures (from, to)")
            check(_is_number(zone.get("slow_rate")) and zone["slow_rate"] > 0, where,
                  "critical zone slow rate must be a number > 0 (°C/min)")
        for key, value in loop["thresholds"].items():
            check(value is None or _is_number(value), where, f"threshold {key} must be a number")
        for flag in SCRIPT_FLAGS:
            check(isinstance(loop[flag], bool), where, f"{flag} must be true or false")

    for name, shutter in config["shutters"].items():
        where = f"Shutter {name}"
        check(SHUTTER_NAME_RE.match(name), where,
              "name may only contain letters, digits, '-' and '_' (no space)")
        check(_text(shutter["line"]), where, "NI-DAQ line is required (e.g. Dev1/port0/line7)")
        interlock = shutter["pressure_interlock"]
        if interlock is not None:
            limit = interlock.get("max_pressure_mbar")
            check(_is_number(limit) and limit > 0, where,
                  "pressure interlock limit must be a number > 0 (mbar)")
            check(isinstance(interlock.get("bypass"), bool), where,
                  "pressure interlock bypass must be true or false")
    return errors


# ── YAML -> the dict used by the program ─────────────────────────────────────

def to_addresses(config: dict) -> dict:
    """
    Runtime dict, one entry per name (layout of the former addresses.py):

      Eurotherm loop  {"device": (port, slave), "baudrate": n,
                       "<register>": (address, decimals), ...,
                       "limits": {...}, "thresholds": {...},
                       "critical_zone": {"range": (a, b), "slow_rate": r},   # if any
                       "shutter_line": "Dev1/port0/line2",                   # if any
                       "script_direct_setpoint": True, ...}                  # if set
      "Pressure"      {"device": (port, baudrate), "bytesize", "stopbits", "command", "unit"}
      shutter         {"shutter_line": "...", "label": "...", "pressure_interlock": {...}}
    """
    addresses = {}
    for name, loop in config["eurotherms"].items():
        entry = {"device": (loop["port"], loop["slave"]), "baudrate": loop["baudrate"],
                 "limits": dict(loop["limits"])}
        for reg, pair in loop["registers"].items():
            if pair is not None:
                entry[reg] = (pair[0], pair[1])
        zone = loop["critical_zone"]
        if zone is not None:
            entry["critical_zone"] = {"range": tuple(zone["range"]),
                                      "slow_rate": zone["slow_rate"]}
        entry["thresholds"] = dict(loop["thresholds"])
        if loop["shutter_line"]:
            entry["shutter_line"] = loop["shutter_line"]
        for flag in SCRIPT_FLAGS:
            if loop[flag]:
                entry[flag] = True
        entry.update(copy.deepcopy(loop.get("extra", {})))
        addresses[name] = entry

    gauge = config["pressure_gauge"]
    if gauge["enabled"]:
        addresses[PRESSURE_KEY] = {
            "device": (gauge["port"], gauge["baudrate"]),
            "bytesize": gauge["bytesize"], "stopbits": gauge["stopbits"],
            "command": gauge["command"], "unit": gauge["unit"],
        }

    for name, shutter in config["shutters"].items():
        entry = {"shutter_line": shutter["line"], "label": shutter["label"] or name}
        if shutter["pressure_interlock"] is not None:
            entry["pressure_interlock"] = dict(shutter["pressure_interlock"])
        addresses[name] = entry
    return addresses


def is_loop(entry) -> bool:
    """True for the addresses entry of a Eurotherm loop."""
    return isinstance(entry, dict) and "temperature_pv" in entry


def loop_ids(addresses: dict) -> list[str]:
    """Eurotherm loops, in configuration order."""
    return [name for name, entry in addresses.items()
            if is_loop(entry) and name != PRESSURE_KEY]


def global_shutters(addresses: dict) -> list[tuple[str, str]]:
    """(id, label) of the shutters that do not belong to a Eurotherm loop."""
    return [(name, entry.get("label") or name) for name, entry in addresses.items()
            if isinstance(entry, dict) and "shutter_line" in entry
            and not is_loop(entry) and name != PRESSURE_KEY]


# ── Old addresses.py -> YAML ─────────────────────────────────────────────────

def from_legacy(addresses: dict, simulation=True, machine_name=None, archive_dir=None) -> dict:
    """Configuration equivalent to the content of a former addresses.py."""
    config = default_config()
    config["machine"].update(name=machine_name, simulation=bool(simulation),
                             script_archive_dir=archive_dir)
    config["pressure_gauge"]["enabled"] = False
    known = {"device", "limits", "critical_zone", "thresholds", "shutter_line", *SCRIPT_FLAGS}
    for name, entry in addresses.items():
        if not isinstance(entry, dict):
            continue
        if name == PRESSURE_KEY:
            port, baudrate = entry["device"]
            # The old program always used 300 baud, whatever was written here:
            # keep the written value only if it is a plausible baud rate.
            if not (_is_int(baudrate) and baudrate >= 50):
                baudrate = 300
            config["pressure_gauge"].update(enabled=True, port=port, baudrate=baudrate)
            for key in ("command", "unit"):
                if key in entry:
                    config["pressure_gauge"][key] = entry[key]
            if "unit" not in entry:
                config["pressure_gauge"]["unit"] = ""    # was not set: the interlock must refuse
        elif is_loop(entry):
            port, slave = entry["device"]
            loop = {"port": port, "slave": slave, "registers": {}, "extra": {}}
            for key, value in entry.items():
                if key in known:
                    if key != "device":
                        loop[key] = copy.deepcopy(value)
                elif key in REGISTERS or (isinstance(value, tuple) and len(value) == 2):
                    loop["registers"][key] = _pair(value)
                else:
                    loop["extra"][key] = copy.deepcopy(value)
            config["eurotherms"][name] = _normalize_eurotherm(loop)
        elif "shutter_line" in entry:
            config["shutters"][name] = _normalize_shutter({
                "label": name.replace("_", " "), "line": entry["shutter_line"],
                "pressure_interlock": entry.get("pressure_interlock")})
    return config


def _load_legacy_module(path: str):
    spec = importlib.util.spec_from_file_location("_legacy_addresses", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate_legacy(legacy_path: str = None) -> dict:
    """Read a former addresses.py and return the equivalent configuration."""
    module = _load_legacy_module(legacy_path or LEGACY_PATH)
    return from_legacy(module.addresses,
                       simulation=getattr(module, "SIMULATION", True),
                       machine_name=getattr(module, "MACHINE_NAME", None),
                       archive_dir=getattr(module, "SCRIPT_ARCHIVE_DIR", None))


# ── Files ────────────────────────────────────────────────────────────────────

class _Dumper(yaml.SafeDumper):
    """Short lists ([register, decimals], [from, to]) on one line."""

    def represent_list(self, data):
        flow = all(not isinstance(item, (list, dict)) for item in data)
        return self.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow)


_Dumper.add_representer(list, _Dumper.represent_list)

_HEADER = """\
# MBE Control - machine configuration ({name})
# Saved {when}.
#
# Edit from the program (Configuration tab, password protected) or by hand.
# Every key is explained in config/machine.example.yaml.
# Comments written here are lost when the file is saved from the program.
# A change takes effect at the next start of MBE Control.
"""


def dump(config: dict) -> str:
    """YAML text of a configuration, with the header comment."""
    body = yaml.dump(config, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                     default_flow_style=False, width=100)
    header = _HEADER.format(name=config["machine"]["name"] or "machine name not set",
                            when=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return header + "\n" + body


def load(path: str = None) -> dict:
    """Read and normalize a configuration file. ConfigError if it cannot be used."""
    path = path or CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e
    try:
        config = normalize(raw)
        to_addresses(config)          # must be buildable
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from e
    except Exception as e:
        raise ConfigError(f"{path}: unusable content ({e!r})") from e
    return config


def save(config: dict, path: str = None) -> str:
    """
    Validate and write the configuration. The previous file is kept as
    <path>.bak; the new one replaces it in one step (never a half-written file).
    Returns the path; ConfigError (nothing written) if the configuration is invalid.
    """
    path = path or CONFIG_PATH
    config = normalize(config)
    errors = validate(config)
    if errors:
        raise ConfigError("configuration not saved:\n" + "\n".join(errors))
    text = dump(config)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak")
        os.replace(tmp, path)
    except OSError as e:
        raise ConfigError(f"cannot write {path}: {e}") from e
    return path


def load_or_create(path: str = None, legacy_path: str = None,
                   example_path: str = None) -> tuple[dict, str]:
    """
    (configuration, how it was obtained). Without a file: convert the former
    addresses.py if there is one, else start from the example.
    """
    path = path or CONFIG_PATH
    legacy_path = legacy_path or LEGACY_PATH
    example_path = example_path or EXAMPLE_PATH
    if os.path.exists(path):
        return load(path), f"loaded from {path}"
    if os.path.exists(legacy_path):
        try:
            config = migrate_legacy(legacy_path)
        except Exception as e:
            raise ConfigError(f"cannot convert {legacy_path} to {path}: {e!r}") from e
        errors = validate(config)
        if errors:
            # Keep the machine usable: write the file as converted and report the
            # problems at startup (the editor refuses to save until they are fixed).
            _write_unchecked(config, path)
        else:
            save(config, path)
        return load(path), f"converted from {legacy_path} to {path}"
    if os.path.exists(example_path):
        config = load(example_path)
        _write_unchecked(config, path)
        return load(path), f"created {path} from the example (simulation mode)"
    return default_config(), "no configuration file: empty machine, simulation mode"


def _write_unchecked(config: dict, path: str):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(dump(config))
    except OSError as e:
        raise ConfigError(f"cannot write {path}: {e}") from e


# ── Changes (for the log) ────────────────────────────────────────────────────

def _flatten(value, prefix=""):
    if isinstance(value, dict):
        flat = {}
        for key, item in value.items():
            flat.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return flat
    return {prefix: value}


def describe_changes(old: dict, new: dict) -> list[str]:
    """One line per value that differs between two configurations (password excluded)."""
    def visible(config):
        return _flatten({k: v for k, v in config.items() if k != "security"})
    a, b = visible(old), visible(new)
    lines = []
    for key in list(a) + [k for k in b if k not in a]:
        if key not in b:
            lines.append(f"{key}: removed (was {a[key]!r})")
        elif key not in a:
            lines.append(f"{key}: added = {b[key]!r}")
        elif a[key] != b[key]:
            lines.append(f"{key}: {a[key]!r} -> {b[key]!r}")
    return lines


# ── Password of the configuration area ───────────────────────────────────────
# Keeps operators out of the settings; it does not protect the file itself
# (anyone who can edit machine.yaml can remove the "security" section).

def _hash_password(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def has_password(config: dict) -> bool:
    entry = _dict(config.get("security")).get("password")
    return isinstance(entry, dict) and bool(entry.get("hash")) and bool(entry.get("salt"))


def set_password(config: dict, password: str):
    """Store the salted hash of `password` in the configuration (never the password)."""
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise ConfigError(f"the password needs at least {PASSWORD_MIN_LENGTH} characters")
    salt = secrets.token_bytes(16)
    config.setdefault("security", {})["password"] = {
        "salt": salt.hex(),
        "iterations": _PBKDF2_ITERATIONS,
        "hash": _hash_password(password, salt, _PBKDF2_ITERATIONS),
    }


def check_password(config: dict, password: str) -> bool:
    if not has_password(config) or not isinstance(password, str):
        return False
    entry = config["security"]["password"]
    try:
        expected = _hash_password(password, bytes.fromhex(entry["salt"]),
                                  int(entry.get("iterations", _PBKDF2_ITERATIONS)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, str(entry["hash"]))


# ── Loaded once at import ────────────────────────────────────────────────────

LOAD_ERROR = None        # why the file could not be used (the program must not start)
LOAD_INFO = ""           # how the configuration was obtained (logged at startup)
try:
    config, LOAD_INFO = load_or_create()
except ConfigError as _e:
    LOAD_ERROR = str(_e)
    config = default_config()

addresses = to_addresses(config)
SIMULATION = config["machine"]["simulation"] is not False     # anything but false = simulation
MACHINE_NAME = config["machine"]["name"]
SCRIPT_ARCHIVE_DIR = config["machine"]["script_archive_dir"]
DAQ_DEVICE = config["daq"]["device"]
DAQ_PORTS = config["daq"]["ports"]
MAX_TEMP_RATE_CAP = config["safety"]["max_temp_rate_cap"]
