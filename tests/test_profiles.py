"""Tests for entity profiles — Phase 3."""

import pytest

from core.types import BaselineShift, PersonProfile, SelfProfile, TopicProfile
from core.profiles.base import Observation, ProfileStore, PRIMACY_DEFAULT
from core.profiles.person import (
    PRIMACY_INTERACTION_THRESHOLD,
    TRUST_NEGATIVE_DELTA,
    TRUST_POSITIVE_DELTA,
    PersonProfileManager,
)
from core.profiles.self_model import (
    SELF_ENTITY_ID,
    STRENGTH_THRESHOLD,
    SelfProfileManager,
)
from core.profiles.topic import (
    AVOIDANCE_CHARGE_THRESHOLD,
    CHARGE_NEGATIVE_DELTA,
    CHARGE_POSITIVE_DELTA,
    CONFLICT_AVOIDANCE_THRESHOLD,
    TopicProfileManager,
)
from core.profiles.contradiction import (
    CONTRADICTION_THRESHOLD,
    ContradictionDetector,
)


# =========================================================================
# ProfileStore (base mechanism)
# =========================================================================

class TestProfileStore:
    def test_record_and_retrieve_observation(self):
        store = ProfileStore()
        obs = Observation(entity_id="alice", trait="patient", value=0.8)
        row_id = store.record_observation(obs)
        assert row_id is not None

        retrieved = store.get_observations("alice")
        assert len(retrieved) == 1
        assert retrieved[0].trait == "patient"
        assert retrieved[0].value == 0.8
        store.close()

    def test_observations_ordered_newest_first(self):
        store = ProfileStore()
        for i in range(5):
            store.record_observation(Observation(
                entity_id="bob", trait="calm", value=0.5 + i * 0.1,
                timestamp=1000.0 + i,
            ))
        obs = store.get_observations("bob")
        assert obs[0].timestamp > obs[-1].timestamp
        store.close()

    def test_observation_limit(self):
        store = ProfileStore()
        for i in range(30):
            store.record_observation(Observation(
                entity_id="charlie", trait="trait", value=0.5,
            ))
        obs = store.get_observations("charlie", limit=10)
        assert len(obs) == 10
        store.close()

    def test_observations_per_entity_isolated(self):
        store = ProfileStore()
        store.record_observation(Observation(entity_id="a", trait="x", value=0.5))
        store.record_observation(Observation(entity_id="b", trait="y", value=0.5))
        assert len(store.get_observations("a")) == 1
        assert len(store.get_observations("b")) == 1
        store.close()

    def test_extract_traits_basic(self):
        store = ProfileStore()
        for _ in range(3):
            store.record_observation(Observation(
                entity_id="dave", trait="patient", value=0.8,
            ))
        traits = store.extract_traits("dave")
        assert "patient" in traits
        assert traits["patient"] == pytest.approx(0.8, abs=0.01)
        store.close()

    def test_extract_traits_primacy_bias(self):
        store = ProfileStore()
        # Primacy observation (high value, weighted more)
        store.record_observation(Observation(
            entity_id="eve", trait="warm", value=0.9, is_primacy=True,
        ))
        # Later observation (lower value)
        store.record_observation(Observation(
            entity_id="eve", trait="warm", value=0.3,
        ))
        traits = store.extract_traits("eve", primacy_weight=0.8)
        # With primacy_weight=0.8: primacy gets 1.0, non-primacy gets 0.8
        # Scores: [0.9*1.0, 0.3*0.8] = [0.9, 0.24], avg = 0.57
        # Higher than if primacy were dampened (old bug)
        assert "warm" in traits
        assert traits["warm"] > 0.5, "Primacy observation should pull score above 0.5"
        store.close()

    def test_extract_traits_empty(self):
        store = ProfileStore()
        traits = store.extract_traits("nobody")
        assert traits == {}
        store.close()

    def test_get_observations_for_trait(self):
        store = ProfileStore()
        store.record_observation(Observation(entity_id="f", trait="calm", value=0.7))
        store.record_observation(Observation(entity_id="f", trait="blunt", value=0.6))
        store.record_observation(Observation(entity_id="f", trait="calm", value=0.8))
        calm_obs = store.get_observations_for_trait("f", "calm")
        assert len(calm_obs) == 2
        store.close()

    def test_observation_count(self):
        store = ProfileStore()
        for i in range(7):
            store.record_observation(Observation(
                entity_id="g", trait="trait", value=0.5,
            ))
        assert store.observation_count("g") == 7
        store.close()


# =========================================================================
# PersonProfileManager
# =========================================================================

class TestPersonProfileManager:
    def _make_manager(self):
        store = ProfileStore()
        mgr = PersonProfileManager(store)
        return store, mgr

    def test_get_or_create_new(self):
        store, mgr = self._make_manager()
        profile = mgr.get_or_create("alice", name="Alice")
        assert profile.person_id == "alice"
        assert profile.name == "Alice"
        assert profile.trust == 0.5  # default
        assert profile.interaction_count == 0
        store.close()
        mgr.close()

    def test_get_or_create_existing(self):
        store, mgr = self._make_manager()
        mgr.get_or_create("alice", name="Alice")
        profile = mgr.get_or_create("alice")
        assert profile.name == "Alice"
        store.close()
        mgr.close()

    def test_save_and_reload(self):
        store, mgr = self._make_manager()
        profile = mgr.get_or_create("bob")
        profile.trust = 0.8
        profile.reliability = 0.9
        profile.stress_response = "seeks_support"
        mgr.save(profile)

        reloaded = mgr.get_or_create("bob")
        assert reloaded.trust == pytest.approx(0.8)
        assert reloaded.reliability == pytest.approx(0.9)
        assert reloaded.stress_response == "seeks_support"
        store.close()
        mgr.close()

    def test_trust_positive_slow_build(self):
        store, mgr = self._make_manager()
        mgr.get_or_create("alice")
        new_trust = mgr.update_trust("alice", valence=1.0)
        # +0.02 * 1.0 = 0.02 above baseline 0.5
        assert new_trust == pytest.approx(0.52)
        store.close()
        mgr.close()

    def test_trust_negative_fast_break(self):
        store, mgr = self._make_manager()
        mgr.get_or_create("alice")
        new_trust = mgr.update_trust("alice", valence=-1.0)
        # -0.15 * 1.0 = -0.15 below baseline 0.5
        assert new_trust == pytest.approx(0.35)
        store.close()
        mgr.close()

    def test_trust_asymmetry(self):
        store, mgr = self._make_manager()
        mgr.get_or_create("alice")

        # 10 positive interactions
        for _ in range(10):
            mgr.update_trust("alice", valence=0.8)
        trust_after_positive = mgr.get_or_create("alice").trust

        # 1 betrayal
        mgr.update_trust("alice", valence=-0.8)
        trust_after_betrayal = mgr.get_or_create("alice").trust

        # Single betrayal should wipe out most of the positive buildup
        positive_gain = trust_after_positive - 0.5
        betrayal_loss = trust_after_positive - trust_after_betrayal
        assert betrayal_loss > positive_gain * 0.5  # betrayal takes more than half
        store.close()
        mgr.close()

    def test_trust_clamped(self):
        store, mgr = self._make_manager()
        mgr.get_or_create("alice")
        # Extreme negative
        for _ in range(20):
            mgr.update_trust("alice", valence=-1.0)
        assert mgr.get_or_create("alice").trust >= 0.0

        # Extreme positive
        for _ in range(200):
            mgr.update_trust("alice", valence=1.0)
        assert mgr.get_or_create("alice").trust <= 1.0
        store.close()
        mgr.close()

    def test_primacy_flag_on_early_interactions(self):
        store, mgr = self._make_manager()
        mgr.get_or_create("charlie")

        # First N interactions should be flagged as primacy
        for i in range(PRIMACY_INTERACTION_THRESHOLD + 3):
            mgr.record_interaction("charlie", {"calm": 0.7}, context=f"interaction_{i}")

        all_obs = store.get_observations("charlie", limit=100)
        primacy_obs = [o for o in all_obs if o.is_primacy]
        non_primacy_obs = [o for o in all_obs if not o.is_primacy]
        assert len(primacy_obs) == PRIMACY_INTERACTION_THRESHOLD
        assert len(non_primacy_obs) == 3
        store.close()
        mgr.close()

    def test_primacy_weight_decays(self):
        store, mgr = self._make_manager()
        mgr.get_or_create("dave")
        initial_weight = mgr.get_or_create("dave").primacy_weight

        # Record many interactions past primacy threshold
        for i in range(PRIMACY_INTERACTION_THRESHOLD + 10):
            mgr.record_interaction("dave", {"trait": 0.5})

        decayed_weight = mgr.get_or_create("dave").primacy_weight
        assert decayed_weight < initial_weight
        assert decayed_weight >= 0.3  # floor
        store.close()
        mgr.close()

    def test_context_switching_baseline_shift(self):
        store, mgr = self._make_manager()
        shift = BaselineShift(arousal=0.1, bonding=0.2, valence=-0.05)
        mgr.set_baseline_shift("boss", shift)

        retrieved = mgr.get_baseline_shift("boss")
        assert retrieved.arousal == pytest.approx(0.1)
        assert retrieved.bonding == pytest.approx(0.2)
        assert retrieved.valence == pytest.approx(-0.05)
        store.close()
        mgr.close()

    def test_trait_extraction(self):
        store, mgr = self._make_manager()
        mgr.get_or_create("eve")
        for _ in range(5):
            mgr.record_interaction("eve", {"patient": 0.8, "warm": 0.7})

        traits = mgr.get_trait_scores("eve")
        assert "patient" in traits
        assert "warm" in traits
        assert traits["patient"] > traits["warm"]
        store.close()
        mgr.close()

    def test_all_profiles(self):
        store, mgr = self._make_manager()
        mgr.get_or_create("a", name="A")
        mgr.get_or_create("b", name="B")
        profiles = mgr.all_profiles()
        assert len(profiles) == 2
        store.close()
        mgr.close()


# =========================================================================
# SelfProfileManager
# =========================================================================

class TestSelfProfileManager:
    def _make_manager(self):
        store = ProfileStore()
        mgr = SelfProfileManager(store)
        return store, mgr

    def test_empty_profile(self):
        store, mgr = self._make_manager()
        profile = mgr.get_profile()
        assert profile.observed_traits == []
        assert profile.strengths == []
        assert profile.flaws == []
        assert profile.dissonance == 0.0
        store.close()

    def test_record_and_extract(self):
        store, mgr = self._make_manager()
        for _ in range(5):
            mgr.record_behavior("empathetic", 0.8, context="support_conversation")
        profile = mgr.get_profile()
        assert "empathetic" in profile.observed_traits
        store.close()

    def test_strengths_emerge(self):
        store, mgr = self._make_manager()
        # Consistently high positive trait → becomes a strength
        for _ in range(10):
            mgr.record_behavior("empathetic", 0.85)
        profile = mgr.get_profile()
        assert "empathetic" in profile.strengths
        store.close()

    def test_flaws_emerge(self):
        store, mgr = self._make_manager()
        # Consistently high negative trait → becomes a flaw
        for _ in range(10):
            mgr.record_behavior("blunt", 0.75)
        profile = mgr.get_profile()
        assert "blunt" in profile.flaws
        store.close()

    def test_positive_trait_not_flaw(self):
        store, mgr = self._make_manager()
        # High positive trait should NOT be classified as flaw
        for _ in range(10):
            mgr.record_behavior("patient", 0.8)
        profile = mgr.get_profile()
        assert "patient" not in profile.flaws
        assert "patient" in profile.strengths
        store.close()

    def test_triggers_emerge(self):
        store, mgr = self._make_manager()
        # Same context with high intensity repeatedly → trigger
        for _ in range(5):
            mgr.record_behavior("defensive", 0.85, context="criticism")
        profile = mgr.get_profile()
        assert "criticism" in profile.triggers
        store.close()

    def test_dissonance_low_when_consistent(self):
        store, mgr = self._make_manager()
        # Consistent behavior
        for _ in range(20):
            mgr.record_behavior("calm", 0.7)
        profile = mgr.get_profile()
        assert profile.dissonance < 0.1
        store.close()

    def test_dissonance_high_when_erratic(self):
        store, mgr = self._make_manager()
        # Build a model
        for _ in range(15):
            mgr.record_behavior("calm", 0.8)
        # Then act erratically
        for _ in range(10):
            mgr.record_behavior("calm", 0.2)
        profile = mgr.get_profile()
        # Dissonance should be elevated — recent behavior diverges from model
        assert profile.dissonance > 0.1
        store.close()

    def test_same_mechanism_as_person(self):
        """Self and person profiles share the same underlying store."""
        store = ProfileStore()
        self_mgr = SelfProfileManager(store)
        person_mgr = PersonProfileManager(store)

        self_mgr.record_behavior("patient", 0.8)
        person_mgr.record_interaction("alice", {"patient": 0.7})

        # Both should be in the same store
        self_obs = store.get_observations(SELF_ENTITY_ID)
        alice_obs = store.get_observations("alice")
        assert len(self_obs) == 1
        assert len(alice_obs) == 1
        store.close()
        person_mgr.close()

    def test_observation_count(self):
        store, mgr = self._make_manager()
        for _ in range(7):
            mgr.record_behavior("trait", 0.5)
        assert mgr.observation_count == 7
        store.close()

    def test_get_expected_traits_for_contradiction(self):
        store, mgr = self._make_manager()
        for _ in range(5):
            mgr.record_behavior("calm", 0.8)
        expected = mgr.get_expected_traits()
        assert "calm" in expected
        assert expected["calm"] == pytest.approx(0.8, abs=0.05)
        store.close()


# =========================================================================
# TopicProfileManager
# =========================================================================

class TestTopicProfileManager:
    def test_get_or_create(self):
        mgr = TopicProfileManager()
        profile = mgr.get_or_create("work")
        assert profile.topic == "work"
        assert profile.emotional_charge == 0.0
        assert profile.avoidance is False
        mgr.close()

    def test_negative_charge_builds(self):
        mgr = TopicProfileManager()
        mgr.get_or_create("work")
        for _ in range(5):
            mgr.record_negative("work", intensity=1.0)
        profile = mgr.get_or_create("work")
        assert profile.emotional_charge > 0.3
        mgr.close()

    def test_positive_reduces_charge(self):
        mgr = TopicProfileManager()
        mgr.get_or_create("work")
        # Build up charge
        for _ in range(5):
            mgr.record_negative("work", intensity=1.0)
        charged = mgr.get_or_create("work").emotional_charge

        # Positive associations should reduce it
        for _ in range(5):
            mgr.record_positive("work", intensity=1.0)
        reduced = mgr.get_or_create("work").emotional_charge
        assert reduced < charged
        mgr.close()

    def test_charge_asymmetric(self):
        mgr = TopicProfileManager()
        # Negative builds faster than positive reduces
        mgr.record_negative("topic_a", intensity=1.0)
        neg_charge = mgr.get_or_create("topic_a").emotional_charge

        mgr.get_or_create("topic_b")
        # Start from same charge as topic_a
        mgr.record_negative("topic_b", intensity=1.0)
        mgr.record_positive("topic_b", intensity=1.0)
        net_charge = mgr.get_or_create("topic_b").emotional_charge

        # After one negative + one positive, charge should still be positive
        # because negative delta (0.10) > positive delta (0.03)
        assert net_charge > 0
        mgr.close()

    def test_avoidance_from_charge(self):
        mgr = TopicProfileManager()
        mgr.get_or_create("trauma")
        # Build charge above avoidance threshold
        for _ in range(10):
            mgr.record_negative("trauma", intensity=1.0)
        profile = mgr.get_or_create("trauma")
        assert profile.avoidance is True
        mgr.close()

    def test_avoidance_from_conflicts(self):
        mgr = TopicProfileManager()
        mgr.get_or_create("politics")
        for _ in range(CONFLICT_AVOIDANCE_THRESHOLD):
            mgr.record_conflict("politics")
        profile = mgr.get_or_create("politics")
        assert profile.avoidance is True
        mgr.close()

    def test_conflict_increases_charge(self):
        mgr = TopicProfileManager()
        mgr.get_or_create("money")
        mgr.record_conflict("money")
        profile = mgr.get_or_create("money")
        assert profile.conflict_count == 1
        assert profile.emotional_charge > 0
        mgr.close()

    def test_all_profiles(self):
        mgr = TopicProfileManager()
        mgr.get_or_create("work")
        mgr.get_or_create("family")
        profiles = mgr.all_profiles()
        assert len(profiles) == 2
        mgr.close()

    def test_get_nonexistent(self):
        mgr = TopicProfileManager()
        assert mgr.get("nothing") is None
        mgr.close()

    def test_charge_clamped(self):
        mgr = TopicProfileManager()
        mgr.get_or_create("extreme")
        for _ in range(50):
            mgr.record_negative("extreme", intensity=1.0)
        profile = mgr.get_or_create("extreme")
        assert profile.emotional_charge <= 1.0

        for _ in range(200):
            mgr.record_positive("extreme", intensity=1.0)
        profile = mgr.get_or_create("extreme")
        assert profile.emotional_charge >= 0.0
        mgr.close()


# =========================================================================
# ContradictionDetector (unified for self and others)
# =========================================================================

class TestContradictionDetector:
    def _setup(self):
        store = ProfileStore()
        detector = ContradictionDetector(store)
        return store, detector

    def test_no_contradictions_when_consistent(self):
        store, detector = self._setup()
        # Build profile
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="alice", trait="patient", value=0.8,
            ))
        expected = store.extract_traits("alice")
        result = detector.detect("alice", expected)
        assert not result.has_contradictions
        assert result.overall_divergence < CONTRADICTION_THRESHOLD
        store.close()

    def test_contradiction_when_behavior_shifts(self):
        store, detector = self._setup()
        # Build profile: alice is patient
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="alice", trait="patient", value=0.8,
                timestamp=1000.0,
            ))
        expected = store.extract_traits("alice")

        # Now alice is suddenly impatient (record recent contradictory behavior)
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="alice", trait="patient", value=0.2,
            ))

        result = detector.detect("alice", expected)
        assert result.has_contradictions
        # Should flag "patient" as contradicted
        traits_flagged = [c.trait for c in result.contradictions]
        assert "patient" in traits_flagged
        store.close()

    def test_contradiction_magnitude(self):
        store, detector = self._setup()
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="bob", trait="calm", value=0.9,
                timestamp=1000.0,
            ))
        expected = store.extract_traits("bob")

        # Moderate shift
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="bob", trait="calm", value=0.4,
            ))

        result = detector.detect("bob", expected)
        assert result.has_contradictions
        signal = result.contradictions[0]
        assert signal.magnitude > 0.3
        assert signal.observed < signal.expected
        store.close()

    def test_self_contradiction_identical_mechanism(self):
        """Self-contradiction uses the exact same detection as others."""
        store, detector = self._setup()
        self_id = "__self__"

        # Build self-model
        for _ in range(10):
            store.record_observation(Observation(
                entity_id=self_id, trait="empathetic", value=0.8,
                timestamp=1000.0,
            ))
        expected = store.extract_traits(self_id)

        # Act out of character
        for _ in range(10):
            store.record_observation(Observation(
                entity_id=self_id, trait="empathetic", value=0.3,
            ))

        result = detector.detect(self_id, expected)
        assert result.has_contradictions
        assert result.contradictions[0].is_self
        assert "Self-contradiction" in result.contradictions[0].description
        store.close()

    def test_no_contradiction_within_threshold(self):
        store, detector = self._setup()
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="charlie", trait="calm", value=0.7,
                timestamp=1000.0,
            ))
        expected = store.extract_traits("charlie")

        # Slight variation — within threshold
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="charlie", trait="calm", value=0.6,
            ))

        result = detector.detect("charlie", expected)
        assert not result.has_contradictions
        store.close()

    def test_multiple_contradictions(self):
        store, detector = self._setup()
        # Profile: calm and warm
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="dave", trait="calm", value=0.9,
                timestamp=1000.0,
            ))
            store.record_observation(Observation(
                entity_id="dave", trait="warm", value=0.8,
                timestamp=1000.0,
            ))
        expected = store.extract_traits("dave")

        # Sudden shift: agitated and cold
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="dave", trait="calm", value=0.2,
            ))
            store.record_observation(Observation(
                entity_id="dave", trait="warm", value=0.2,
            ))

        result = detector.detect("dave", expected)
        assert len(result.contradictions) == 2
        assert result.overall_divergence > 0.4
        store.close()

    def test_empty_profile_no_contradictions(self):
        store, detector = self._setup()
        result = detector.detect("nobody", {})
        assert not result.has_contradictions
        assert result.overall_divergence == 0.0
        store.close()

    def test_no_recent_observations_no_contradictions(self):
        store, detector = self._setup()
        result = detector.detect("ghost", {"calm": 0.8})
        assert not result.has_contradictions
        store.close()

    def test_description_format_others(self):
        store, detector = self._setup()
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="eve", trait="reliable", value=0.9,
                timestamp=1000.0,
            ))
        expected = store.extract_traits("eve")
        for _ in range(10):
            store.record_observation(Observation(
                entity_id="eve", trait="reliable", value=0.3,
            ))

        result = detector.detect("eve", expected)
        assert result.has_contradictions
        desc = result.contradictions[0].description
        assert "eve" in desc
        assert "reliable" in desc
        assert "lower" in desc
        store.close()
