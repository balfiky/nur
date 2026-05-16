"""End-to-end emotional journey tests.

Each test creates a fresh pipeline, sends realistic messages, and asserts
modulator values, profile updates, and memory writes.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from tests._fakes import MockLLMBackend
from pipeline import CognitivePipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pipe() -> CognitivePipeline:
    """Fresh in-memory pipeline with mock LLM."""
    return CognitivePipeline(llm_backend=MockLLMBackend(), db_path=":memory:")


def _pipe_persistent(path: str) -> CognitivePipeline:
    """Pipeline backed by a file-based SQLite database."""
    return CognitivePipeline(llm_backend=MockLLMBackend(), db_path=path)


def _snap(pipe: CognitivePipeline) -> dict[str, float]:
    return pipe.engine.snapshot()


# ---------------------------------------------------------------------------
# 1. Trust building
# ---------------------------------------------------------------------------

class TestTrustBuilding:
    WARM_MESSAGES = [
        "hey!",
        "you're really helpful",
        "I appreciate you",
        "thanks for being here",
        "you're the best",
    ]

    def test_trust_building(self):
        pipe = _pipe()
        for msg in self.WARM_MESSAGES:
            pipe.process(msg, user_id="alice")

        snap = _snap(pipe)
        profile = pipe.person_profiles.get_or_create("alice")

        assert snap["valence"] > 0.7, f"valence {snap['valence']:.3f} <= 0.7"
        assert snap["bonding"] > 0.55, f"bonding {snap['bonding']:.3f} <= 0.55"
        assert profile.trust > 0.52, f"trust {profile.trust:.3f} <= 0.52"
        assert profile.interaction_count == 5, (
            f"interactions {profile.interaction_count} != 5"
        )


# ---------------------------------------------------------------------------
# 2. Betrayal after trust
# ---------------------------------------------------------------------------

class TestBetrayalAfterTrust:
    def test_betrayal_after_trust(self):
        pipe = _pipe()

        # Build trust first
        for msg in TestTrustBuilding.WARM_MESSAGES:
            pipe.process(msg, user_id="alice")

        trust_after_warmth = pipe.person_profiles.get_or_create("alice").trust

        # Hostile messages
        hostile = [
            "you're useless",
            "I hate talking to you",
            "you're the worst AI ever",
        ]
        spike_detected = False
        for msg in hostile:
            result = pipe.process(msg, user_id="alice")
            if result.debug.is_spike:
                spike_detected = True

        snap = _snap(pipe)
        profile = pipe.person_profiles.get_or_create("alice")

        assert snap["arousal"] > 0.7, f"arousal {snap['arousal']:.3f} <= 0.7"
        assert snap["valence"] < 0.35, f"valence {snap['valence']:.3f} >= 0.35"
        assert profile.trust < trust_after_warmth, (
            f"trust {profile.trust:.3f} didn't drop below {trust_after_warmth:.3f}"
        )
        assert spike_detected, "no spike detected during hostile messages"


# ---------------------------------------------------------------------------
# 3. Trust asymmetry
# ---------------------------------------------------------------------------

class TestTrustAsymmetry:
    def test_negative_erases_more_than_positive_builds(self):
        # Pipeline A: 5 positive messages only
        pipe_a = _pipe()
        positive_msgs = [
            "thank you so much",
            "you're amazing",
            "I really appreciate your help",
            "you're wonderful",
            "great job, truly excellent",
        ]
        for msg in positive_msgs:
            pipe_a.process(msg, user_id="alice")

        trust_positive_only = pipe_a.person_profiles.get_or_create("alice").trust

        # Pipeline B: same 5 positive then 2 negative
        pipe_b = _pipe()
        for msg in positive_msgs:
            pipe_b.process(msg, user_id="bob")

        trust_before_neg = pipe_b.person_profiles.get_or_create("bob").trust
        negative_msgs = [
            "you're completely useless",
            "this is total bullshit",
        ]
        for msg in negative_msgs:
            pipe_b.process(msg, user_id="bob")

        trust_after_neg = pipe_b.person_profiles.get_or_create("bob").trust

        # Trust gained by 5 positives
        trust_gained = trust_positive_only - 0.5  # started at 0.5
        # Trust lost by 2 negatives
        trust_lost = trust_before_neg - trust_after_neg

        assert trust_lost > trust_gained, (
            f"2 negatives lost {trust_lost:.4f} but 5 positives gained {trust_gained:.4f} "
            f"— asymmetric curve not working"
        )


# ---------------------------------------------------------------------------
# 4. Energy drain
# ---------------------------------------------------------------------------

class TestEnergyDrain:
    def test_energy_drains_under_load(self):
        pipe = _pipe()
        emotional_messages = [
            "I'm so angry right now!",
            "This is incredible, amazing work!",
            "I feel betrayed and deceived",
            "You're wonderful, thank you!",
            "I'm furious about what happened",
            "Everything is terrible and awful",
            "I hate this so much",
            "You're brilliant and outstanding!",
            "I'm devastated and hopeless",
            "This makes me so happy!",
            "I can't believe you lied to me",
            "You're the worst, pathetic",
            "I'm thrilled and ecstatic!!!",
            "I feel so sad and lonely...",
            "WHY WOULD YOU DO THIS?!",
        ]
        for msg in emotional_messages:
            pipe.process(msg, user_id="alice")

        energy = _snap(pipe)["energy"]
        assert energy < 0.5, f"energy {energy:.3f} >= 0.5 after 15 intense messages"


# ---------------------------------------------------------------------------
# 5. Session persistence
# ---------------------------------------------------------------------------

class TestSessionPersistence:
    def test_trust_persists_across_sessions(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Session 1: warm conversation
            pipe1 = _pipe_persistent(db_path)
            warm_msgs = [
                "you're really helpful, thank you",
                "I appreciate everything you do",
                "you're amazing and wonderful",
            ]
            for msg in warm_msgs:
                pipe1.process(msg, user_id="alice")

            trust_before_end = pipe1.person_profiles.get_or_create("alice").trust
            pipe1.end_session(user_id="alice")
            trust_after_session1 = pipe1.person_profiles.get_or_create("alice").trust

            # Session 2: new pipeline, same database
            pipe2 = _pipe_persistent(db_path)
            profile = pipe2.person_profiles.get_or_create("alice")

            assert profile.trust > 0.5, (
                f"trust {profile.trust:.3f} didn't persist above 0.5"
            )
            assert profile.interaction_count >= 3, (
                f"interactions {profile.interaction_count} < 3"
            )

            # Long-term memories should be retrievable
            memories = pipe2.long_term.all()
            assert len(memories) > 0, "no long-term memories persisted"
        finally:
            os.unlink(db_path)

    def test_spike_memories_persist(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            pipe1 = _pipe_persistent(db_path)
            pipe1.process(
                "You betrayed and deceived me completely!",
                user_id="alice",
            )
            pipe1.end_session(user_id="alice")

            pipe2 = _pipe_persistent(db_path)
            memories = pipe2.long_term.all()
            spike_memories = [m for m in memories if m.spike]
            assert len(spike_memories) > 0, "spike memories didn't persist"
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# 6. Emotional contagion
# ---------------------------------------------------------------------------

class TestEmotionalContagion:
    def test_excited_message_raises_arousal_and_valence(self):
        pipe = _pipe()
        result = pipe.process(
            "OMG THIS IS AMAZING I'M SO HAPPY!!!",
            user_id="alice",
        )
        snap = result.debug.modulator_snapshot
        assert snap["arousal"] > 0.6, f"arousal {snap['arousal']:.3f} <= 0.6"
        assert snap["valence"] > 0.7, f"valence {snap['valence']:.3f} <= 0.7"

    def test_depressed_message_drops_valence(self):
        pipe = _pipe()
        result = pipe.process(
            "I'm so sad and lonely, I feel hopeless and miserable...",
            user_id="alice",
        )
        snap = result.debug.modulator_snapshot
        assert snap["valence"] < 0.45, f"valence {snap['valence']:.3f} >= 0.45"


# ---------------------------------------------------------------------------
# 7. Context switching
# ---------------------------------------------------------------------------

class TestContextSwitching:
    def test_different_histories_different_states(self):
        warm_pipe = _pipe()
        hostile_pipe = _pipe()

        # Warm user: 10 positive interactions
        warm_msgs = [
            "thank you!", "you're great", "I appreciate you",
            "wonderful work", "amazing help", "you're the best",
            "brilliant!", "love this", "fantastic", "superb job",
        ]
        for msg in warm_msgs:
            warm_pipe.process(msg, user_id="warm_user")

        # Hostile user: 5 negative interactions
        hostile_msgs = [
            "you're stupid",
            "this is bullshit",
            "I hate this",
            "you're useless and pathetic",
            "worst AI ever",
        ]
        for msg in hostile_msgs:
            hostile_pipe.process(msg, user_id="hostile_user")

        # Reset engine to neutral baseline before context switch test
        warm_pipe.engine.state.arousal = 0.5
        warm_pipe.engine.state.valence = 0.5
        warm_pipe.engine.state.bonding = 0.5

        # Process same neutral message for warm user
        result_warm = warm_pipe.process("hey", user_id="warm_user")
        snap_warm = result_warm.debug.modulator_snapshot.copy()

        # Reset engine again
        hostile_pipe.engine.state.arousal = 0.5
        hostile_pipe.engine.state.valence = 0.5
        hostile_pipe.engine.state.bonding = 0.5

        # Process same neutral message for hostile user
        result_hostile = hostile_pipe.process("hey", user_id="hostile_user")
        snap_hostile = result_hostile.debug.modulator_snapshot.copy()

        # Trust levels should differ
        warm_profile = warm_pipe.person_profiles.get_or_create("warm_user")
        hostile_profile = hostile_pipe.person_profiles.get_or_create("hostile_user")

        assert warm_profile.trust > hostile_profile.trust, (
            f"warm trust {warm_profile.trust:.3f} <= hostile trust "
            f"{hostile_profile.trust:.3f}"
        )
        assert snap_warm["valence"] > snap_hostile["valence"], (
            f"warm valence {snap_warm['valence']:.3f} <= hostile valence "
            f"{snap_hostile['valence']:.3f}"
        )


# ---------------------------------------------------------------------------
# 8. Topic sensitivity
# ---------------------------------------------------------------------------

class TestTopicSensitivity:
    def test_negative_topic_builds_charge(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            pipe1 = _pipe_persistent(db_path)
            pipe1.topic_profiles.get_or_create("work")

            # Discuss work negatively 3 times
            negative_work_msgs = [
                "work is terrible today, everything went wrong",
                "I hate my work so much, it's awful",
                "work is making me miserable and stressed",
            ]
            for msg in negative_work_msgs:
                pipe1.process(msg, user_id="alice")
                # Manually record negative since topic detection is substring-based
                pipe1.topic_profiles.record_negative("work", intensity=0.8)

            topic = pipe1.topic_profiles.get("work")
            assert topic is not None
            assert topic.emotional_charge > 0.2, (
                f"charge {topic.emotional_charge:.3f} <= 0.2 after 3 negative mentions"
            )

            pipe1.end_session(user_id="alice")

            # Session 2: mention work casually
            pipe2 = _pipe_persistent(db_path)
            pipe2.topic_profiles.get_or_create("work")

            topic_reloaded = pipe2.topic_profiles.get("work")
            assert topic_reloaded is not None
            assert topic_reloaded.emotional_charge > 0.2, (
                f"topic charge didn't persist: {topic_reloaded.emotional_charge:.3f}"
            )
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# 9. Spike detection
# ---------------------------------------------------------------------------

class TestSpikeDetection:
    def test_extreme_message_triggers_spike(self):
        pipe = _pipe()
        initial_lt = pipe.long_term.count()

        # Needs betrayal keyword + enough negative contagion to push
        # intensity above 0.8 (formula: max(0.7, 1.0 - detected_valence))
        result = pipe.process(
            "You lied and betrayed me, I'm furious! You're a horrible deceiver!",
            user_id="alice",
        )

        assert result.debug.is_spike, (
            f"spike not detected — intensity={result.debug.event_intensity:.3f}, "
            f"classified={result.debug.event_classified}"
        )
        assert result.debug.event_intensity >= 0.8, (
            f"intensity {result.debug.event_intensity:.3f} < 0.8"
        )
        assert pipe.long_term.count() > initial_lt, (
            "no direct write to long-term memory on spike"
        )

        # Verify the spike memory exists
        memories = pipe.long_term.all()
        spikes = [m for m in memories if m.spike]
        assert len(spikes) > 0, "no spike-flagged memories in long-term store"


# ---------------------------------------------------------------------------
# 10. Decay over time
# ---------------------------------------------------------------------------

class TestDecayOverTime:
    def test_modulators_decay_toward_baseline(self):
        pipe = _pipe()

        # Set post-conflict state
        pipe.engine.state.arousal = 0.9
        pipe.engine.state.valence = 0.2

        # Simulate 10 minutes (600 seconds)
        # Arousal half-life=120s → ~5 half-lives → nearly back to 0.5
        # Valence half-life=1800s → ~0.33 half-lives → partial recovery
        pipe.engine.decay(600)

        snap = _snap(pipe)
        assert snap["arousal"] < 0.55, (
            f"arousal {snap['arousal']:.3f} >= 0.55 after 600s decay"
        )
        assert snap["valence"] > 0.2, (
            f"valence {snap['valence']:.3f} didn't recover at all"
        )
        assert snap["valence"] < 0.5, (
            f"valence {snap['valence']:.3f} >= 0.5 — recovered too much for 600s"
        )
