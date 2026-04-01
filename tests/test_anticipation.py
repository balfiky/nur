"""Tests for the anticipation engine — v2 Phase 2."""

from datetime import datetime, timedelta, timezone

import pytest

from core.anticipation import (
    CONFIDENCE_GATE,
    PRE_SHIFT_SCALE,
    AnticipationEngine,
)
from core.types import (
    Anticipation,
    ModulatorState,
    PersonProfile,
    TopicProfile,
    UnresolvedItem,
)


def _person(
    trust: float = 0.5,
    volatility: float = 0.5,
    interactions: int = 10,
    stress_response: str = "unknown",
) -> PersonProfile:
    return PersonProfile(
        person_id="test",
        trust=trust,
        emotional_volatility=volatility,
        interaction_count=interactions,
        stress_response=stress_response,
    )


def _topic(
    name: str,
    charge: float = 0.0,
    avoidance: bool = False,
    conflict_count: int = 0,
) -> TopicProfile:
    return TopicProfile(
        topic=name,
        emotional_charge=charge,
        avoidance=avoidance,
        conflict_count=conflict_count,
    )


def _unresolved(
    source: str = "contradiction",
    intensity: float = 0.6,
    age_hours: float = 72.0,
    item_id: str = "u1",
) -> UnresolvedItem:
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return UnresolvedItem(
        id=item_id,
        source=source,
        description=f"test {source}",
        created_at=created,
        intensity=intensity,
        decay_rate=0.02,
    )


class TestAnticipationPredict:
    def test_no_heuristic_fires_returns_neutral(self):
        engine = AnticipationEngine()
        result = engine.predict(
            recent_messages=[],
            person_profile=_person(interactions=0),
            topic_profiles={},
            current_state=ModulatorState(),
        )
        assert result.confidence == 0.0
        assert result.predicted_emotional_tone == "neutral"
        assert result.modulator_pre_shifts == {}

    # ---- Topic trajectory ----

    def test_sensitive_topic_trajectory_fires(self):
        """Spec: 2+ mentions of sensitive topic in last 3 messages → prediction."""
        engine = AnticipationEngine()
        topics = {"breakup": _topic("breakup", charge=0.7, avoidance=True)}
        messages = [
            "I've been thinking about the breakup",
            "The breakup really hurt",
            "Anyway how are you",
        ]
        result = engine.predict(
            recent_messages=messages,
            person_profile=_person(),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )
        assert result.confidence >= CONFIDENCE_GATE
        assert "breakup" in result.predicted_topics
        assert "arousal" in result.modulator_pre_shifts
        assert result.modulator_pre_shifts["arousal"] > 0

    def test_topic_trajectory_needs_two_mentions(self):
        """Single mention is not enough to trigger."""
        engine = AnticipationEngine()
        topics = {"breakup": _topic("breakup", charge=0.7)}
        messages = ["I mentioned the breakup once"]
        result = engine.predict(
            recent_messages=messages,
            person_profile=_person(),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )
        # Should not fire — only 1 mention
        assert "breakup" not in result.predicted_topics or result.confidence < CONFIDENCE_GATE

    def test_topic_trajectory_avoidance_adds_valence_shift(self):
        """Avoidant topic adds negative valence shift."""
        engine = AnticipationEngine()
        topics = {"trauma": _topic("trauma", charge=0.8, avoidance=True)}
        messages = ["trauma is hard", "talking about trauma again"]
        result = engine.predict(
            recent_messages=messages,
            person_profile=_person(),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )
        assert result.modulator_pre_shifts.get("valence", 0) < 0

    def test_topic_trajectory_case_insensitive(self):
        engine = AnticipationEngine()
        topics = {"Breakup": _topic("Breakup", charge=0.7)}
        messages = ["the BREAKUP was hard", "still thinking about breakup"]
        result = engine.predict(
            recent_messages=messages,
            person_profile=_person(),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )
        assert "Breakup" in result.predicted_topics

    # ---- Person patterns ----

    def test_high_volatility_low_trust_predicts_conflict(self):
        engine = AnticipationEngine()
        result = engine.predict(
            recent_messages=["hey", "what's up"],
            person_profile=_person(volatility=0.8, trust=0.3),
            topic_profiles={},
            current_state=ModulatorState(),
        )
        assert result.confidence >= CONFIDENCE_GATE
        assert "conflict" in result.predicted_topics
        assert result.modulator_pre_shifts.get("arousal", 0) > 0
        assert result.modulator_pre_shifts.get("valence", 0) < 0

    def test_person_pattern_needs_interaction_history(self):
        """Less than 3 interactions → no person pattern."""
        engine = AnticipationEngine()
        result = engine.predict(
            recent_messages=["hey"],
            person_profile=_person(volatility=0.9, trust=0.2, interactions=2),
            topic_profiles={},
            current_state=ModulatorState(),
        )
        # Person heuristic should not fire
        assert "conflict" not in result.predicted_topics

    def test_volatile_person_calm_state_braces(self):
        engine = AnticipationEngine()
        result = engine.predict(
            recent_messages=["hey"],
            person_profile=_person(volatility=0.8, trust=0.6),
            topic_profiles={},
            current_state=ModulatorState(arousal=0.3),
        )
        assert result.predicted_emotional_tone == "anticipatory"
        assert result.modulator_pre_shifts.get("arousal", 0) > 0

    def test_lashes_out_pattern(self):
        engine = AnticipationEngine()
        result = engine.predict(
            recent_messages=["this is annoying"],
            person_profile=_person(stress_response="lashes_out"),
            topic_profiles={},
            current_state=ModulatorState(arousal=0.7),
        )
        assert result.predicted_emotional_tone == "bracing"

    # ---- Unresolved aging ----

    def test_unresolved_aging_fires_for_old_intense_items(self):
        """Spec: item >48h old with intensity >0.5 → prediction."""
        engine = AnticipationEngine()
        items = [_unresolved(intensity=0.7, age_hours=72)]
        result = engine.predict(
            recent_messages=[],
            person_profile=_person(),
            topic_profiles={},
            current_state=ModulatorState(),
            unresolved_items=items,
        )
        assert result.confidence >= CONFIDENCE_GATE
        assert result.modulator_pre_shifts.get("resolution", 0) > 0

    def test_unresolved_aging_ignores_young_items(self):
        """Items <48h old should not trigger."""
        engine = AnticipationEngine()
        items = [_unresolved(intensity=0.7, age_hours=24)]
        result = engine.predict(
            recent_messages=[],
            person_profile=_person(),
            topic_profiles={},
            current_state=ModulatorState(),
            unresolved_items=items,
        )
        # Should not fire from unresolved aging
        assert result.modulator_pre_shifts.get("resolution", 0) == 0

    def test_unresolved_aging_ignores_low_intensity(self):
        """Items with intensity <=0.5 should not trigger."""
        engine = AnticipationEngine()
        items = [_unresolved(intensity=0.3, age_hours=96)]
        result = engine.predict(
            recent_messages=[],
            person_profile=_person(),
            topic_profiles={},
            current_state=ModulatorState(),
            unresolved_items=items,
        )
        assert result.modulator_pre_shifts.get("resolution", 0) == 0

    def test_unresolved_aging_picks_highest_intensity(self):
        engine = AnticipationEngine()
        items = [
            _unresolved(intensity=0.6, age_hours=72, item_id="low"),
            _unresolved(intensity=0.9, age_hours=72, item_id="high"),
        ]
        result = engine.predict(
            recent_messages=[],
            person_profile=_person(),
            topic_profiles={},
            current_state=ModulatorState(),
            unresolved_items=items,
        )
        assert "test contradiction" in result.predicted_topics[0]

    # ---- Temporal patterns ----

    def test_monday_morning_stress(self):
        engine = AnticipationEngine()
        # Monday 9am UTC
        monday_9am = datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc)
        result = engine.predict(
            recent_messages=[],
            person_profile=_person(interactions=10),
            topic_profiles={},
            current_state=ModulatorState(),
            current_time=monday_9am,
        )
        assert result.predicted_emotional_tone == "monday_stress"
        assert result.modulator_pre_shifts.get("arousal", 0) > 0
        assert result.modulator_pre_shifts.get("energy", 0) < 0

    def test_late_night_fatigue(self):
        engine = AnticipationEngine()
        late = datetime(2026, 4, 1, 23, 30, tzinfo=timezone.utc)
        result = engine.predict(
            recent_messages=[],
            person_profile=_person(interactions=10),
            topic_profiles={},
            current_state=ModulatorState(),
            current_time=late,
        )
        assert result.predicted_emotional_tone == "late_night"
        assert result.modulator_pre_shifts.get("energy", 0) < 0

    def test_friday_afternoon(self):
        engine = AnticipationEngine()
        # Friday 4pm
        friday_4pm = datetime(2026, 4, 3, 16, 0, tzinfo=timezone.utc)
        result = engine.predict(
            recent_messages=[],
            person_profile=_person(interactions=10),
            topic_profiles={},
            current_state=ModulatorState(),
            current_time=friday_4pm,
        )
        assert result.predicted_emotional_tone == "winding_down"

    def test_temporal_needs_interaction_history(self):
        engine = AnticipationEngine()
        monday_9am = datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc)
        result = engine.predict(
            recent_messages=[],
            person_profile=_person(interactions=3),
            topic_profiles={},
            current_state=ModulatorState(),
            current_time=monday_9am,
        )
        # Not enough interactions for temporal pattern
        assert result.predicted_emotional_tone != "monday_stress"


class TestApplyPreShift:
    def test_shifts_applied_at_30_percent(self):
        """Spec: pre-shifts are 30% intensity."""
        engine = AnticipationEngine()
        state = ModulatorState(arousal=0.5)
        anticipation = Anticipation(
            predicted_topics=["test"],
            predicted_emotional_tone="tense",
            modulator_pre_shifts={"arousal": 0.10},
            confidence=0.6,
            basis="test",
        )
        engine.apply_pre_shift(state, anticipation)
        # 0.5 + 0.10 * 0.3 = 0.53
        assert state.arousal == pytest.approx(0.53, abs=0.001)

    def test_no_shift_below_confidence_gate(self):
        """Spec: below 0.3 confidence → no pre-shift."""
        engine = AnticipationEngine()
        state = ModulatorState(arousal=0.5)
        anticipation = Anticipation(
            predicted_topics=["test"],
            predicted_emotional_tone="tense",
            modulator_pre_shifts={"arousal": 0.10},
            confidence=0.2,  # below gate
            basis="test",
        )
        engine.apply_pre_shift(state, anticipation)
        assert state.arousal == 0.5  # unchanged

    def test_shift_exactly_at_gate_applies(self):
        engine = AnticipationEngine()
        state = ModulatorState(arousal=0.5)
        anticipation = Anticipation(
            predicted_topics=[],
            predicted_emotional_tone="tense",
            modulator_pre_shifts={"arousal": 0.10},
            confidence=0.3,  # exactly at gate
            basis="test",
        )
        engine.apply_pre_shift(state, anticipation)
        assert state.arousal > 0.5

    def test_shift_clamped_to_bounds(self):
        engine = AnticipationEngine()
        state = ModulatorState(arousal=0.98)
        anticipation = Anticipation(
            predicted_topics=[],
            predicted_emotional_tone="test",
            modulator_pre_shifts={"arousal": 1.0},
            confidence=0.6,
            basis="test",
        )
        engine.apply_pre_shift(state, anticipation)
        assert state.arousal <= 1.0

    def test_negative_shift_clamped(self):
        engine = AnticipationEngine()
        state = ModulatorState(energy=0.02)
        anticipation = Anticipation(
            predicted_topics=[],
            predicted_emotional_tone="test",
            modulator_pre_shifts={"energy": -1.0},
            confidence=0.6,
            basis="test",
        )
        engine.apply_pre_shift(state, anticipation)
        assert state.energy >= 0.0

    def test_multiple_shifts(self):
        engine = AnticipationEngine()
        state = ModulatorState(arousal=0.5, valence=0.5)
        anticipation = Anticipation(
            predicted_topics=[],
            predicted_emotional_tone="tense",
            modulator_pre_shifts={"arousal": 0.10, "valence": -0.05},
            confidence=0.6,
            basis="test",
        )
        engine.apply_pre_shift(state, anticipation)
        assert state.arousal == pytest.approx(0.53, abs=0.001)
        assert state.valence == pytest.approx(0.485, abs=0.001)

    def test_unknown_modulator_ignored(self):
        engine = AnticipationEngine()
        state = ModulatorState(arousal=0.5)
        anticipation = Anticipation(
            predicted_topics=[],
            predicted_emotional_tone="test",
            modulator_pre_shifts={"nonexistent": 0.5},
            confidence=0.6,
            basis="test",
        )
        engine.apply_pre_shift(state, anticipation)
        assert state.arousal == 0.5

    def test_pre_shift_max_impact_bounded(self):
        """Even with large delta, actual shift is ≤30% of delta."""
        engine = AnticipationEngine()
        state = ModulatorState(arousal=0.5)
        anticipation = Anticipation(
            predicted_topics=[],
            predicted_emotional_tone="test",
            modulator_pre_shifts={"arousal": 1.0},
            confidence=0.9,
            basis="test",
        )
        engine.apply_pre_shift(state, anticipation)
        actual_shift = state.arousal - 0.5
        assert actual_shift == pytest.approx(1.0 * PRE_SHIFT_SCALE, abs=0.001)


class TestHeuristicMerging:
    def test_multiple_heuristics_merge_shifts(self):
        """When multiple heuristics fire, their shifts merge additively."""
        engine = AnticipationEngine()
        monday_9am = datetime(2026, 3, 30, 9, 0, tzinfo=timezone.utc)
        topics = {"breakup": _topic("breakup", charge=0.7)}
        messages = ["thinking about breakup", "the breakup was hard"]
        result = engine.predict(
            recent_messages=messages,
            person_profile=_person(interactions=10),
            topic_profiles=topics,
            current_state=ModulatorState(),
            current_time=monday_9am,
        )
        # Both topic trajectory and temporal should fire
        # arousal should have contributions from both
        assert result.modulator_pre_shifts.get("arousal", 0) > 0.10

    def test_highest_confidence_wins_primary(self):
        """Primary prediction (topics, tone) comes from highest-confidence heuristic."""
        engine = AnticipationEngine()
        topics = {"breakup": _topic("breakup", charge=0.9, avoidance=True)}
        messages = ["breakup pain", "breakup again"]
        # Topic trajectory should have higher confidence than temporal
        result = engine.predict(
            recent_messages=messages,
            person_profile=_person(interactions=10),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )
        assert "breakup" in result.predicted_topics
