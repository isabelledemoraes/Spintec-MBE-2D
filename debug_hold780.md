# Debug: Scripts stuck in `hold(780)` / `hold(750)` — never reaching `temperature_ramp()`

## Context
- **Scripts:** `Fe_ramp-20260304.py`, `Ge_ramp-20260304.py`, `substrate_ramp-20260304.py`
- **Symptom:** All 3 scripts successfully completed their first `hold()`, then entered the second `hold(780)` / `hold(750)` and never exited — `temperature_ramp()` was never reached.
- **Reproducible:** Yes. This happened on 2026-03-04. The next day (2026-03-05), scripts were manually restarted mid-hold and ran correctly.

## Timeline evidence from `dialog_20260304.txt`

```
[17:17:34] Script 1 (Ge)        → Holding for 45 min   ✅ first hold
[17:17:36] Script 2 (Fe)        → Holding for 45 min   ✅ first hold
[17:17:37] Script 3 (Substrate) → Holding for 45 min   ✅ first hold

[17:28:43] Script 2 (Fe) stopped by user mid-hold (statement 1 only)
[17:34:21] Script 2 (Fe) restarted → Holding for 30 min  (adjusted hold)

[18:11:18] → Holding for 780 min   ← Script 1 or 2 entered second hold
[18:12:01] → Holding for 750 min   ← Script 3 entered second hold
[18:12:14] → Holding for 780 min   ← third script entered second hold

[21:44:01] Script 6 stopped
[21:44:04] Script 5 stopped
```

The `temperature_ramp()` call **never appears** in the log after the 780/750 min holds.
The holds of 780 and 750 min are approximately **13 hours** — correct for an overnight ramp,
but the scripts appear to have started holding at ~18:11, meaning they should have
reached `temperature_ramp()` at ~07:11 the next morning.

Instead, on 2026-03-05, the operator manually stopped and restarted all scripts at ~07:47,
suggesting the holds ran past their expected end time **or** the transition to
`temperature_ramp()` silently failed.

## Script structure (all 3 follow same pattern)

```python
hold(45)         # statement 1 — wait until ramp start time
hold(780)        # statement 2 — overnight hold at standby temperature
temperature_ramp("Ge", 1098.5, 10)  # statement 3 — never reached
```

Script 2 (Fe) was stopped after statement 1 and restarted with a corrected `hold(30)`,
meaning its second hold started ~17 min later than the others. This desynchronization
may be relevant.

## Expected vs Actual behavior
- **Expected:** After `hold(780)` / `hold(750)` completes (~13 hours), script advances
  to `temperature_ramp()` automatically.
- **Actual:** Scripts appeared to stay blocked in the second hold. The operator had to
  manually interrupt and restart them the next morning (~07:47), at which point
  `temperature_ramp()` executed successfully.

## Hypotheses to investigate
1. **Hold duration computed incorrectly** — does `hold(780)` mean 780 seconds or 780 minutes?
   If seconds (~13 min), the hold would have expired ~18:25 and `temperature_ramp()` should
   have run. If minutes (~13 h), it should complete ~07:11 next morning. Check what unit
   `hold()` uses and whether the overnight log shows the ramp starting autonomously.
2. **Script 2 restart broke shared state** — Script 2 was stopped mid-hold and restarted.
   Could this have corrupted a shared timer, lock, or event flag that other scripts wait on?
3. **Silent exception in hold()** — Does `hold()` swallow exceptions? Could a timeout,
   resource contention, or scheduler issue have caused it to block indefinitely?
4. **Transition condition** — Is there a condition (temperature threshold, instrument state)
   that must be met before `hold()` releases? Check whether the standby log shows any
   instrument anomaly between 18:00 and 07:47.

## Task
1. Read the `hold()` and `temperature_ramp()` implementations.
2. Determine what triggers `hold()` to exit — is it purely time-based, or does it check
   external state?
3. Check whether Script 2's mid-sequence stop/restart could affect the hold completion
   logic of Scripts 1 and 3.
4. Identify the root cause. Propose a targeted fix.
5. Explain the fix in 2–3 sentences. Do not refactor unrelated code.
