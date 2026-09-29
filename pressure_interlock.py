# pressure_interlock.py
"""
Pressure interlock (issue #8).

A shutter whose addresses.py entry has a "pressure_interlock" dict, e.g.

    "Liquid_N2": {"shutter_line": "Dev1/port0/line7",
                  "pressure_interlock": {"max_pressure_mbar": 1.0e-8}},

may only be opened while the pressure gauge (addresses["Pressure"]) reads
strictly below max_pressure_mbar. The gauge is read at the moment of opening.
Any doubt (no gauge, read error, invalid number, bad configuration) blocks
the opening: check_pressure() returns only when opening is allowed.

Bypass (maintenance, gauge out of order): "bypass": True in the same dict
opens without any pressure check. Only the literal True counts.
"""
import math

# Same default as PressureGaugeController.
DEFAULT_GAUGE_COMMAND = "DS IG1 \r\n"


class InterlockError(RuntimeError):
    """Opening refused by the pressure interlock; nothing was written to the shutter."""


def interlock_for_line(addresses: dict, line_name: str):
    """
    Interlock config of the shutter driving this NI-DAQ line, or None.
    Matched on the line, not the ID, so any other addresses entry that uses
    the same line is interlocked too.
    """
    for info in addresses.values():
        if (isinstance(info, dict)
                and info.get("shutter_line") == line_name
                and "pressure_interlock" in info):
            return info["pressure_interlock"]
    return None


def is_bypassed(config) -> bool:
    """True only for "bypass": True (not 1, "yes", ...)."""
    return isinstance(config, dict) and config.get("bypass") is True


def bypassed_shutters(addresses: dict) -> list[str]:
    """IDs of the shutters whose pressure interlock is bypassed in addresses.py."""
    return [sid for sid, info in addresses.items()
            if isinstance(info, dict) and is_bypassed(info.get("pressure_interlock"))]


def gauge_name(addresses: dict) -> str:
    """Gauge channel for messages, e.g. "IG1" from the command "DS IG1"."""
    command = addresses.get("Pressure", {}).get("command", DEFAULT_GAUGE_COMMAND)
    parts = command.split()
    return parts[-1] if len(parts) >= 2 else "Pressure gauge"


def _is_positive_number(value) -> bool:
    return (isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0)


def config_problem(addresses: dict, config) -> str | None:
    """Why this interlock configuration cannot be used, or None if it is valid."""
    limit = config.get("max_pressure_mbar") if isinstance(config, dict) else None
    if not _is_positive_number(limit):
        return f"'max_pressure_mbar' must be a positive number in addresses.py (got {limit!r})"
    unit = addresses.get("Pressure", {}).get("unit")
    if unit != "mbar":
        return f"addresses['Pressure']['unit'] must be 'mbar' (got {unit!r})"
    return None


def check_pressure(gauge, addresses: dict, config) -> float:
    """
    Read the gauge now and return the pressure (mbar) if it is strictly below
    the limit. Otherwise raise InterlockError with the measured value (or the
    reason there is none) and the required limit.
    """
    name = gauge_name(addresses)
    problem = config_problem(addresses, config)
    if problem:
        raise InterlockError(f"pressure interlock configuration invalid: {problem}")

    limit = config["max_pressure_mbar"]
    required = f"required < {limit:.1e} mbar"

    if gauge is None or not getattr(gauge, "connected", False):
        raise InterlockError(f"{name} unavailable (gauge not connected), {required}")
    try:
        value = gauge.read_value("pressure")
    except Exception as e:
        raise InterlockError(f"{name} reading failed ({e}), {required}") from e
    if not _is_positive_number(value):
        raise InterlockError(f"{name} reading invalid ({value!r}), {required}")
    if not value < limit:
        raise InterlockError(f"{name} = {value:.1e} mbar, {required}")
    return value
