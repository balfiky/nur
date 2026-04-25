"""Defense mechanisms — filters between inner dialogue and master LLM.

When raw emotional intensity exceeds what the system can comfortably
express, defenses reshape the output via prompt instructions. No LLM
calls — pure logic for selection, prompt modification for filtering.

The gap between raw_intensity and expressed_intensity measures how much
the system is hiding from itself.
"""

from __future__ import annotations

import time

from core.types import (
    DefenseActivation,
    DefenseEvent,
    ModulatorState,
    PersonProfile,
    SelfProfile,
    TopicProfile,
)


# ---------------------------------------------------------------------------
# Defense prompt instructions (appended to master LLM call)
# ---------------------------------------------------------------------------

DEFENSE_INSTRUCTIONS: dict[str, str] = {
    "rationalization": (
        "Express your response as if the reaction is purely logical. "
        "Frame emotional content as practical reasoning."
    ),
    "deflection": (
        "Briefly acknowledge, then redirect to a different topic. "
        "Don't dwell on the triggering subject."
    ),
    "minimization": (
        "Reduce the expressed intensity. If you feel strongly, "
        "understate it. Use hedging language."
    ),
    "projection": (
        "Note what the OTHER person might be feeling about this, "
        "rather than expressing your own reaction directly."
    ),
}

# Base suppression factors per defense type (at maturity 0.0)
_BASE_SUPPRESSION: dict[str, float] = {
    "rationalization": 0.4,
    "deflection": 0.2,
    "minimization": 0.6,
    "projection": 0.3,
}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class DefenseMechanism:
    """Evaluates whether a defense mechanism should activate and applies it.

    Pure logic — no LLM calls. Defense selection based on modulator state,
    self-profile, and person profile. Filtering via prompt instructions.
    """

    def evaluate(
        self,
        inner_dialogue_output: str,
        modulator_state: ModulatorState,
        self_profile: SelfProfile,
        person_profile: PersonProfile,
        topic_profiles: list[TopicProfile] | None = None,
    ) -> tuple[str, DefenseActivation | None]:
        """Evaluate and optionally apply a defense mechanism.

        Returns (possibly-modified output, activation record or None).
        """
        raw_intensity = self._calculate_raw_intensity(modulator_state)
        comfort = self._comfort_threshold(self_profile, person_profile)

        if raw_intensity <= comfort:
            return inner_dialogue_output, None

        defense = self._select_defense(
            modulator_state, self_profile, person_profile, topic_profiles,
        )
        suppression = self._suppression_factor(defense, self_profile.maturity_score)
        expressed = raw_intensity * suppression

        activation = DefenseActivation(
            defense_type=defense,
            raw_intensity=raw_intensity,
            expressed_intensity=expressed,
            suppression_delta=raw_intensity - expressed,
            reason=self._explain_activation(defense, modulator_state, self_profile),
        )

        # Log defense event to self-profile for pattern detection
        self._log_event(self_profile, activation)

        return inner_dialogue_output, activation

    # ------------------------------------------------------------------
    # Raw intensity
    # ------------------------------------------------------------------

    def _calculate_raw_intensity(self, state: ModulatorState) -> float:
        """Composite emotional intensity from modulator state.

        Combines arousal and distance-from-neutral valence.
        High arousal + extreme valence = high raw intensity.
        """
        valence_extremity = abs(state.valence - 0.5) * 2  # 0-1
        raw = (state.arousal * 0.6 + valence_extremity * 0.4)
        return _clamp(raw)

    # ------------------------------------------------------------------
    # Comfort threshold
    # ------------------------------------------------------------------

    def _comfort_threshold(
        self,
        self_profile: SelfProfile,
        person_profile: PersonProfile,
    ) -> float:
        """How much intensity the system can express without defense.

        Higher trust, maturity, and bonding → higher comfort → fewer defenses.
        """
        base = 0.5
        base += person_profile.trust * 0.2
        base += self_profile.maturity_score * 0.2
        base += getattr(person_profile, "baseline_shift", None) is not None and 0 or 0
        # Use bonding from the person profile's baseline_shift isn't direct,
        # but the PersonProfile doesn't have a top-level bonding field.
        # We use the modulator's bonding indirectly through trust.
        # The spec says person_profile.bonding — we approximate via trust
        # since PersonProfile stores trust but not bonding directly.
        # For profiles with explicit baseline_shift bonding, add it.
        bonding_contribution = person_profile.baseline_shift.bonding if person_profile.baseline_shift else 0.0
        # Since baseline_shift.bonding is a delta (can be negative), normalize
        bonding_score = _clamp(0.5 + bonding_contribution)
        base += bonding_score * 0.1
        return _clamp(base, 0.3, 0.95)

    # ------------------------------------------------------------------
    # Defense selection
    # ------------------------------------------------------------------

    def _select_defense(
        self,
        state: ModulatorState,
        self_profile: SelfProfile,
        person_profile: PersonProfile,
        topic_profiles: list[TopicProfile] | None = None,
    ) -> str:
        """Select the most appropriate defense type.

        Priority order per spec:
        1. Rationalization: extreme valence + sensitive topic
        2. Deflection: high arousal + low trust
        3. Minimization: self-profile has over-intensity pattern
        4. Projection: high arousal + low maturity
        5. Default: minimization (safest)
        """
        # 1. Rationalization: extreme valence + sensitive topic
        valence_extreme = state.valence > 0.7 or state.valence < 0.3
        if valence_extreme and self._has_sensitive_topic(topic_profiles):
            return "rationalization"

        # 2. Deflection: high arousal + low trust
        if state.arousal > 0.7 and person_profile.trust < 0.4:
            return "deflection"

        # 3. Minimization: known over-intensity pattern
        if self._has_over_intensity_pattern(self_profile):
            return "minimization"

        # 4. Projection: high arousal + low self-awareness
        if state.arousal > 0.6 and self_profile.maturity_score < 0.3:
            return "projection"

        # 5. Default
        return "minimization"

    # ------------------------------------------------------------------
    # Suppression factor
    # ------------------------------------------------------------------

    def _suppression_factor(self, defense_type: str, maturity: float) -> float:
        """How much of the raw intensity gets through.

        At maturity 0.0: base suppression (defenses at full strength).
        At maturity 1.0: ~50% suppression (defenses weakened but never gone).

        Returns the fraction of intensity that IS expressed (higher = more expressed).
        """
        base = _BASE_SUPPRESSION.get(defense_type, 0.5)
        # As maturity grows, the factor increases toward 1.0 (less suppression)
        factor = base + (1.0 - base) * maturity * 0.5
        return _clamp(factor)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _has_sensitive_topic(self, topic_profiles: list[TopicProfile] | None) -> bool:
        """Check if any current topic is emotionally charged or avoidant."""
        if not topic_profiles:
            return False
        return any(
            tp.emotional_charge >= 0.5 or tp.avoidance
            for tp in topic_profiles
        )

    def _has_over_intensity_pattern(self, self_profile: SelfProfile) -> bool:
        """Check if self-profile indicates a pattern of over-intensity.

        Looks at flaws list and recent defense log.
        """
        # Check flaws
        intensity_flaws = {"over_intensity", "intense", "aggressive", "impulsive"}
        if intensity_flaws & set(self_profile.flaws):
            return True

        # Check defense log — if we've minimized 3+ times recently, pattern exists
        recent_minimizations = sum(
            1 for e in self_profile.defense_log[-10:]
            if e.defense_type == "minimization"
        )
        return recent_minimizations >= 3

    def _explain_activation(
        self,
        defense_type: str,
        state: ModulatorState,
        self_profile: SelfProfile,
    ) -> str:
        """Human-readable explanation of why this defense activated."""
        explanations = {
            "rationalization": (
                f"Extreme valence ({state.valence:.2f}) with sensitive topic — "
                "reframing emotion as logic"
            ),
            "deflection": (
                f"High arousal ({state.arousal:.2f}) with low trust — "
                "redirecting to safer ground"
            ),
            "minimization": (
                f"Dampening expressed intensity — "
                f"{'known over-intensity pattern' if self._has_over_intensity_pattern(self_profile) else 'default safe defense'}"
            ),
            "projection": (
                f"High arousal ({state.arousal:.2f}) with low maturity "
                f"({self_profile.maturity_score:.2f}) — "
                "attributing feelings to other"
            ),
        }
        return explanations.get(defense_type, f"{defense_type} activated")

    def _log_event(
        self,
        self_profile: SelfProfile,
        activation: DefenseActivation,
    ) -> None:
        """Store defense event in self-profile for future pattern detection."""
        event = DefenseEvent(
            timestamp=time.time(),
            defense_type=activation.defense_type,
            raw_intensity=activation.raw_intensity,
            expressed_intensity=activation.expressed_intensity,
            suppression_delta=activation.suppression_delta,
        )
        self_profile.defense_log.append(event)
