"""Proactive behavior evaluation — self-initiated action.

Evaluates whether Nūr feels compelled to initiate something based on:
  - Unresolved cognitive tension (items with sufficient intensity)
  - Pending task plans (incomplete multi-step work)
  - Commitments and follow-up obligations
  - Temporal patterns (session idle time)
  - Emotionally salient unfinished matters

All logic is deterministic — zero LLM calls.
Recent action density and recovery time shape activation pressure; they are
not hard caps on whether a character may initiate.
"""

from __future__ import annotations

import time
from typing import Any

from core.character_vector import CharacterVector
from core.life_influence import LifeInfluence
from core.life_influence import derive_life_influence_from_vector
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

DEFAULT_IDLE_TRIGGER_SECONDS = 300.0
DEFAULT_RECENT_DENSITY_REFERENCE = 3
DEFAULT_RECOVERY_SECONDS = 300.0
DEFAULT_BASE_ACTIVATION = 0.4

# Intensity boosts from modulator state
_RESOLUTION_BOOST = 0.15        # high resolution → more proactive
_LOW_ENERGY_PENALTY = -0.10     # low energy → less proactive
_HIGH_BONDING_BOOST = 0.05      # high bonding → more willing to reach out
_LOW_VALENCE_BOOST = 0.05       # negative mood amplifies unfinished-business sense


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
    if idle_seconds > DEFAULT_IDLE_TRIGGER_SECONDS and state.resolution > 0.3:
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


def collect_skill_want_triggers(
    capability_gaps: list[dict[str, Any]],
    *,
    character_vector: CharacterVector | None = None,
) -> list[ProactiveTrigger]:
    """Collect proactive triggers for repeated capability gaps."""
    influence = derive_life_influence_from_vector(character_vector)
    competence_gain = max(0.0, influence.competence_pressure)
    curiosity_gain = max(0.0, influence.curiosity_pressure)
    caution_penalty = max(0.0, influence.caution_pressure) * 0.5
    triggers: list[ProactiveTrigger] = []
    for gap in capability_gaps:
        recurrence = _safe_float(gap.get("recent_recurrence"), 0.0)
        frustration = _safe_float(gap.get("frustration_intensity"), 0.0)
        gap_type = str(gap.get("gap_type") or "capability").strip() or "capability"
        intensity = 0.25 + min(0.3, recurrence * 0.06) + frustration * 0.35
        intensity += competence_gain * 0.4 + curiosity_gain * 0.25 - caution_penalty
        if intensity < 0.35:
            continue
        triggers.append(ProactiveTrigger(
            source=ProactiveTriggerSource.SKILL_WANT,
            description=f"Capability gap: {gap_type}",
            intensity=intensity,
            item_id=str(gap.get("id") or gap_type),
        ))
    return triggers


# ---------------------------------------------------------------------------
# Trigger scoring (modulator-influenced)
# ---------------------------------------------------------------------------

def _score_trigger(
    trigger: ProactiveTrigger,
    state: ModulatorState,
    person: PersonProfile,
    life_influence: LifeInfluence | None = None,
    character_vector: CharacterVector | None = None,
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

    # Negative mood amplifies the sense of unfinished business
    if state.valence < 0.3:
        score += _LOW_VALENCE_BOOST

    if character_vector is not None:
        life_influence = derive_life_influence_from_vector(character_vector)

    if life_influence is not None:
        category = _trigger_gain_category(trigger)
        score *= life_influence.proactive_gain_for(category)

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

    if trigger.source == ProactiveTriggerSource.SKILL_WANT:
        return ProactiveAction(
            action_type="suggest",
            trigger=trigger,
            message=f"I'd want a skill for this capability gap: {trigger.description[:100]}",
            rationale=f"Repeated capability gap (intensity={trigger.intensity:.2f})",
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
    density_reference: int = DEFAULT_RECENT_DENSITY_REFERENCE,
    idle_threshold: float = DEFAULT_IDLE_TRIGGER_SECONDS,
    recovery_seconds: float = DEFAULT_RECOVERY_SECONDS,
    activation_threshold: float = DEFAULT_BASE_ACTIVATION,
    life_influence: LifeInfluence | None = None,
    character_vector: CharacterVector | None = None,
    skill_want_triggers: list[ProactiveTrigger] | None = None,
) -> tuple[ProactiveAction | None, ProactiveTrace]:
    """Evaluate whether Nūr should initiate proactive behavior.

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

    # Record recovery parameters applied. These shape threshold, not hard caps.
    trace.limits_applied = {
        "recent_density_reference": density_reference,
        "idle_threshold": idle_threshold,
        "recovery_seconds": recovery_seconds,
        "base_activation": activation_threshold,
    }

    density_reference = max(1.0, float(density_reference))
    recent_density = max(0.0, proactive_count / density_reference)
    recovery_penalty = min(0.35, recent_density * 0.12)
    if last_proactive_at is not None and recovery_seconds > 0:
        elapsed = now - last_proactive_at
        recovery_penalty += max(0.0, (recovery_seconds - elapsed) / recovery_seconds) * 0.15

    # ---- State checks ----
    if idle_seconds < idle_threshold:
        trace.suppressed_reasons.append(
            f"Not idle enough ({idle_seconds:.0f}s < {idle_threshold:.0f}s)"
        )
        return None, trace

    if state.energy < 0.15:
        trace.suppressed_reasons.append(
            f"Energy too low ({state.energy:.2f})"
        )
        return None, trace

    # ---- Collect and score triggers ----

    triggers = _collect_triggers(unresolved_items, active_plan, idle_seconds, state)
    if skill_want_triggers:
        triggers.extend(skill_want_triggers)
    trace.triggers_found = triggers

    if not triggers:
        trace.suppressed_reasons.append("No triggers found")
        return None, trace

    # Score each trigger
    scored: list[tuple[float, ProactiveTrigger]] = []
    for t in triggers:
        base_score = _score_trigger(t, state, person, None)
        score = _score_trigger(
            t,
            state,
            person,
            life_influence,
            character_vector,
        )
        delta = round(score - base_score, 6)
        if delta != 0.0:
            key = f"{t.source.value if hasattr(t.source, 'value') else str(t.source)}:{t.item_id or t.description[:40]}"
            trace.life_influence_score_deltas[key] = delta
        scored.append((score, t))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_trigger = scored[0]

    # Check activation threshold
    effective_threshold = min(1.0, max(0.0, activation_threshold + recovery_penalty))
    trace.limits_applied["effective_activation"] = effective_threshold
    if best_score < effective_threshold:
        trace.suppressed_reasons.append(
            f"Best trigger score ({best_score:.2f}) below threshold ({effective_threshold:.2f})"
        )
        return None, trace

    # ---- Select action ----

    action = _select_action(best_trigger, active_plan, state)
    trace.action_taken = action

    return action, trace


def _trigger_gain_category(trigger: ProactiveTrigger) -> str:
    if trigger.source == ProactiveTriggerSource.UNRESOLVED_ITEM:
        return "unresolved_item"
    if trigger.source == ProactiveTriggerSource.COMMITMENT:
        return "commitment"
    if trigger.source == ProactiveTriggerSource.PENDING_TASK:
        return "pending_task"
    if trigger.source == ProactiveTriggerSource.SKILL_WANT:
        return "skill_gap"
    if trigger.source == ProactiveTriggerSource.TEMPORAL:
        return "temporal"
    if trigger.source == ProactiveTriggerSource.EMOTIONAL_SALIENCE:
        return "emotional_salience"
    return "internal"


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
