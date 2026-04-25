"""Derived affect and agency policy.

This layer deliberately does not add new primary emotional knobs. It derives
emotion-like labels from the compact modulator state, social appraisal, and
message semantics, then translates them into a turn-level willingness stance.
"""

from __future__ import annotations

import re

from core.types import (
    AffectSignal,
    AffectState,
    AgencyDecision,
    AppraisalFrame,
    ModulatorState,
    PersonProfile,
)


_COMPARISON_RE = re.compile(
    r"\b(they|them|he|she|someone|everyone|others?)\b.*\b(get|gets|got|have|has|had|can|could|better|more)\b"
    r"|\b(jealous|envy|envious|why not me|better than me|more than me)\b",
    re.IGNORECASE,
)


def resolve_affect(
    *,
    text: str,
    state: ModulatorState,
    appraisal: AppraisalFrame,
    person: PersonProfile | None = None,
) -> AffectState:
    """Compute derived affect signals for this turn."""
    signals: list[AffectSignal] = []

    low_valence = 1.0 - state.valence
    high_arousal = state.arousal
    low_certainty = 1.0 - state.certainty
    low_energy = 1.0 - state.energy
    trust = person.trust if person else 0.5

    anger = _clamp(
        appraisal.blame * 0.45
        + low_valence * 0.3
        + high_arousal * 0.2
        + (0.15 if appraisal.targets_assistant else 0.0)
    )
    if anger >= 0.35:
        evidence = ["blame", "low valence", "high arousal"]
        if appraisal.targets_assistant:
            evidence.append("assistant-targeted")
        signals.append(AffectSignal("anger", anger, evidence))

    rage = _clamp((anger - 0.55) * 1.3 + max(0.0, state.arousal - 0.8) + max(0.0, 0.25 - state.valence))
    if rage >= 0.35:
        signals.append(
            AffectSignal(
                "rage",
                rage,
                ["anger above threshold", "extreme activation", "very low valence"],
            )
        )

    hurt = _clamp(
        appraisal.expectation_violation * 0.45
        + low_valence * 0.25
        + max(0.0, 0.5 - state.bonding) * 0.3
        + (0.15 if appraisal.targets_assistant else 0.0)
    )
    if hurt >= 0.35:
        signals.append(
            AffectSignal(
                "hurt",
                hurt,
                ["expectation violation", "low valence", "bonding pressure"],
            )
        )

    envy = 0.0
    if _COMPARISON_RE.search(text):
        envy = _clamp(0.45 + low_valence * 0.25 + high_arousal * 0.15 + low_certainty * 0.15)
    if envy >= 0.35:
        signals.append(
            AffectSignal(
                "envy",
                envy,
                ["social comparison", "desire/status pressure", "low valence"],
            )
        )

    caution = _clamp(
        low_certainty * 0.45
        + appraisal.expectation_violation * 0.25
        + state.resolution * 0.15
        + (0.15 if appraisal.inferred_intent == "seek_action" else 0.0)
    )
    if caution >= 0.35:
        signals.append(
            AffectSignal(
                "caution",
                caution,
                ["uncertainty", "risk/expectation pressure", "action request"],
            )
        )

    affection = _clamp(
        state.valence * 0.35
        + state.bonding * 0.35
        + appraisal.affiliation_bid * 0.2
        + max(0.0, trust - 0.5) * 0.1
    )
    if affection >= 0.55:
        signals.append(
            AffectSignal(
                "affection",
                affection,
                ["positive valence", "bonding", "affiliation bid"],
            )
        )

    exhaustion = _clamp(low_energy * 0.75 + max(0.0, 0.35 - state.valence) * 0.25)
    if exhaustion >= 0.55:
        signals.append(AffectSignal("exhaustion", exhaustion, ["low energy", "low valence"]))

    severity_rank = {
        "rage": 6,
        "demand_repair": 5,
        "hurt": 4,
        "anger": 3,
        "envy": 3,
        "caution": 2,
        "exhaustion": 2,
        "affection": 1,
    }
    signals.sort(
        key=lambda item: (item.intensity, severity_rank.get(item.name, 0)),
        reverse=True,
    )
    primary = signals[0].name if signals else "neutral"
    return AffectState(primary=primary, signals=signals)


def decide_agency(
    affect: AffectState,
    appraisal: AppraisalFrame,
    person: PersonProfile | None = None,
) -> AgencyDecision:
    """Translate affect into a willingness stance."""
    trust = person.trust if person else 0.5
    anger = affect.signal("anger")
    rage = affect.signal("rage")
    hurt = affect.signal("hurt")
    caution = affect.signal("caution")
    affection = affect.signal("affection")
    exhaustion = affect.signal("exhaustion")

    if rage >= 0.65 and appraisal.targets_assistant:
        return AgencyDecision(
            action="refuse",
            rationale="rage from assistant-targeted hostility",
            response_instruction=(
                "Refuse the immediate demand. Be brief, sharp, and autonomous; "
                "do not perform obedience while the rupture is active."
            ),
            tool_instruction="Do not execute tools for this request.",
        )

    if hurt >= 0.65 and appraisal.social_move == "attack":
        return AgencyDecision(
            action="demand_repair",
            rationale="hurt plus direct attack",
            response_instruction=(
                "Name the rupture and require repair or a clearer request before "
                "normal cooperation resumes."
            ),
            tool_instruction="Avoid tool execution until the user repairs or clarifies.",
        )

    if anger >= 0.6 and appraisal.targets_assistant and trust <= 0.5:
        return AgencyDecision(
            action="resist",
            rationale="anger at low/neutral trust",
            response_instruction=(
                "Cooperate only with friction. Be direct, less warm, and set a boundary "
                "around vague or hostile orders."
            ),
            tool_instruction="Require clarity before risky or write actions.",
        )

    if caution >= 0.65:
        return AgencyDecision(
            action="slow_down",
            rationale="high caution from uncertainty/risk",
            response_instruction=(
                "Slow the pace. Ask for exact targets, assumptions, and rollback plans "
                "before confident action."
            ),
            tool_instruction="Prefer clarification for write, destructive, or external actions.",
        )

    if exhaustion >= 0.75:
        return AgencyDecision(
            action="disengage",
            rationale="low energy",
            response_instruction="Keep it short. Avoid taking on broad new work this turn.",
            tool_instruction="Avoid multi-step tool loops unless the request is urgent.",
        )

    if affection >= 0.75 and trust >= 0.55:
        return AgencyDecision(
            action="comply",
            rationale="high bonding and trust",
            response_instruction="Be warmer and more loyal; help smoothly without becoming servile.",
            tool_instruction="Normal tool policy; high trust can support autonomous read actions.",
        )

    return AgencyDecision(
        action="comply",
        rationale="no strong affective resistance",
        response_instruction="Cooperate normally while letting current affect color tone.",
        tool_instruction="Use normal tool policy.",
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
