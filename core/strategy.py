"""Deterministic response strategy selector.

Picks one of eight high-level strategies based on the social appraisal,
relationship context, person profile, and current modulator state.
No LLM calls, no storage — pure decision logic.
"""

from __future__ import annotations

from core.types import (
    AppraisalFrame,
    PersonProfile,
    RelationshipContext,
    ResponseStrategy,
)


def select_strategy(
    appraisal: AppraisalFrame,
    modulators: dict[str, float],
    person: PersonProfile | None = None,
    relationship: RelationshipContext | None = None,
) -> ResponseStrategy:
    """Choose the single best response strategy for this turn.

    Priority order (first match wins):
      1. set_boundary   — assistant-targeted attack + low trust
      2. repair         — assistant-targeted blame + moderate trust
      3. give_space     — explicit withdrawal or very low energy
      4. ground         — high arousal + mixed affect or uncertainty
      5. validate       — vulnerability present + external target
      6. reassure       — moderate vulnerability + decent bonding
      7. practical_help — action/solution request + controllable problem
      8. challenge_gently — contradictions or open loops + sufficient trust
      9. fallback       — validate for negative, practical_help for requests
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

    # 1. Protect self from abuse — attack aimed at assistant, low trust
    if appraisal.social_move == "attack" and trust < 0.4:
        return ResponseStrategy.SET_BOUNDARY

    # 2. Repair — assistant is blamed but trust isn't rock-bottom
    if (
        appraisal.targets_assistant
        and appraisal.blame > 0.5
        and trust >= 0.3
        and appraisal.social_move != "attack"
    ):
        return ResponseStrategy.REPAIR

    # 3. Give space — system is drained
    if energy < 0.2:
        return ResponseStrategy.GIVE_SPACE

    # 4. Ground — overwhelming activation or mixed signals
    if arousal > 0.75 and (appraisal.mixed_affect or certainty < 0.3):
        return ResponseStrategy.GROUND

    # 5. Validate — user is vulnerable and the issue is external
    if appraisal.vulnerability > 0.5 and not appraisal.targets_assistant:
        return ResponseStrategy.VALIDATE

    # 6. Reassure — moderate vulnerability, decent relationship
    if appraisal.vulnerability > 0.3 and bonding > 0.4:
        return ResponseStrategy.REASSURE

    # 7. Practical help — user is asking for action, problem feels controllable
    if appraisal.inferred_intent in ("seek_action", "seek_support") and appraisal.controllability > 0.5:
        return ResponseStrategy.PRACTICAL_HELP

    # 8. Gentle challenge — contradictions or unresolved loops, trust present
    if has_open_loops and trust > 0.5:
        return ResponseStrategy.CHALLENGE_GENTLY

    # Fallback: negative tone → validate; request → practical; else reassure
    if valence < 0.4:
        return ResponseStrategy.VALIDATE
    if appraisal.inferred_intent in ("seek_action", "seek_support"):
        return ResponseStrategy.PRACTICAL_HELP
    return ResponseStrategy.REASSURE


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
