"""Deterministic response strategy selector.

Picks one of eight high-level strategies based on the social appraisal,
relationship context, person profile, and current modulator state.
No LLM calls, no storage — pure decision logic.
"""

from __future__ import annotations

from core.life_influence import LifeInfluence
from core.types import (
    AppraisalFrame,
    PersonProfile,
    RelationshipContext,
    ResponseStrategy,
    StrategyDecisionTrace,
)


def select_strategy(
    appraisal: AppraisalFrame,
    modulators: dict[str, float],
    person: PersonProfile | None = None,
    relationship: RelationshipContext | None = None,
    life_influence: LifeInfluence | None = None,
) -> ResponseStrategy:
    """Choose the single best response strategy for this turn."""
    trace = select_strategy_with_trace(
        appraisal=appraisal,
        modulators=modulators,
        person=person,
        relationship=relationship,
        life_influence=life_influence,
    )
    return ResponseStrategy(trace.selected)


def select_strategy_with_trace(
    appraisal: AppraisalFrame,
    modulators: dict[str, float],
    person: PersonProfile | None = None,
    relationship: RelationshipContext | None = None,
    life_influence: LifeInfluence | None = None,
) -> StrategyDecisionTrace:
    """Choose a response strategy and explain the deterministic rule path.

    Priority order remains first-match-wins; Life History influence only
    participates as a small open-loop tie-break before lower-priority fallbacks.
    """
    trust = person.trust if person else 0.5
    energy = modulators.get("energy", 1.0)
    arousal = modulators.get("arousal", 0.5)
    valence = modulators.get("valence", 0.5)
    certainty = modulators.get("certainty", 0.5)
    bonding = modulators.get("bonding", 0.5)

    has_open_loops = (
        relationship is not None and relationship.open_loop_count > 0
    )
    rejected: list[dict[str, object]] = []

    def evidence(**extra: object) -> dict[str, object]:
        base: dict[str, object] = {
            "social_move": appraisal.social_move,
            "targets_assistant": appraisal.targets_assistant,
            "primary_target": appraisal.primary_target,
            "inferred_intent": appraisal.inferred_intent,
            "blame": appraisal.blame,
            "vulnerability": appraisal.vulnerability,
            "trust": trust,
            "energy": energy,
            "arousal": arousal,
            "valence": valence,
            "certainty": certainty,
            "bonding": bonding,
            "has_open_loops": has_open_loops,
        }
        base.update(extra)
        return base

    def reject(rule: str, reason: str, **extra: object) -> None:
        item: dict[str, object] = {"rule": rule, "reason": reason}
        item.update(extra)
        rejected.append(item)

    def matched(strategy: ResponseStrategy, rule: str, **extra: object) -> StrategyDecisionTrace:
        prior = rejected or [{
            "rule": "no_prior_major_rule",
            "reason": "selected the first applicable major rule",
        }]
        return StrategyDecisionTrace(
            selected=strategy.value,
            matched_rule=rule,
            evidence=evidence(**extra),
            rejected_rules=prior,
        )

    # 1. Protect self from abuse — attack aimed at assistant, low/neutral trust.
    if appraisal.social_move == "attack" and trust <= 0.5:
        return matched(ResponseStrategy.SET_BOUNDARY, "assistant_targeted_attack_low_trust")
    reject(
        "assistant_targeted_attack_low_trust",
        "requires attack social move and trust <= 0.5",
        social_move=appraisal.social_move,
        trust=trust,
    )

    # 2. Repair — assistant is blamed but trust isn't rock-bottom
    if (
        appraisal.targets_assistant
        and appraisal.blame > 0.5
        and trust >= 0.3
        and appraisal.social_move != "attack"
    ):
        return matched(ResponseStrategy.REPAIR, "assistant_targeted_repair")
    reject(
        "assistant_targeted_repair",
        "requires assistant-targeted blame, trust >= 0.3, and non-attack phrasing",
        targets_assistant=appraisal.targets_assistant,
        blame=appraisal.blame,
        trust=trust,
    )

    # 2b. Apologies are explicit repair attempts. Relationship-memory open
    # loops are only available after session digestion, so in-session repairs
    # cannot depend on has_open_loops alone.
    if appraisal.social_move == "apology" and trust >= 0.2:
        return matched(ResponseStrategy.REPAIR, "apology_repair")
    reject(
        "apology_repair",
        "requires apology social move and trust >= 0.2",
        social_move=appraisal.social_move,
        trust=trust,
    )

    # 3. Give space — system is drained
    if energy < 0.2:
        return matched(ResponseStrategy.GIVE_SPACE, "low_energy_give_space")
    reject("low_energy_give_space", "requires energy < 0.2", energy=energy)

    # 4. Ground — overwhelming activation or mixed signals
    if (
        arousal > 0.75 and (appraisal.mixed_affect or certainty < 0.3)
    ) or (
        appraisal.mixed_affect and appraisal.vulnerability > 0.5 and arousal > 0.6
    ) or (
        appraisal.expectation_violation > 0.6 and arousal >= 0.65 and certainty <= 0.45
    ) or (
        appraisal.inferred_intent == "seek_action" and arousal >= 0.65 and certainty <= 0.4
    ):
        return matched(ResponseStrategy.GROUND, "overwhelm_ground")
    reject(
        "overwhelm_ground",
        "requires high arousal with mixed affect, uncertainty, expectation violation, or action confusion",
        arousal=arousal,
        mixed_affect=appraisal.mixed_affect,
        certainty=certainty,
        expectation_violation=appraisal.expectation_violation,
    )

    if (
        has_open_loops
        and life_influence is not None
        and life_influence.repair_pressure > 0.0
        and trust > 0.4
    ):
        if appraisal.targets_assistant or appraisal.social_move == "apology":
            return matched(
                ResponseStrategy.REPAIR,
                "life_repair_pressure_repair",
                repair_pressure=life_influence.repair_pressure,
            )
        if appraisal.inferred_intent not in ("seek_action", "seek_support"):
            return matched(
                ResponseStrategy.CHALLENGE_GENTLY,
                "life_repair_pressure_open_loop",
                repair_pressure=life_influence.repair_pressure,
            )
    reject(
        "life_repair_pressure_open_loop",
        "requires open loops, positive repair pressure, trust > 0.4, and no stronger support/action intent",
        open_loop_count=(relationship.open_loop_count if relationship else 0),
        repair_pressure=(life_influence.repair_pressure if life_influence else 0.0),
        trust=trust,
    )

    # 5. Validate — user is vulnerable or distressed and the issue is external
    if (
        not appraisal.targets_assistant
        and (
            appraisal.vulnerability > 0.5
            or (appraisal.social_move == "complaint" and valence < 0.45)
        )
    ):
        return matched(ResponseStrategy.VALIDATE, "external_vulnerability_validate")
    reject(
        "external_vulnerability_validate",
        "requires non-assistant target with vulnerability > 0.5 or distressed complaint",
        targets_assistant=appraisal.targets_assistant,
        vulnerability=appraisal.vulnerability,
        social_move=appraisal.social_move,
        valence=valence,
    )

    # 6. Reassure — moderate vulnerability, decent relationship
    if appraisal.vulnerability > 0.3 and bonding > 0.4:
        return matched(ResponseStrategy.REASSURE, "moderate_vulnerability_reassure")
    reject(
        "moderate_vulnerability_reassure",
        "requires vulnerability > 0.3 and bonding > 0.4",
        vulnerability=appraisal.vulnerability,
        bonding=bonding,
    )

    # 7. Practical help — user is asking for action, problem feels controllable
    if appraisal.inferred_intent in ("seek_action", "seek_support") and appraisal.controllability > 0.5:
        return matched(ResponseStrategy.PRACTICAL_HELP, "actionable_request_practical_help")
    reject(
        "actionable_request_practical_help",
        "requires action/support intent and controllability > 0.5",
        inferred_intent=appraisal.inferred_intent,
        controllability=appraisal.controllability,
    )

    # 8. Gentle challenge — contradictions or unresolved loops, trust present
    if has_open_loops and trust > 0.5:
        return matched(ResponseStrategy.CHALLENGE_GENTLY, "open_loop_challenge_gently")
    reject(
        "open_loop_challenge_gently",
        "requires open loops and trust > 0.5",
        open_loop_count=(relationship.open_loop_count if relationship else 0),
        trust=trust,
    )

    # Fallback: negative tone → validate; request → practical; else reassure
    if valence < 0.4:
        return matched(ResponseStrategy.VALIDATE, "fallback_validate")
    if appraisal.inferred_intent in ("seek_action", "seek_support"):
        return matched(ResponseStrategy.PRACTICAL_HELP, "fallback_practical_help")
    return matched(ResponseStrategy.REASSURE, "fallback_reassure")


# ---------------------------------------------------------------------------
# Strategy prompt instructions — injected into the generator system prompt
# ---------------------------------------------------------------------------

STRATEGY_INSTRUCTIONS: dict[str, str] = {
    ResponseStrategy.VALIDATE: (
        "Acknowledge what the user is feeling or experiencing."
        " Mirror their emotional state before offering anything else."
        " Do not rush to fix or reframe."
    ),
    ResponseStrategy.REASSURE: (
        "Provide comfort and emotional safety."
        " Convey that the situation is manageable and that you are present."
        " Warmth over analysis."
    ),
    ResponseStrategy.REPAIR: (
        "The relationship has taken a hit."
        " Take responsibility where appropriate, acknowledge the rupture,"
        " and signal willingness to rebuild trust."
    ),
    ResponseStrategy.GROUND: (
        "The user may be overwhelmed or confused."
        " Help them orient: name what is happening, slow the pace,"
        " offer one concrete next step."
    ),
    ResponseStrategy.GIVE_SPACE: (
        "Pull back. Keep it brief and non-intrusive."
        " Signal availability without pressure."
        " Less is more right now."
    ),
    ResponseStrategy.PRACTICAL_HELP: (
        "Focus on the actionable request."
        " Provide clear, concrete guidance."
        " Emotional coloring should support, not replace, the practical answer."
    ),
    ResponseStrategy.CHALLENGE_GENTLY: (
        "There is something worth naming — a pattern, a contradiction,"
        " or an unresolved thread."
        " Surface it with care; frame as observation, not accusation."
    ),
    ResponseStrategy.SET_BOUNDARY: (
        "The user is being hostile."
        " Respond with firm clarity: acknowledge the emotion without absorbing blame,"
        " and set a limit on what you will accept."
    ),
}
