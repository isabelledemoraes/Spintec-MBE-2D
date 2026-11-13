"""
Text shown in every NEW script tab: one commented line per script function
(syntax + units + short note). All lines are '#' comments, so the template is an
empty script for the compiler (which warns until the user writes a command).
No Qt here, so tests can check it against script_compiler.DSL_PARAMS.
"""

SCRIPT_TEMPLATE = '''\
# ── Script functions (delete these comments if you like) ─────────────────────
# Loops: Au, CaF2, Se1, Se2, Substrate, SnSe.  Shutters: Au, CaF2, Se1, Se2, SnSe, Liquid_N2, eGun1, eGun2
# temperature_ramp("Au", target_C, rate_C_per_min)   - ramp to a temperature, returns at once (Se1/Se2: just sets the setpoint)
# power_ramp("Au", target_percent, rate_percent_per_s) - ramp output power (manual mode), returns at once
# hold(duration_min)                                  - wait this many minutes (ramps keep running)
# wait_until_temperature("Au", target_C, tolerance=0.5, timeout=None) - wait until within tolerance (C); timeout in seconds
# wait_until("YYYY-MM-DD HH:MM")                      - wait until this date and time
# open_shutter("eGun1")                               - open a shutter (Liquid_N2 only if pressure is low enough)
# close_shutter("eGun1")                              - close a shutter
# Example: temperature_ramp("Au", 300, 10)
#          hold(1)
'''
