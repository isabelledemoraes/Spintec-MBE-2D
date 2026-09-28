# 🔥 Claude Code — Synchronized Ramp Calculator with Critical Zones

Paste the prompt below into Claude Code from your project root.

---

## ✅ Claude Code Prompt

```
You are a senior software engineer working on an instrument control platform 
for physical vapor deposition (PVD/evaporation). The software controls 
Eurotherm temperature controllers and power sources for multiple evaporation 
cells (Au, Se, Substrate, and others).

## CONTEXT

The scripting system is single-threaded per tab. Multiple script tabs run 
in parallel threads. Scripts use functions like:
  - temperature_ramp(cell, target_temp, rate_°C_per_min)
  - power_ramp(cell, target_power, rate_%_per_min)
  - hold(minutes)
  - wait(minutes)

The user wants to start multiple cells at DIFFERENT times so they ALL reach 
their target temperature at the SAME time — synchronized arrival.

The delay for each cell is calculated as:
  ramp_time = (target_temp - current_temp) / rate
  delay = max_ramp_time - this_cell_ramp_time

The cell with the longest ramp time gets delay = 0 (starts immediately).
All others get a wait() prepended to their script.

---

## FEATURE TO BUILD: Synchronized Ramp Calculator

### What it does:
1. User inputs for each cell:
   - Current temperature (read live from the instrument OR entered manually)
   - Target temperature
   - Ramp rate (°C/min) — but this may be VARIABLE due to critical zones (see below)

2. The calculator computes:
   - Total ramp time per cell (accounting for variable rates through critical zones)
   - The required start delay for each cell
   - Displays a visual timeline preview (horizontal bar chart, one bar per cell)

3. Output: generates ready-to-paste script blocks for each tab, like:
   
   --- Tab: Au ---
   wait(0)
   temperature_ramp("Au", 1300, 15)
   
   --- Tab: Se ---
   wait(34.2)   # minutes
   temperature_ramp("Se", 189, 2)   # slow rate through critical zone
   
   --- Tab: Substrate ---
   wait(18.5)
   power_ramp("Sub", 60, 2)
   hold(10)
   power_ramp("Sub", 0, 1)

---

## CRITICAL ZONE LOGIC

Some materials must slow down their ramp rate when passing through a 
critical temperature range near their melting point. This changes the 
total ramp time calculation significantly.

Implement a MATERIAL PROFILE system with the following known materials.
Each profile defines normal rate, critical zone boundaries, and the 
reduced rate inside the critical zone:

### Material Profiles (build these as a config dict or class):

Se (Selenium):
  - No critical zone
  - Normal rate: user-defined (e.g. 4°C/min)
  - Simple linear ramp calculation only

Au (Gold):
  - Melting point: 1064°C
  - Critical zone: 1000°C → 1080°C
  - Normal rate: user-defined (e.g. 15°C/min)
  - Critical zone rate: 3°C/min  ← must slow down through this range
  - After critical zone: user-defined rate resumes
  - This is the ONLY material with a critical zone currently

Substrate:
  - No melting point constraint
  - Power-based ramp, not temperature-based
  - Ramp time = delta_power / rate_%_per_min
  - No critical zone

Other materials:
  - Allow the user to define a custom material with:
    - Name
    - Melting point
    - Critical zone: (melting_point - 20°C) → (melting_point + 15°C)
    - Critical zone rate: default to 20% of normal rate
  - Store custom materials persistently (JSON config file) so they 
    are available next time the software is launched.

### Ramp time calculation with critical zone:

def calculate_ramp_time(start_temp, target_temp, normal_rate, profile):
    time = 0
    temp = start_temp
    
    if profile has critical zone AND path crosses it:
        # Segment 1: start_temp → critical_zone_start (normal rate)
        # Segment 2: critical_zone_start → critical_zone_end (slow rate)
        # Segment 3: critical_zone_end → target_temp (normal rate)
        # Sum all three segments
    else:
        time = (target_temp - start_temp) / normal_rate
    
    return time  # in minutes

Handle edge cases:
  - start_temp already inside critical zone
  - target_temp inside critical zone (never exits it)
  - critical zone not in path (start and target both below or both above)

---

## UI REQUIREMENTS

### Placement:
Build this as a NEW TAB in the main interface, labeled "⏱ Sync Ramp".
It should sit alongside the existing tabs (Temperature, Pressure, 
Scripts, Logs, etc.) and be always accessible — not a popup.

### Layout of the dialog:

+--------------------------------------------------+
|  ⏱ Synchronized Ramp Calculator                  |
+--------------------------------------------------+
|  [+ Add Cell]                                    |
|                                                  |
|  Cell: [Au ▼]  Current: [auto/manual °C]         |
|  Target: [1300 °C]  Rate: [15 °C/min]            |
|  Profile: Au (critical zone: 1000-1080°C) ℹ      |
|  Estimated time: 58.3 min                        |
|  [🗑 Remove]                                      |
|                                                  |
|  Cell: [Se ▼]  Current: [auto/manual °C]         |
|  Target: [189 °C]   Rate: [4 °C/min]             |
|  Profile: Se (critical zone: 200-220°C) ℹ        |
|  Estimated time: 41.2 min  ← NOT in critical zone|
|  [🗑 Remove]                                      |
|                                                  |
+--------------------------------------------------+
|  📊 TIMELINE PREVIEW                             |
|  Au   |████████████████████████████| 58.3 min    |
|  Se        |████████████████████| 41.2 min       |
|  Sub            |██████| 12.0 min                |
|  (bars start at their delay offset)              |
+--------------------------------------------------+
|  [📋 Generate Scripts]  [❌ Close]               |
+--------------------------------------------------+

### Generated output panel:
After clicking "Generate Scripts", show a text area with copy buttons 
for each tab:

  ┌─ Tab: Au ──────────────────────┐ [Copy]
  │ wait(0)                        │
  │ temperature_ramp("Au", 1300, 15)│
  └────────────────────────────────┘

  ┌─ Tab: Se ──────────────────────┐ [Copy]
  │ wait(17.1)                     │
  │ temperature_ramp("Se", 189, 4) │
  └────────────────────────────────┘

### Auto-read current temperatures:
- If the Eurotherm workers are running, populate "Current Temp" fields 
  automatically from live readings.
- Add a small 🔄 refresh button next to each current temp field.
- If not connected, allow manual entry.

---

## ADDITIONAL REQUIREMENTS

- All delay values rounded to 1 decimal place (minutes)
- Timeline preview uses a simple horizontal bar chart — 
  use pyqtgraph or QPainter bars, NOT matplotlib (keep it lightweight)
- Critical zone segments shown in a different color (orange/red) 
  on the timeline bars
- Add a tooltip on the ℹ icon showing critical zone details for 
  the selected material
- Material profiles stored in a JSON config file (e.g. 
  `config/material_profiles.json`) so users can edit them without 
  touching code
- The generated wait() values must account for the time it takes 
  to open the dialog, calculate, and paste — add a small "execution 
  offset" field (default: 0 min) the user can bump up if they need 
  time to set up tabs

---

## INSTRUCTIONS

1. First scan the project to understand:
   - How script tabs are created and managed
   - How temperature_ramp() and power_ramp() are defined
   - How live temperature readings are accessed from EurothermWorker
   - Where dialogs/popups are created in the codebase

2. Build the material profiles config system first

3. Build the ramp time calculator logic with full critical zone support

4. Build the UI dialog

5. Register the new "⏱ Sync Ramp" tab in the main tab widget, 
   in a logical position (e.g. after Scripts tab)

6. Test the calculation logic with these known cases:
   - Se at 20°C → 189°C at 4°C/min (no critical zone → simple calc)
   - Se at 20°C → 250°C at 4°C/min (no critical zone → simple calc)
   - Au at 25°C → 1300°C at 15°C/min (DOES cross critical zone 
     1000-1080°C → segmented calc)
   - Au at 25°C → 500°C at 15°C/min (does NOT cross critical zone 
     → simple calc)
   - Au at 1020°C → 1300°C at 15°C/min (starts INSIDE critical zone 
     → edge case)

7. Do NOT modify any existing ramp or script execution logic.
```

---

## 💡 Tips Before Running

- Make sure Claude Code can see your `config/` folder (or equivalent) 
  so it places the JSON profiles in the right location.
- After generation, test the calculator offline — no instruments needed.
- You can edit `material_profiles.json` directly to tweak critical zone 
  boundaries without touching any code.
- The "execution offset" field is your friend for complex setups where 
  you need 2-3 minutes to get all tabs ready before hitting Run.
