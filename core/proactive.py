"""Proactive behavior evaluation — bounded self-initiated action.

Evaluates whether Jarvis feels compelled to initiate something based on:
  - Unresolved cognitive tension (items with sufficient intensity)
  - Pending task plans (incomplete multi-step work)
  - Commitments and follow-up obligations
  - Temporal patterns (session idle time)
  - Emotionally salient unfinished matters

All logic is deterministic — zero LLM calls.
All autonomy is bounded — strict limits on proactive actions per session.
"""

from __future__ import annotations

import time
from typing import Any

from core.types import (
    ModulatorState,
    PersonProfile,
    ProactiveAction,
    ProactiveTrace,
    ProactiveTrigger,
    ProactiveTriggerSource,
    TaskPlan,
    TaskStatus,
    UnresolvedItem,
)

# ---------------------------------------------------------------------------
# Default configuration values
# ---------------------------------------------------------------------------

DEFAULT_IDLE_THRESHOLD = 300.0   # 5 minutes idle before proactive check
DEFAULT_MAX_PROACTIVE = 3        # max proactive actions per session
DEFAULT_COOLDOWN = 300.0         # 5 minutes between proactive actions
DEFAULT_ACTIVATION_THRESHOLD = 0.4  # trigger intensity must exceed this

# Intensity boosts from modulator state
_RESOLUTION_BOOST = 0.15        # high resolution → more proactive
_LOW_ENERGY_PENALTY = -0.10     # low energy → less proactive
_HIGH_BONDING_BOOST = 0.05      # high bonding → more willing to reach out


# ---------------------------------------------------------------------------
# Trigger collection
# ---------------------------------------------------------------------------

def _collect_triggers(
    unresolved_items: list[UnresolvedItem],
    active_plan: TaskPlan | None,
    idle_seconds: float,
    state: ModulatorState,
) -> list[ProactiveTrigger]:
    """Gather all potential proactive triggers from current state."""
    triggers: list[ProactiveTrigger] = []

    # 1. Unresolved items with sufficient intensity
    for item in unresolved_items:
        if item.intensity >= 0.3:
            triggers.append(ProactiveTrigger(
                source=ProactiveTriggerSource.UNRESOLVED_ITEM,
                description=item.description,
                intensity=item.intensity,
                item_id=item.id,
            ))

    # 2. Pending task plan
    if active_plan and not active_plan.is_terminal:
        pending_steps = sum(
            1 for s in active_plan.steps if s.status == TaskStatus.PENDING
        )
        if pending_steps > 0:
            plan_intensity = 0.4 + 0.1 * min(pending_steps, 3)
            triggers.append(ProactiveTrigger(
                source=ProactiveTriggerSource.PENDING_TASK,
                description=f"Pending plan: {active_plan.goal[:80]} ({pending_steps} steps left)",
                intensity=min(1.0, plan_intensity),
                item_id=active_plan.id,
            ))

    # 3. Commitment-type unresolved items (separate from generic unresolved)
    for item in unresolved_items:
        if item.source == "commitment" and item.intensity >= 0.2:
            triggers.append(ProactiveTrigger(
                source=ProactiveTriggerSource.COMMITMENT,
                description=item.description,
                intensity=item.intensity + 0.1,  # commitments get a boost
                item_id=item.id,
            ))

    # 4. Temporal: long idle with high resolution
    if idle_seconds > DEFAULT_IDLE_THRESHOLD and state.resolution > 0.3:
        temporal_intensity = min(1.0, 0.3 + state.resolution * 0.3)
        triggers.append(ProactiveTrigger(
            source=ProactiveTriggerSource.TEMPORAL,
            description=f"Idle for {idle_seconds:.0f}s with unresolved tension",
            intensity=temporal_intensity,
        ))

    # 5. Emotionally salient: high resolution + specific emotional state
    if state.resolution > 0.5 and state.arousal > 0.4:
        triggers.append(ProactiveTrigger(
            source=ProactiveTriggerSource.EMOTIONAL_SALIENCE,
            description="Emotionally salient unfinished matters",
            intensity=min(1.0, state.resolution * 0.7 + state.arousal * 0.2),
        ))

    return triggers


# ---------------------------------------------------------------------------
# Trigger scoring (modulator-influenced)
# ---------------------------------------------------------------------------

def _score_trigger(
    trigger: ProactiveTrigger,
    state: ModulatorState,
    person: PersonProfile,
) -> float:
    """Score a trigger, influenced by current modulator state.

    Returns adjusted intensity (may exceed 1.0, clamped by caller).
    """
    score = trigger.intensity

    # Resolution boost: high resolution makes proactive action more likely
    if state.resolution > 0.4:
        score += _RESOLUTION_BOOST

    # Energy penalty: low energy suppresses proactive action
    if state.energy < 0.3:
        score += _LOW_ENERGY_PENALTY

    # Bonding boost: more willing to reach out to trusted people
    if person.trust > 0.6:
        score += _HIGH_BONDING_BOOST

    # Arousal: moderate arousal drives action, very high suppresses (overwhelmed)
    if 0.3 < state.arousal < 0.7:
        score += 0.05
    elif state.arousal > 0.8:
        score -= 0.10

    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Action selection
# ---------------------------------------------------------------------------

def _select_action(
    trigger: ProactiveTrigger,
    active_plan: TaskPlan | None,
    state: ModulatorState,
) -> ProactiveAction:
    """Given the best trigger, decide what action to take."""

    # Pending task → offer to continue
    if trigger.source == ProactiveTriggerSource.PENDING_TASK and active_plan:
        return ProactiveAction(
            action_type="continue_task",
            trigger=trigger,
            message=f"I have an unfinished task: {active_plan.goal[:100]}. Should I continue?",
            rationale=f"Pending plan with {sum(1 for s in active_plan.steps if s.status == TaskStatus.PENDING)} steps remaining",
        )

    # Commitment → follow up
    if trigger.source == ProactiveTriggerSource.COMMITMENT:
        return ProactiveAction(
            action_type="follow_up",
            trigger=trigger,
            message=f"I wanted to follow up on something: {trigger.description[:100]}",
            rationale=f"Commitment item (intensity={trigger.intensity:.2f})",
        )

    # Unresolved item → follow up
    if trigger.source == ProactiveTriggerSource.UNRESOLVED_ITEM:
        return ProactiveAction(
            action_type="follow_up",
            trigger=trigger,
            message=f"Something has been on my mind: {trigger.description[:100]}",
            rationale=f"Unresolved tension (intensity={trigger.intensity:.2f})",
        )

    # Temporal / emotional → suggest engagement
    if trigger.source in (ProactiveTriggerSource.TEMPORAL, ProactiveTriggerSource.EMOTIONAL_SALIENCE):
        return ProactiveAction(
            action_type="suggest",
            trigger=trigger,
            message="I've been thinking about some things we left unresolved.",
            rationale=f"Idle timeout with active tension (resolution={state.resolution:.2f})",
        )

    # Fallback
    return ProactiveAction(
        action_type="suggest",
        trigger=trigger,
        message=f"I noticed something worth addressing: {trigger.description[:100]}",
        rationale="General proactive trigger",
    )


# ---------------------------------------------------------------------------
# Main evaluation function
# ---------------------------------------------------------------------------

def evaluate_proactive(
    state: ModulatorState,
    unresolved_items: list[UnresolvedItem],
    active_plan: TaskPlan | None,
    person: PersonProfile,
    idle_seconds: float,
    proactive_count: int,
    last_proactive_at: float | None = None,
    *,
    max_proactive: int = DEFAULT_MAX_PROACTIVE,
    idle_threshold: float = DEFAULT_IDLE_THRESHOLD,
    cooldown: float = DEFAULT_COOLDOWN,
    activation_threshold: float = DEFAULT_ACTIVATION_THRESHOLD,
) -> tuple[ProactiveAction | None, ProactiveTrace]:
    """Evaluate whether Jarvis should initiate proactive behavior.

    Returns (action, trace) where action is None if no proactive behavior
    is warranted. The trace is always populated for debug visibility.

    All logic is deterministic — zero LLM calls.
    """
    now = time.time()
    trace = ProactiveTrace(
        idle_seconds=idle_seconds,
        proactive_count=proactive_count,
        timestamp=now,
    )

    # Record limits applied
    trace.limits_applied = {
        "max_proactive": max_proactive,
        "idle_threshold": idle_threshold,
        "cooldown": cooldown,
        "activation_threshold": activation_threshold,
    }

    # ---- Bound checks ----

    # 1. Max proactive actions per session
    if proactive_count >= max_proactive:
        trace.suppressed_reasons.append(
            f"Max proactive actions reached ({proactive_count}/{max_proactive})"
        )
        return None, trace

    # 2. Not idle long enough
    if idle_seconds < idle_threshold:
        trace.suppressed_reasons.append(
            f"Not idle enough ({idle_seconds:.0f}s < {idle_threshold:.0f}s)"
        )
        return None, trace

    # 3. Cooldown between proactive actions
    if last_proactive_at is not None:
        elapsed = now - last_proactive_at
        if elapsed < cooldown:
            trace.suppressed_reasons.append(
                f"Cooldown not elapsed ({elapsed:.0f}s < {cooldown:.0f}s)"
            )
            return None, trace

    # 4. Too tired to be proactive
    if state.energy < 0.15:
        trace.suppressed_reasons.append(
            f"Energy too low ({state.energy:.2f})"
        )
        return None, trace

    # ---- Collect and score triggers ----

    triggers = _collect_triggers(unresolved_items, active_plan, idle_seconds, state)
    trace.triggers_found = triggers

    if not triggers:
        trace.suppressed_reasons.append("No triggers found")
        return None, trace

    # Score each trigger
    scored: list[tuple[float, ProactiveTrigger]] = []
    for t in triggers:
        score = _score_trigger(t, state, person)
        scored.append((score, t))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_trigger = scored[0]

    # Check activation threshold
    if best_score < activation_threshold:
        trace.suppressed_reasons.append(
            f"Best trigger score ({best_score:.2f}) below threshold ({activation_threshold:.2f})"
        )
        return None, trace

    # ---- Select action ----

    action = _select_action(best_trigger, active_plan, state)
    trace.action_taken = action

    return action, trace
