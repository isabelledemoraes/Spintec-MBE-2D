# 🛠️ Claude Code — Bug Fix & Feature Improvement Prompt

Copy and paste the full prompt below into Claude Code from your project root.

---

## ✅ Claude Code Prompt

```
You are a senior software engineer working on an instrument control interface 
(likely Python/PyQt or similar GUI framework with Eurotherm temperature 
controllers and pressure sensors).

Below is a prioritized list of bugs and improvements. Please:
1. Analyze the relevant files BEFORE making any changes
2. Fix each issue in order
3. Run any available tests after each fix
4. Summarize every change made at the end

---

## 🖥️ INTERFACE ISSUES

### 1. Eurotherm Scaling — Better Layout for Multiple Eurotherms
- PROBLEM: The interface does not scale well when more Eurotherm controllers 
  are added. Adding more Eurotherms makes the layout crowded or broken.
- EXPECTED: The UI should dynamically adapt — use a scrollable area, grid 
  layout, or collapsible panels so any number of Eurotherms can be added 
  without breaking the layout.
- ACTION: Find the widget/layout responsible for rendering Eurotherm 
  controllers and refactor it to support dynamic scaling (QScrollArea, 
  QGridLayout, or equivalent).

### 2. Pressure — Move to Separate Tab
- PROBLEM: Pressure data is currently displayed alongside temperature or 
  other data, making the interface cluttered.
- EXPECTED: Pressure should have its own dedicated tab.
- ACTION: 
  - Create a new "Pressure" tab in the main tab widget
  - Move all pressure-related widgets and plots into this tab
  - X-axis: human-readable timestamp (e.g., HH:MM:SS or DD/MM HH:MM) — 
    NOT a raw Unix timestamp or float. Make sure tick labels do not overlap 
    (rotate or auto-space them).
  - Y-axis: labeled in mbar, with appropriate range and gridlines.

---

## 📝 SCRIPT ISSUES

### 3. Script Window — Cannot Reopen After All Windows Are Closed
- PROBLEM: Once all script windows are closed, clicking to open a new script 
  window does nothing. The application does not open a new window.
- EXPECTED: It should always be possible to open a new script window, 
  regardless of how many are currently open (including zero).
- LIKELY CAUSE: The reference to the script window is lost when the last 
  window closes (garbage collected or set to None), so subsequent open 
  attempts silently fail.
- ACTION: Fix the window management logic so that a new script window 
  instance is always created fresh on demand. Check for patterns like 
  `self.script_window = ScriptWindow()` that may not be re-initialized 
  after close.

### 4. Follow-Up Graphic Stops Updating After Buffer Limit
- PROBLEM: The live/follow-up plot stops updating after a certain amount 
  of data is collected. It appears frozen.
- EXPECTED: The plot should continue scrolling/updating indefinitely during 
  a running script.
- LIKELY CAUSE: A fixed-size buffer (list, deque, or array) is filling up 
  and updates stop being appended or triggered. Or the plot update timer/
  callback is being disconnected.
- ACTION: 
  - Find the buffer that holds plot data and convert it to a rolling window 
    (use `collections.deque(maxlen=N)` for a sliding window, or implement 
    downsampling for long runs).
  - Verify the update timer/signal is never disconnected during a run.
  - Make the buffer size configurable if appropriate.

### 5. Temperature Ramp — Preview Not Working
- PROBLEM: Cannot preview the `temperature_ramp` before running it. 
  The preview feature is either missing, broken, or throws an error.
- EXPECTED: User should be able to visualize the full planned temperature 
  ramp (time vs. temperature) before executing it, without needing 
  instruments connected.
- ACTION:
  - Find or create a `preview_temperature_ramp()` function that parses the 
    ramp parameters and generates a simulated time-temperature curve.
  - Display it in a popup plot window (matplotlib figure or embedded plot).
  - This must work in mock/offline mode — no hardware connection required.
  - Add a "Preview Ramp" button near the ramp configuration UI.

---

## 📊 LOGS & VISUALIZATION — New Feature

### 6. New "Logs" Tab — Instrument Log Viewer
- PROBLEM: There is no easy way to visualize historical log data from 
  individual instruments in the GUI.
- EXPECTED: A new lightweight "Logs" tab that allows the user to:
  - Select which instrument's log to view (dropdown or list)
  - See the log data plotted as a clean time-series graph
  - The UI must be LIGHT and fast — avoid heavy dependencies or slow renders
- ACTION:
  - Create a new "Logs" tab in the main tab widget
  - Add an instrument selector (QComboBox) populated with available 
    instrument log files or live instrument list
  - On selection, parse the log file (CSV, TSV, or whatever format is used) 
    and render a pyqtgraph or matplotlib plot:
      - X-axis: clean timestamp (same format as Pressure tab)
      - Y-axis: auto-labeled with the measurement unit from the log
      - Include basic controls: zoom, pan, reset view
  - Keep rendering lightweight — use pyqtgraph (preferred for performance) 
    over matplotlib if the project allows it
  - If multiple log columns exist, allow the user to select which parameter 
    to plot

---

## 📋 GENERAL INSTRUCTIONS

- Do NOT change any working functionality unrelated to the issues above.
- Preserve all existing class/function names unless a rename is strictly 
  necessary for the fix.
- If a fix requires a new dependency, add it to requirements.txt and 
  tell me which package to install.
- After all fixes, provide a summary table:

  | # | Issue | Files Changed | Status |
  |---|-------|---------------|--------|
  | 1 | Eurotherm scaling | ... | ✅ Fixed |
  | 2 | Pressure tab | ... | ✅ Fixed |
  | 3 | Script reopen | ... | ✅ Fixed |
  | 4 | Buffer freeze | ... | ✅ Fixed |
  | 5 | Ramp preview | ... | ✅ Fixed |
  | 6 | Logs tab | ... | ✅ Fixed |
```

---

## 💡 Tips Before Running

- **Run from project root** so Claude Code can find all files automatically.
- If Claude Code asks a clarifying question about your log file format 
  (CSV, JSON, binary, etc.), answer it once and it will handle the rest.
- After the session, run `make test` or your test suite to validate nothing 
  was broken.
- Use `/compact` in Claude Code after issues 1–3 are fixed to save tokens 
  before continuing with 4–6.
