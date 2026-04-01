"""Tests for defense mechanisms — v2 Phase 4."""

import pytest

from core.types import (
    DefenseActivation,
    DefenseEvent,
    ModulatorState,
    PersonProfile,
    SelfProfile,
    TopicProfile,
)
from core.defense_mechanisms import (
    DEFENSE_INSTRUCTIONS,
    DefenseMechanism,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _person(trust: float = 0.5, bonding_delta: float = 0.0) -> PersonProfile:
    from core.types import BaselineShift
    return PersonProfile(
        person_id="test",
        trust=trust,
        baseline_shift=BaselineShift(bonding=bonding_delta),
    )


def _self(
    maturity: float = 0.0,
    flaws: list[str] | None = None,
    defense_log: list[DefenseEvent] | None = None,
) -> SelfProfile:
    return SelfProfile(
        maturity_score=maturity,
        flaws=flaws or [],
        defense_log=defense_log or [],
    )


def _high_intensity_state() -> ModulatorState:
    """State that produces raw_intensity above default comfort threshold."""
    return ModulatorState(arousal=0.9, valence=0.1)


def _low_intensity_state() -> ModulatorState:
    """State that produces raw_intensity below comfort threshold."""
    return ModulatorState(arousal=0.3, valence=0.5)


# ---------------------------------------------------------------------------
# No defense when below comfort threshold
# ---------------------------------------------------------------------------

class TestNoDefenseNeeded:
    def test_low_intensity_no_defense(self):
        dm = DefenseMechanism()
        output, activation = dm.evaluate(
            "I'm doing fine.",
            _low_intensity_state(),
            _self(),
            _person(),
        )
        assert activation is None
        assert output == "I'm doing fine."

    def test_high_trust_raises_comfort_threshold(self):
        """High trust → higher comfort → defense less likely."""
        dm = DefenseMechanism()
        # Moderate intensity that would trigger with low trust
        state = ModulatorState(arousal=0.65, valence=0.2)
        # With high trust, comfort threshold is higher
        _, activation = dm.evaluate(
            "response",
            state,
            _self(maturity=0.5),
            _person(trust=0.9),
        )
        assert activation is None

    def test_high_maturity_raises_comfort_threshold(self):
        """High maturity → higher comfort → defense less likely."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.65, valence=0.2)
        _, activation = dm.evaluate(
            "response",
            state,
            _self(maturity=0.9),
            _person(trust=0.7),
        )
        assert activation is None


# ---------------------------------------------------------------------------
# Rationalization triggers
# ---------------------------------------------------------------------------

class TestRationalization:
    def test_extreme_valence_plus_sensitive_topic(self):
        """High valence + sensitive topic → rationalization."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.8, valence=0.1)  # extreme low valence
        topics = [TopicProfile(topic="breakup", emotional_charge=0.7)]
        _, activation = dm.evaluate(
            "I'm upset about this.",
            state,
            _self(),
            _person(),
            topic_profiles=topics,
        )
        assert activation is not None
        assert activation.defense_type == "rationalization"

    def test_high_valence_also_triggers(self):
        """valence > 0.7 + sensitive topic → rationalization."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.8, valence=0.9)
        topics = [TopicProfile(topic="love", emotional_charge=0.6)]
        _, activation = dm.evaluate(
            "I feel so strongly!",
            state,
            _self(),
            _person(),
            topic_profiles=topics,
        )
        assert activation is not None
        assert activation.defense_type == "rationalization"

    def test_no_sensitive_topic_skips_rationalization(self):
        """Extreme valence but no sensitive topic → not rationalization."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.8, valence=0.1)
        # No topics or non-sensitive topics
        _, activation = dm.evaluate(
            "response",
            state,
            _self(),
            _person(trust=0.3),  # low trust → deflection instead
        )
        assert activation is not None
        assert activation.defense_type != "rationalization"

    def test_rationalization_instruction_appended(self):
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.8, valence=0.1)
        topics = [TopicProfile(topic="loss", emotional_charge=0.8)]
        output, activation = dm.evaluate(
            "I'm devastated.",
            state,
            _self(),
            _person(),
            topic_profiles=topics,
        )
        assert "Defense instruction" in output
        assert "logical" in output.lower() or "practical" in output.lower()


# ---------------------------------------------------------------------------
# Deflection triggers
# ---------------------------------------------------------------------------

class TestDeflection:
    def test_high_arousal_low_trust(self):
        """arousal > 0.7 + trust < 0.4 → deflection."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.9, valence=0.15)  # extreme enough to exceed comfort
        _, activation = dm.evaluate(
            "I don't want to talk about it.",
            state,
            _self(),
            _person(trust=0.2),
        )
        assert activation is not None
        assert activation.defense_type == "deflection"

    def test_deflection_needs_low_trust(self):
        """High arousal but normal trust → not deflection."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.9, valence=0.15)
        _, activation = dm.evaluate(
            "response",
            state,
            _self(),
            _person(trust=0.6),
        )
        if activation:
            assert activation.defense_type != "deflection"

    def test_deflection_instruction_appended(self):
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.9, valence=0.15)
        output, _ = dm.evaluate(
            "response",
            state,
            _self(),
            _person(trust=0.2),
        )
        assert "redirect" in output.lower()


# ---------------------------------------------------------------------------
# Minimization triggers
# ---------------------------------------------------------------------------

class TestMinimization:
    def test_over_intensity_flaw_triggers(self):
        """Self-profile flaw 'over_intensity' → minimization."""
        dm = DefenseMechanism()
        state = _high_intensity_state()
        _, activation = dm.evaluate(
            "I'm FURIOUS!",
            state,
            _self(flaws=["over_intensity"]),
            _person(trust=0.6),  # trust high enough to skip deflection
        )
        assert activation is not None
        assert activation.defense_type == "minimization"

    def test_intense_flaw_triggers(self):
        """Flaw 'intense' also counts."""
        dm = DefenseMechanism()
        state = _high_intensity_state()
        _, activation = dm.evaluate(
            "response",
            state,
            _self(flaws=["intense"]),
            _person(trust=0.6),
        )
        assert activation is not None
        assert activation.defense_type == "minimization"

    def test_repeated_minimization_creates_pattern(self):
        """3+ recent minimizations in defense log → over-intensity pattern."""
        import time as _time
        log = [
            DefenseEvent(_time.time(), "minimization", 0.7, 0.4, 0.3)
            for _ in range(4)
        ]
        dm = DefenseMechanism()
        state = _high_intensity_state()
        _, activation = dm.evaluate(
            "response",
            state,
            _self(defense_log=log),
            _person(trust=0.6),
        )
        assert activation is not None
        assert activation.defense_type == "minimization"

    def test_minimization_is_default(self):
        """When no specific trigger matches, minimization is the default."""
        dm = DefenseMechanism()
        # Arousal not extreme enough for deflection/projection, no topic
        state = ModulatorState(arousal=0.7, valence=0.3)
        _, activation = dm.evaluate(
            "response",
            state,
            _self(maturity=0.5),  # maturity too high for projection
            _person(trust=0.6),
        )
        if activation:
            assert activation.defense_type == "minimization"

    def test_minimization_instruction_appended(self):
        dm = DefenseMechanism()
        state = _high_intensity_state()
        output, _ = dm.evaluate(
            "I'm really upset",
            state,
            _self(flaws=["over_intensity"]),
            _person(trust=0.6),
        )
        assert "hedging" in output.lower() or "understate" in output.lower()


# ---------------------------------------------------------------------------
# Projection triggers
# ---------------------------------------------------------------------------

class TestProjection:
    def test_high_arousal_low_maturity(self):
        """arousal > 0.6 + maturity < 0.3 → projection."""
        dm = DefenseMechanism()
        # Need raw intensity > comfort. trust=0.5, mat=0.1 → comfort ≈ 0.57
        # arousal=0.85, valence=0.15 → raw ≈ 0.85*0.6 + 0.7*0.4 = 0.79
        state = ModulatorState(arousal=0.85, valence=0.15)
        _, activation = dm.evaluate(
            "This is stressful.",
            state,
            _self(maturity=0.1),
            _person(trust=0.5),  # trust moderate so deflection won't fire
        )
        assert activation is not None
        assert activation.defense_type == "projection"

    def test_projection_needs_low_maturity(self):
        """High maturity blocks projection."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.85, valence=0.15)
        _, activation = dm.evaluate(
            "response",
            state,
            _self(maturity=0.5),
            _person(trust=0.5),
        )
        if activation:
            assert activation.defense_type != "projection"

    def test_projection_instruction_appended(self):
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.85, valence=0.15)
        output, _ = dm.evaluate(
            "I'm stressed",
            state,
            _self(maturity=0.1),
            _person(trust=0.5),
        )
        assert "OTHER person" in output


# ---------------------------------------------------------------------------
# Suppression factor degrades with maturity (monotonically)
# ---------------------------------------------------------------------------

class TestSuppressionDegrades:
    def test_suppression_decreases_with_maturity(self):
        """Spec: suppression decreases monotonically as maturity grows."""
        dm = DefenseMechanism()
        for defense_type in ["rationalization", "deflection", "minimization", "projection"]:
            prev_delta = float("inf")
            for maturity_10x in range(0, 11):
                maturity = maturity_10x / 10.0
                factor = dm._suppression_factor(defense_type, maturity)
                # Factor is the fraction expressed — it should INCREASE with maturity
                # So suppression_delta (raw - expressed) should DECREASE
                # Since raw is constant, expressed = raw * factor increases
                # meaning suppression_delta = raw * (1 - factor) decreases
                suppression = 1.0 - factor
                assert suppression <= prev_delta, (
                    f"{defense_type} at maturity {maturity}: "
                    f"suppression {suppression} > previous {prev_delta}"
                )
                prev_delta = suppression

    def test_maturity_zero_full_strength(self):
        """At maturity 0, defenses at base suppression."""
        dm = DefenseMechanism()
        # Rationalization base = 0.4
        factor = dm._suppression_factor("rationalization", 0.0)
        assert factor == pytest.approx(0.4, abs=0.01)

    def test_maturity_one_half_strength(self):
        """At maturity 1.0, defenses at ~50% strength."""
        dm = DefenseMechanism()
        # Rationalization: 0.4 + (1-0.4)*1.0*0.5 = 0.4 + 0.3 = 0.7
        factor = dm._suppression_factor("rationalization", 1.0)
        assert factor == pytest.approx(0.7, abs=0.01)
        # Deflection: 0.2 + (1-0.2)*1.0*0.5 = 0.2 + 0.4 = 0.6
        factor = dm._suppression_factor("deflection", 1.0)
        assert factor == pytest.approx(0.6, abs=0.01)

    def test_never_fully_gone(self):
        """Even at maturity 1.0, suppression factor < 1.0 (never zero suppression)."""
        dm = DefenseMechanism()
        for defense_type in ["rationalization", "deflection", "minimization", "projection"]:
            factor = dm._suppression_factor(defense_type, 1.0)
            assert factor < 1.0, f"{defense_type} reached full transparency at maturity 1.0"

    def test_expressed_less_than_raw(self):
        """expressed_intensity should always be less than raw_intensity."""
        dm = DefenseMechanism()
        state = _high_intensity_state()
        _, activation = dm.evaluate(
            "response",
            state,
            _self(),
            _person(trust=0.2),  # low trust → will trigger defense
        )
        assert activation is not None
        assert activation.expressed_intensity < activation.raw_intensity
        assert activation.suppression_delta > 0


# ---------------------------------------------------------------------------
# Defense logging
# ---------------------------------------------------------------------------

class TestDefenseLogging:
    def test_defense_logged_in_self_profile(self):
        """Each defense activation is stored in self-profile defense_log."""
        dm = DefenseMechanism()
        self_p = _self()
        assert len(self_p.defense_log) == 0
        dm.evaluate(
            "I'm angry!",
            _high_intensity_state(),
            self_p,
            _person(trust=0.2),
        )
        assert len(self_p.defense_log) == 1
        event = self_p.defense_log[0]
        assert event.defense_type in DEFENSE_INSTRUCTIONS
        assert event.raw_intensity > 0
        assert event.suppression_delta > 0

    def test_no_defense_no_log(self):
        """When no defense fires, nothing is logged."""
        dm = DefenseMechanism()
        self_p = _self()
        dm.evaluate(
            "All good.",
            _low_intensity_state(),
            self_p,
            _person(),
        )
        assert len(self_p.defense_log) == 0

    def test_multiple_defenses_accumulate(self):
        dm = DefenseMechanism()
        self_p = _self()
        for _ in range(5):
            dm.evaluate(
                "intense!",
                _high_intensity_state(),
                self_p,
                _person(trust=0.2),
            )
        assert len(self_p.defense_log) == 5

    def test_logged_event_has_timestamp(self):
        import time as _time
        dm = DefenseMechanism()
        self_p = _self()
        before = _time.time()
        dm.evaluate(
            "response",
            _high_intensity_state(),
            self_p,
            _person(trust=0.2),
        )
        after = _time.time()
        assert before <= self_p.defense_log[0].timestamp <= after


# ---------------------------------------------------------------------------
# DefenseActivation structure
# ---------------------------------------------------------------------------

class TestDefenseActivation:
    def test_activation_fields(self):
        dm = DefenseMechanism()
        _, activation = dm.evaluate(
            "response",
            _high_intensity_state(),
            _self(),
            _person(trust=0.2),
        )
        assert activation is not None
        assert isinstance(activation.defense_type, str)
        assert isinstance(activation.raw_intensity, float)
        assert isinstance(activation.expressed_intensity, float)
        assert isinstance(activation.suppression_delta, float)
        assert isinstance(activation.reason, str)
        assert activation.reason != ""

    def test_suppression_delta_is_gap(self):
        """suppression_delta = raw - expressed."""
        dm = DefenseMechanism()
        _, activation = dm.evaluate(
            "response",
            _high_intensity_state(),
            _self(),
            _person(trust=0.2),
        )
        assert activation is not None
        expected_delta = activation.raw_intensity - activation.expressed_intensity
        assert activation.suppression_delta == pytest.approx(expected_delta, abs=0.001)


# ---------------------------------------------------------------------------
# Comfort threshold
# ---------------------------------------------------------------------------

class TestComfortThreshold:
    def test_comfort_clamped_low(self):
        """Comfort threshold never below 0.3."""
        dm = DefenseMechanism()
        threshold = dm._comfort_threshold(
            _self(maturity=0.0),
            _person(trust=0.0),
        )
        assert threshold >= 0.3

    def test_comfort_clamped_high(self):
        """Comfort threshold never above 0.95."""
        dm = DefenseMechanism()
        threshold = dm._comfort_threshold(
            _self(maturity=1.0),
            _person(trust=1.0, bonding_delta=1.0),
        )
        assert threshold <= 0.95

    def test_comfort_increases_with_trust(self):
        dm = DefenseMechanism()
        low = dm._comfort_threshold(_self(), _person(trust=0.1))
        high = dm._comfort_threshold(_self(), _person(trust=0.9))
        assert high > low

    def test_comfort_increases_with_maturity(self):
        dm = DefenseMechanism()
        low = dm._comfort_threshold(_self(maturity=0.0), _person())
        high = dm._comfort_threshold(_self(maturity=0.9), _person())
        assert high > low


# ---------------------------------------------------------------------------
# Raw intensity
# ---------------------------------------------------------------------------

class TestRawIntensity:
    def test_calm_state_low_intensity(self):
        dm = DefenseMechanism()
        raw = dm._calculate_raw_intensity(ModulatorState(arousal=0.3, valence=0.5))
        assert raw < 0.4

    def test_high_arousal_extreme_valence_high_intensity(self):
        dm = DefenseMechanism()
        raw = dm._calculate_raw_intensity(ModulatorState(arousal=0.9, valence=0.1))
        assert raw > 0.7

    def test_intensity_clamped(self):
        dm = DefenseMechanism()
        raw = dm._calculate_raw_intensity(ModulatorState(arousal=1.0, valence=0.0))
        assert 0.0 <= raw <= 1.0
