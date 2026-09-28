# Feature: Add `wait_until()` to the script DSL

## Context

The project is an MBE instrument control GUI (see CLAUDE.md).
Scripts are plain text executed inside a sandboxed `exec()` in `script_editor_integration.py`.
The allowed DSL functions are defined inside the `runner()` closure in `ScriptEditor.run_script()`,
and validated statically in `ScriptEditor._validate_script()`.

## What to implement

Add a new DSL function:

```python
wait_until("2026-06-10 08:30")
```

### Signature
```python
def wait_until(datetime_str: str):
```

### Format
- Argument: a single string in the format `"YYYY-MM-DD HH:MM"` (24-hour clock).
- Example: `wait_until("2026-06-10 08:30")`

### Behaviour
1. Parse `datetime_str` using `datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")`.
2. If the target time is **already in the past**:
   - Log a warning: `[Script N] wait_until: target time YYYY-MM-DD HH:MM is in the past — skipping.`
   - Return immediately (do not abort).
3. If the target time is in the future:
   - Log: `[Script N] Waiting until YYYY-MM-DD HH:MM (in X h YY min).`
   - Sleep in short intervals (e.g. 5 seconds) polling `stop_event` so the script can still
     be stopped by the user during the wait.
   - While waiting, emit `_hw_status` signal every poll with a countdown string like:
     `⏳ Waiting until 08:30 — 2 h 14 min remaining`
   - When the target time is reached, clear the status label and log:
     `[Script N] wait_until reached — continuing.`

### Stop event handling
Check `self.stop_event.is_set()` in the polling loop and return immediately if set,
consistent with how `hold()` is implemented.

---

## Files to modify

### 1. `script_editor_integration.py`

#### A — Add `wait_until` inside `runner()` (next to `hold`, `power_ramp`, etc.)

Add the function definition after `hold()`:

```python
def wait_until(datetime_str: str):
    from datetime import datetime as _dt
    try:
        target = _dt.strptime(datetime_str, "%Y-%m-%d %H:%M")
    except ValueError:
        self.log_callback(
            f"[{self.script_name}] wait_until: invalid format '{datetime_str}'. "
            f"Expected YYYY-MM-DD HH:MM."
        )
        return

    now = _dt.now()
    if target <= now:
        self.log_callback(
            f"[{self.script_name}] wait_until: target time {datetime_str} is in the past — skipping."
        )
        return

    total_sec = (target - now).total_seconds()
    h, rem = divmod(int(total_sec), 3600)
    m = rem // 60
    self.log_callback(
        f"[{self.script_name}] Waiting until {datetime_str} (in {h} h {m:02d} min)."
    )

    while not self.stop_event.is_set():
        remaining = (target - _dt.now()).total_seconds()
        if remaining <= 0:
            break
        h2, rem2 = divmod(int(remaining), 3600)
        m2 = rem2 // 60
        self._hw_status.emit(
            f"⏳ Waiting until {target.strftime('%H:%M')} — {h2} h {m2:02d} min remaining"
        )
        time.sleep(min(5, remaining))

    self._hw_status.emit("")
    if not self.stop_event.is_set():
        self.log_callback(f"[{self.script_name}] wait_until reached — continuing.")
```

#### B — Register `wait_until` in the `exec()` globals dict

Find the `exec(code, {...})` call at the bottom of `runner()` and add:

```python
"wait_until": wait_until,
```

#### C — Add `wait_until` to `_validate_script()`

In `_validate_script()`, add to the `allowed_functions` dict:

```python
"wait_until": {"min_args": 1, "max_args": 1},
```

Then add argument validation for it (after the `hold` block):

```python
if func_name == "wait_until":
    arg1 = node.args[0]
    if not isinstance(arg1, ast.Constant) or not isinstance(arg1.value, str):
        errors.append(
            f"Error on line {node.lineno}: Argument of 'wait_until' must be a string "
            f'(e.g., "2026-06-10 08:30").'
        )
    else:
        # Validate format at check time
        try:
            from datetime import datetime as _dt
            _dt.strptime(arg1.value, "%Y-%m-%d %H:%M")
        except ValueError:
            errors.append(
                f"Error on line {node.lineno}: 'wait_until' date '{arg1.value}' "
                f"is not in YYYY-MM-DD HH:MM format."
            )
```

#### D — Add `wait_until` to `check_script()` preview (dry-run simulation)

In `check_script()`, find where the dry-run functions are defined
(the local `power_ramp`, `hold`, etc. that just accumulate `recipe_data`)
and add a no-op stub so Check doesn't error on scripts containing `wait_until`:

```python
def wait_until(datetime_str: str):
    pass  # no-op in preview
```

Register it in the `exec()` globals for the preview run:

```python
"wait_until": wait_until,
```

---

## 2. Update `CLAUDE.md`

In the **Script DSL** section, add `wait_until` to the command reference:

```
wait_until("YYYY-MM-DD HH:MM")   # blocks until that date/time; skips silently if in the past
```

In **Recent work / open items**, mark this feature as done once implemented.

---

## Expected usage example

```python
temperature_ramp("Au", 200, 5)
wait_until("2026-06-11 06:00")   # wait for morning shift before continuing
temperature_ramp("Au", 800, 3)
open_shutter("Au")
hold(60)
close_shutter("Au")
```

---

## Output format

Show each change as a diff or inline edit with the exact location (function name / line context).
Do not refactor unrelated code. Do not change existing function signatures.
