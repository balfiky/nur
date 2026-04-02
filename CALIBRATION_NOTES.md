# Calibration Notes — Phase 10

Tuning decisions applied to the cognitive pipeline's decision heuristics.
All changes are behavioral (threshold values), not structural.

---

## 1. Persistence drive base: 0.50 → 0.45

**File:** `core/action_variables.py`

**Formula:** `persistence_drive = 0.45 + resolution * 0.25 + (energy - 0.5) * 0.2`

**Why:** At the old base of 0.50, persistence_drive almost never dropped below 0.5 — the threshold at which `task_planning.execute_plan()` blocks a plan after a step failure. This meant plans always continued through failures, even when the system was tired (low energy) and had no unfinished business (zero resolution). The "give up when exhausted" path was effectively dead code.

**New behavior:**
- Energy 0.2, resolution 0.0 → persistence = 0.39 → plans block on failure
- Energy 0.5, resolution 0.0 → persistence = 0.45 → plans block on failure
- Energy 0.5, resolution 0.3 → persistence = 0.525 → plans continue through failure
- Energy 0.7, resolution 0.0 → persistence = 0.49 → borderline, blocks

**Tradeoff:** Multi-step plans are slightly more likely to stop mid-way when the system has low energy and no resolution tension driving it forward. This is intentional — the system should not blindly push through failures when it has no motivation to do so.

---

## 2. Action urgency base: 0.30 → 0.25

**File:** `core/action_variables.py`

**Formula:** `action_urgency = 0.25 + (arousal - 0.5) * 0.4 + resolution * 0.2 - (0.5 - min(energy, 0.5)) * 0.2`

**Why:** The defer decision path (`action_urgency < 0.15`) was unreachable at the old base of 0.30. Even with arousal at 0.0 and energy at 0.0, urgency bottomed out at 0.30 - 0.20 - 0.10 = 0.00... but that required both at absolute zero. In practice, the defer path never fired because moderate arousal or energy kept urgency well above 0.15.

**New behavior:**
- Arousal 0.2, energy 0.2, resolution 0.0 → urgency = 0.07 → defer fires
- Arousal 0.3, energy 0.3, resolution 0.0 → urgency = 0.13 → defer fires
- Arousal 0.5, energy 0.5, resolution 0.0 → urgency = 0.25 → no defer
- Arousal 0.7, energy 0.5, resolution 0.0 → urgency = 0.33 → no defer

**Tradeoff:** When the system is both calm (low arousal) and tired (low energy) and has no unfinished business, it may defer tool actions. This matches the intended design: "too tired / no drive" should actually cause deferral.

---

## 3. Proactive valence boost: +0.05 when valence < 0.3

**File:** `core/proactive.py`

**Why:** Proactive trigger scoring was influenced by resolution, energy, arousal, and trust — but not by mood (valence). A system in a negative mood with high resolution (unfinished business) should feel *more* compelled to act on that unfinished business, not the same as a neutral system. This is the "negative mood amplifies unfinished-business sense" coherence fix.

**New behavior:** When `state.valence < 0.3`, all proactive trigger scores get +0.05. This is a small boost that only matters at the activation threshold boundary.

**Tradeoff:** Slightly more proactive behavior when the system is in a negative mood. The boost is small enough (0.05) that it won't cause spurious proactive actions on its own — it only tips triggers that were already close to the activation threshold (0.4).

---

## 4. Tool trust positive delta: 0.01 → 0.015

**File:** `core/tool_memory.py`

**Why:** The original trust asymmetry for tool actions was 1:3 (positive +0.01, negative -0.03). This was too harsh compared to the general conversation asymmetry of 1:7.5 (+0.02/-0.15). Helpful tool actions (successful reads, searches) should build trust a bit faster since they demonstrate competence, not just social exchange.

**New behavior:** Trust asymmetry for tool actions is now 1:2 (+0.015/-0.03). Still conservative, still asymmetric, but trust builds slightly faster from helpful tool use.

**Tradeoff:** Trust recovers marginally faster after tool-related negative events. The change is small (0.005 per action) and still heavily skewed toward caution.

---

## 5. Arbiter thresholds extracted as named constants

**File:** `core/dual_process/tool_loop.py`

```python
REFUSE_RISK_TOLERANCE = 0.4       # destructive + risk below this → refuse
CLARIFY_AUTONOMY_BIAS = 0.35     # autonomy below this → clarify
CLARIFY_WRITE_THRESHOLD = 0.65   # write/destructive + clarification above this → clarify
DEFER_URGENCY_THRESHOLD = 0.15   # urgency below this → defer
```

**Why:** These values were previously hardcoded inline in `make_tool_decision()`. Extracting them as named constants makes the tuning surface visible and documents what each threshold means. No behavioral change — same values as before.

---

## Verification

- All 1144+ tests pass (pytest)
- 28/28 eval scenarios pass (72/72 assertions)
- 8 new calibration boundary scenarios lock in the tuned thresholds
- No regressions in emotional core, tool loop, task planning, proactive, defense, or relationship suites
