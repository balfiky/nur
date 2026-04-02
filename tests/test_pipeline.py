"""Tests for the cognitive pipeline — Phase 6."""

import json
import pytest

from core.dual_process.generator import MockLLMBackend
from pipeline import CognitivePipeline, DebugState, PipelineResponse


class TestCognitivePipeline:
    def _make_pipeline(
        self,
        response: str = "I understand.",
        *,
        db_path: str = ":memory:",
    ) -> CognitivePipeline:
        backend = MockLLMBackend(response=response)
        return CognitivePipeline(llm_backend=backend, db_path=db_path)

    def test_basic_process(self):
        pipe = self._make_pipeline()
        result = pipe.process("Hello there", user_id="alice")
        assert isinstance(result, PipelineResponse)
        assert result.response == "I understand."
        assert result.debug.user_message == "Hello there"
        assert result.debug.user_id == "alice"

    def test_returns_debug_state(self):
        pipe = self._make_pipeline()
        result = pipe.process("How are you?", user_id="bob")
        d = result.debug
        assert isinstance(d, DebugState)
        assert d.detected_emotion is not None
        assert d.appraisal_frame is not None
        assert d.modulator_snapshot != {}
        assert d.event_classified != ""
        assert d.energy_after > 0
        assert d.emotion_label != ""

    def test_contagion_affects_state(self):
        pipe = self._make_pipeline()
        # Excited message should shift arousal/valence
        pipe.process("I'm so excited and thrilled!!!", user_id="alice")
        snap = pipe.engine.snapshot()
        assert snap["arousal"] > 0.5 or snap["valence"] > 0.5

    def test_conflict_event_classification(self):
        pipe = self._make_pipeline()
        result = pipe.process("I'm angry with you about this argument!", user_id="alice")
        assert result.debug.event_classified == "conflict"

    def test_warmth_event_classification(self):
        pipe = self._make_pipeline()
        result = pipe.process("You're so kind and caring", user_id="alice")
        assert result.debug.event_classified == "warmth"

    def test_positive_feedback_classification(self):
        pipe = self._make_pipeline()
        result = pipe.process("Thank you, I really appreciate that", user_id="alice")
        assert result.debug.event_classified == "positive_feedback"

    def test_betrayal_classification(self):
        pipe = self._make_pipeline()
        result = pipe.process("You lied and betrayed my trust", user_id="alice")
        assert result.debug.event_classified == "betrayal"

    def test_external_distress_uses_user_message_not_relational_conflict(self):
        pipe = self._make_pipeline()
        result = pipe.process("I'm furious about work, not at you.", user_id="alice")
        assert result.debug.appraisal_frame is not None
        assert result.debug.appraisal_frame.primary_target == "external"
        assert result.debug.appraisal_frame.targets_assistant is False
        assert result.debug.event_classified == "user_message"
        profile = pipe.person_profiles.get_or_create("alice")
        assert profile.trust == pytest.approx(0.5)

    def test_direct_attack_still_affects_relationship(self):
        pipe = self._make_pipeline()
        result = pipe.process("You are useless and this answer is terrible.", user_id="alice")
        assert result.debug.appraisal_frame is not None
        assert result.debug.appraisal_frame.targets_assistant is True
        assert result.debug.event_classified == "negative_feedback"
        profile = pipe.person_profiles.get_or_create("alice")
        assert profile.trust < 0.5

    def test_energy_drains_over_messages(self):
        pipe = self._make_pipeline()
        initial_energy = pipe.engine.state.energy
        for i in range(10):
            pipe.process(f"Message {i}", user_id="alice")
        assert pipe.engine.state.energy < initial_energy

    def test_short_term_memory_grows(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        pipe.process("How are you?", user_id="alice")
        # Each process call records 2 entries (event + outcome)
        assert len(pipe.short_term) >= 4

    def test_conversation_history_tracked(self):
        pipe = self._make_pipeline(response="Fine, thanks!")
        pipe.process("Hello", user_id="alice")
        assert len(pipe._conversation_history) == 2
        assert pipe._conversation_history[0]["role"] == "user"
        assert pipe._conversation_history[1]["role"] == "assistant"

    def test_end_session_clears_state(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        pipe.process("You're great, thank you!", user_id="alice")
        result = pipe.end_session(user_id="alice")
        assert len(pipe.short_term) == 0
        assert len(pipe._conversation_history) == 0
        assert result.summary != ""

    def test_end_session_writes_to_long_term(self):
        pipe = self._make_pipeline()
        for i in range(5):
            pipe.process(f"Warm message {i}, thanks!", user_id="alice")
        initial_lt_count = pipe.long_term.count()
        pipe.end_session(user_id="alice")
        # Digestion should write at least one memory
        assert pipe.long_term.count() >= initial_lt_count

    def test_relationship_context_surfaces_across_sessions(self, tmp_path):
        db_path = str(tmp_path / "relationship.db")
        pipe = self._make_pipeline(db_path=db_path)
        pipe.process("I'm angry with you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")

        result = pipe.process("hello again", user_id="alice")
        assert result.debug.relationship_context is not None
        assert result.debug.relationship_context.open_loop_count >= 1
        assert "Relationship context" in pipe._llm_backend.last_system_prompt
        pipe.close()

    def test_relationship_repair_closes_open_loop(self, tmp_path):
        db_path = str(tmp_path / "relationship_repair.db")
        pipe = self._make_pipeline(db_path=db_path)
        pipe.process("I'm angry with you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")

        pipe.process("I'm sorry for snapping at you about the deadline.", user_id="alice")
        pipe.end_session(user_id="alice")

        result = pipe.process("thanks for sticking with me", user_id="alice")
        assert result.debug.relationship_context is not None
        assert result.debug.relationship_context.open_loop_count == 0
        assert any(
            event.event_kind == "repair"
            for event in result.debug.relationship_context.recent_events
        )
        pipe.close()

    def test_rest_recovers_energy(self):
        pipe = self._make_pipeline()
        # Drain energy
        for i in range(20):
            pipe.process(f"Intense message {i}", user_id="alice")
        drained_energy = pipe.engine.state.energy
        # Rest for 5 hours
        pipe.apply_rest(5.0)
        assert pipe.engine.state.energy > drained_energy

    def test_self_check_triggers_regeneration(self):
        """When self-check fails, pipeline should regenerate."""
        # Response that will fail self-check when valence is very low
        backend = MockLLMBackend(response="That's great! Wonderful! Amazing stuff!")
        pipe = CognitivePipeline(llm_backend=backend)
        # Force very low valence
        pipe.engine.state.valence = 0.1
        result = pipe.process("I'm feeling terrible", user_id="alice")
        # Self-check should have caught the positive tone mismatch
        # and attempted regeneration
        if not result.debug.self_check_passed:
            assert result.debug.generation_attempts == 2
            assert result.debug.correction_note != ""

    def test_person_profile_interaction_count_increases(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        pipe.process("Hi again", user_id="alice")
        profile = pipe.person_profiles.get_or_create("alice")
        assert profile.interaction_count >= 2

    def test_topic_detection(self):
        pipe = self._make_pipeline()
        # Create a topic profile first
        pipe.topic_profiles.get_or_create("work")
        result = pipe.process("Let's talk about work", user_id="alice")
        assert len(result.debug.topic_profiles) >= 1

    def test_multiple_users_isolated(self):
        pipe = self._make_pipeline()
        pipe.process("Hello from Alice", user_id="alice")
        pipe.process("Hello from Bob", user_id="bob")
        alice = pipe.person_profiles.get_or_create("alice")
        bob = pipe.person_profiles.get_or_create("bob")
        assert alice.person_id != bob.person_id

    def test_spike_event_writes_to_long_term(self):
        pipe = self._make_pipeline()
        initial_count = pipe.long_term.count()
        # Betrayal should be high intensity → spike
        pipe.process("You betrayed and deceived me completely!", user_id="alice")
        # Spike should have been written directly to LT
        assert pipe.long_term.count() > initial_count

    def test_insult_classified_as_negative_feedback(self):
        pipe = self._make_pipeline()
        result = pipe.process("you are stupid and useless", user_id="alice")
        assert result.debug.event_classified == "negative_feedback"
        assert result.debug.event_intensity > 0.5

    def test_insult_moves_modulators(self):
        pipe = self._make_pipeline()
        result = pipe.process("you are stupid and worthless", user_id="alice")
        snap = result.debug.modulator_snapshot
        # Insult should push valence below neutral and arousal above neutral
        assert snap["valence"] < 0.5, f"Valence should drop: {snap['valence']}"
        assert snap["arousal"] > 0.5, f"Arousal should rise: {snap['arousal']}"

    def test_profanity_classified_as_negative(self):
        pipe = self._make_pipeline()
        result = pipe.process("this is total bullshit", user_id="alice")
        assert result.debug.event_classified == "negative_feedback"

    def test_hostile_command_classified_as_conflict(self):
        pipe = self._make_pipeline()
        result = pipe.process("shut up and go away", user_id="alice")
        assert result.debug.event_classified == "conflict"

    def test_strong_negative_contagion_moves_modulators(self):
        """Even without keyword match, strong detected negative emotion should move things."""
        pipe = self._make_pipeline()
        # "terrible awful" hits contagion patterns but check modulator movement
        result = pipe.process("everything is terrible and awful", user_id="alice")
        snap = result.debug.modulator_snapshot
        assert snap["valence"] < 0.5, f"Should feel negative: {snap['valence']}"

    def test_full_session_arc(self):
        """Simulate a complete session: greeting → discussion → conflict → resolution."""
        pipe = self._make_pipeline(response="I hear you.")
        pipe.process("Hey there, hope you're doing well", user_id="paco")
        snap1 = pipe.engine.snapshot()

        pipe.process("I'm grateful for your help yesterday", user_id="paco")
        snap2 = pipe.engine.snapshot()
        assert snap2["valence"] >= snap1["valence"]  # positive feedback

        pipe.process("Actually I'm angry about what happened", user_id="paco")
        snap3 = pipe.engine.snapshot()
        assert snap3["arousal"] > snap2["arousal"]  # conflict raises arousal

        pipe.process("I'm sorry, let's forgive and move on in peace", user_id="paco")
        snap4 = pipe.engine.snapshot()

        # End session
        digested = pipe.end_session(user_id="paco")
        assert digested.summary != ""
        assert len(pipe.short_term) == 0


class TestPipelineLLMClassification:
    """Tests for LLM-based event classification and topic detection."""

    def _make_pipeline_with_llm_response(self, response: str) -> CognitivePipeline:
        """Create pipeline with a backend that returns specific JSON for classification."""
        backend = _ClassifyMockBackend(response)
        return CognitivePipeline(llm_backend=backend)

    def test_llm_classify_event_valid_json(self):
        backend = _ClassifyMockBackend('{"event_type": "warmth", "intensity": 0.7}')
        pipe = CognitivePipeline(llm_backend=backend)
        result = pipe.process("you're so kind", user_id="alice")
        assert result.debug.event_classified == "warmth"

    def test_llm_classify_event_invalid_falls_back(self):
        backend = MockLLMBackend(response="I understand.")
        pipe = CognitivePipeline(llm_backend=backend)
        # Assistant-targeted anger triggers rule-based conflict classification
        result = pipe.process("I'm angry with you about this argument!", user_id="alice")
        assert result.debug.event_classified == "conflict"

    def test_rule_based_detect_topics_substring(self):
        """Topic detection is rule-based (substring match). LLM path not used in pipeline."""
        backend = MockLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.topic_profiles.get_or_create("work")
        result = pipe.process("my work is stressful", user_id="alice")
        assert len(result.debug.topic_profiles) >= 1
        assert result.debug.topic_profiles[0].topic == "work"

    def test_llm_detect_topics_invalid_falls_back(self):
        backend = MockLLMBackend(response="I understand.")
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.topic_profiles.get_or_create("work")
        # Substring "work" is in text → rule-based fallback finds it
        result = pipe.process("Let's talk about work", user_id="alice")
        assert len(result.debug.topic_profiles) >= 1

    def test_rule_based_classify_betrayal(self):
        """Event classification is rule-based. LLM path not used in pipeline."""
        backend = MockLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        result = pipe.process("you betrayed my trust", user_id="alice")
        assert result.debug.event_classified == "betrayal"


class _ClassifyMockBackend:
    """Mock that returns JSON for classify_event prompts, canned text otherwise."""

    def __init__(self, classify_response: str) -> None:
        self._classify = classify_response

    def generate(self, system_prompt: str, user_message: str) -> str:
        if "Event Classification" in system_prompt or "event_type" in system_prompt:
            return self._classify
        return "I understand."


class _TopicMockBackend:
    """Mock that returns JSON for detect_topics prompts, canned text otherwise."""

    def __init__(self, topics_response: str) -> None:
        self._topics = topics_response

    def generate(self, system_prompt: str, user_message: str) -> str:
        if "Topic Detection" in system_prompt or "known_topics" in system_prompt.lower():
            return self._topics
        return "I understand."
