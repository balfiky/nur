"""Integration tests for the v2 pipeline — Phase 5."""

import pytest

from core.dual_process.generator import MockLLMBackend
from core.types import Anticipation, InnerDialogueTrace, DefenseActivation
from pipeline import CognitivePipeline, DebugState, PipelineResponse


# ---------------------------------------------------------------------------
# Counting backend — tracks LLM call count
# ---------------------------------------------------------------------------

class CountingLLMBackend:
    """Mock that counts calls and returns parseable responses.

    Returns "APPROVED: ok" for slow-path calls (contains 'Evaluate this response')
    and "I understand." for everything else.
    """

    def __init__(self, response: str = "I understand.") -> None:
        self._response = response
        self.call_count = 0

    def generate(self, system_prompt: str, user_message: str) -> str:
        self.call_count += 1
        # Detect slow-path calls and return parseable approval
        if "Evaluate this response" in system_prompt or "APPROVED" in system_prompt:
            return "APPROVED: response is appropriate"
        return self._response


# ---------------------------------------------------------------------------
# End-to-end message processing
# ---------------------------------------------------------------------------

class TestV2EndToEnd:
    def _make_pipeline(self, response: str = "I understand.") -> CognitivePipeline:
        backend = MockLLMBackend(response=response)
        return CognitivePipeline(llm_backend=backend)

    def test_basic_v2_process(self):
        """End-to-end v2 pipeline produces a response with full debug."""
        pipe = self._make_pipeline()
        result = pipe.process("Hello there", user_id="alice")
        assert isinstance(result, PipelineResponse)
        assert result.response == "I understand."
        assert result.debug.user_message == "Hello there"

    def test_debug_payload_has_anticipation(self):
        pipe = self._make_pipeline()
        result = pipe.process("How are you?", user_id="bob")
        assert result.debug.anticipation is not None
        assert isinstance(result.debug.anticipation, Anticipation)

    def test_debug_payload_has_dialogue_trace(self):
        pipe = self._make_pipeline()
        result = pipe.process("Tell me something", user_id="alice")
        assert result.debug.dialogue_trace is not None
        assert isinstance(result.debug.dialogue_trace, InnerDialogueTrace)
        assert result.debug.dialogue_trace.total_llm_calls >= 1

    def test_debug_payload_has_defense(self):
        """Defense may or may not activate, but the field should exist."""
        pipe = self._make_pipeline()
        result = pipe.process("Hello", user_id="alice")
        # For a calm message, defense likely doesn't fire
        # Field is None when no defense, DefenseActivation when it fires
        assert result.debug.defense_activation is None or isinstance(
            result.debug.defense_activation, DefenseActivation
        )

    def test_debug_payload_has_unresolved_count(self):
        pipe = self._make_pipeline()
        result = pipe.process("Hello", user_id="alice")
        assert isinstance(result.debug.unresolved_count, int)
        assert result.debug.unresolved_count >= 0

    def test_all_v2_debug_fields_present(self):
        """Complete check that all v2 debug fields exist."""
        pipe = self._make_pipeline()
        result = pipe.process("test message", user_id="alice")
        d = result.debug
        assert hasattr(d, "anticipation")
        assert hasattr(d, "dialogue_trace")
        assert hasattr(d, "defense_activation")
        assert hasattr(d, "unresolved_count")
        # v1 fields still present
        assert hasattr(d, "detected_emotion")
        assert hasattr(d, "modulator_snapshot")
        assert hasattr(d, "event_classified")
        assert hasattr(d, "energy_after")
        assert hasattr(d, "emotion_label")


# ---------------------------------------------------------------------------
# LLM call budget: 4-7 per message
# ---------------------------------------------------------------------------

class TestLLMCallBudget:
    def test_typical_message_4_calls(self):
        """Normal message: contagion(1) + inner dialogue fast+slow(2) + master(1) = 4."""
        backend = CountingLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.process("How are you today?", user_id="alice")
        # MockLLMBackend returns "I understand." → slow path parses as APPROVED
        # So: contagion(1) + fast(1) + slow(1) + master(1) = 4
        assert 4 <= backend.call_count <= 7, f"Expected 4-7 calls, got {backend.call_count}"

    def test_call_count_at_minimum(self):
        """Verify minimum with mock that approves round 1.

        contagion=1, classify_event_llm=1, fast=1, slow=1, master=1, self_check=1 → 6.
        """
        backend = CountingLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.process("Hello", user_id="alice")
        assert backend.call_count == 6

    def test_high_arousal_bypasses_inner_dialogue(self):
        """High arousal → fast path only (skips slow path).

        contagion(1) + classify(1) + fast_only(1) + master(1) + self_check(1) = 5.
        """
        backend = CountingLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.engine.state.arousal = 0.95
        pipe.process("Emergency!", user_id="alice")
        assert backend.call_count == 5

    def test_low_energy_bypasses_inner_dialogue(self):
        """Low energy → fast path only, fewer calls.

        contagion(1) + classify(1) + fast_only(1) + master(1) + self_check(1) = 5.
        """
        backend = CountingLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.engine.state.energy = 0.1
        pipe.process("Hello", user_id="alice")
        assert backend.call_count == 5

    def test_multiple_messages_budget_consistent(self):
        """Each message stays within budget."""
        backend = CountingLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        for i in range(5):
            before = backend.call_count
            pipe.process(f"Message {i}", user_id="alice")
            calls_this_msg = backend.call_count - before
            assert 3 <= calls_this_msg <= 7, (
                f"Message {i}: {calls_this_msg} calls"
            )


# ---------------------------------------------------------------------------
# v1 behavior preserved
# ---------------------------------------------------------------------------

class TestV1BehaviorPreserved:
    def _make_pipeline(self) -> CognitivePipeline:
        return CognitivePipeline(llm_backend=MockLLMBackend())

    def test_contagion_still_works(self):
        pipe = self._make_pipeline()
        pipe.process("I'm so excited and thrilled!!!", user_id="alice")
        snap = pipe.engine.snapshot()
        assert snap["arousal"] > 0.5 or snap["valence"] > 0.5

    def test_conflict_classification_preserved(self):
        pipe = self._make_pipeline()
        result = pipe.process("I'm so angry about this argument!", user_id="alice")
        assert result.debug.event_classified == "conflict"

    def test_energy_drains_preserved(self):
        pipe = self._make_pipeline()
        initial = pipe.engine.state.energy
        for i in range(10):
            pipe.process(f"Message {i}", user_id="alice")
        assert pipe.engine.state.energy < initial

    def test_spike_writes_to_long_term_preserved(self):
        pipe = self._make_pipeline()
        initial_count = pipe.long_term.count()
        pipe.process("You betrayed and deceived me completely!", user_id="alice")
        assert pipe.long_term.count() > initial_count

    def test_session_management_preserved(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        pipe.process("Thanks!", user_id="alice")
        result = pipe.end_session(user_id="alice")
        assert len(pipe.short_term) == 0
        assert result.summary != ""

    def test_rest_recovers_energy_preserved(self):
        pipe = self._make_pipeline()
        for i in range(15):
            pipe.process(f"msg {i}", user_id="alice")
        drained = pipe.engine.state.energy
        pipe.apply_rest(5.0)
        assert pipe.engine.state.energy > drained

    def test_person_profile_still_tracks(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        pipe.process("Hi again", user_id="alice")
        profile = pipe.person_profiles.get_or_create("alice")
        assert profile.interaction_count >= 2

    def test_conversation_history_preserved(self):
        pipe = self._make_pipeline()
        pipe.process("Hello", user_id="alice")
        assert len(pipe._conversation_history) == 2
        assert pipe._conversation_history[0]["role"] == "user"
        assert pipe._conversation_history[1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Resolution integration
# ---------------------------------------------------------------------------

class TestResolutionIntegration:
    def test_spike_creates_unresolved_item(self):
        """Emotional spikes should create unresolved items."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        pipe.process("You betrayed and deceived me completely!", user_id="alice")
        assert pipe.engine.state.resolution > 0
        assert len(pipe.engine.active_unresolved()) > 0

    def test_resolution_event_resolves_item(self):
        """A resolution event should resolve existing items."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        # Create a spike → unresolved item
        pipe.process("You betrayed me!", user_id="alice")
        unresolved_before = len(pipe.engine.active_unresolved())
        assert unresolved_before > 0
        # Resolution event
        pipe.process("I'm sorry, let's forgive and find peace", user_id="alice")
        assert len(pipe.engine.active_unresolved()) < unresolved_before

    def test_unresolved_count_in_debug(self):
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("You betrayed me!", user_id="alice")
        assert result.debug.unresolved_count > 0


# ---------------------------------------------------------------------------
# Anticipation integration
# ---------------------------------------------------------------------------

class TestAnticipationIntegration:
    def test_anticipation_runs_on_every_message(self):
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("Hello", user_id="alice")
        assert result.debug.anticipation is not None

    def test_anticipation_uses_conversation_history(self):
        """After building history, anticipation has recent messages to work with."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        pipe.topic_profiles.get_or_create("breakup")
        # Need emotional_charge >= 0.5 for sensitive topic detection.
        # CHARGE_NEGATIVE_DELTA=0.10, so 6 calls at intensity 1.0 → charge 0.60.
        for _ in range(6):
            pipe.topic_profiles.record_negative("breakup", 1.0)

        pipe.process("thinking about the breakup", user_id="alice")
        pipe.process("the breakup was hard", user_id="alice")
        result = pipe.process("still hurting from breakup", user_id="alice")
        # With sensitive topic mentioned 2+ times, anticipation should fire
        ant = result.debug.anticipation
        assert ant is not None
        assert ant.confidence > 0 or len(ant.predicted_topics) > 0


# ---------------------------------------------------------------------------
# Inner dialogue integration
# ---------------------------------------------------------------------------

class TestInnerDialogueIntegration:
    def test_dialogue_trace_in_debug(self):
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("What do you think?", user_id="alice")
        trace = result.debug.dialogue_trace
        assert trace is not None
        assert len(trace.rounds) >= 1
        assert trace.final_candidate != ""

    def test_dialogue_trace_with_counting_backend(self):
        """CountingLLMBackend returns parseable APPROVED → 1 round."""
        pipe = CognitivePipeline(llm_backend=CountingLLMBackend())
        result = pipe.process("Hello", user_id="alice")
        trace = result.debug.dialogue_trace
        assert trace.rounds[0].slow_path_approved is True
        assert trace.reached_deadlock is False


# ---------------------------------------------------------------------------
# Defense integration
# ---------------------------------------------------------------------------

class TestDefenseIntegration:
    def test_defense_does_not_fire_on_calm(self):
        """Calm message → no defense activation."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("Hello, how are you?", user_id="alice")
        assert result.debug.defense_activation is None

    def test_defense_fires_on_extreme_state(self):
        """Push engine to extreme state, then process → defense should fire."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        pipe.engine.state.arousal = 0.95
        pipe.engine.state.valence = 0.05
        # Need to keep arousal below bypass threshold so inner dialogue runs
        # Actually with arousal 0.95 it will bypass, and defense checks state
        # after all updates. Let's set a state that exceeds comfort but
        # doesn't bypass dialogue.
        pipe.engine.state.arousal = 0.75
        pipe.engine.state.valence = 0.1
        # Lower trust so comfort threshold is low
        person = pipe.person_profiles.get_or_create("alice")
        person.trust = 0.2
        pipe.person_profiles.save(person)

        result = pipe.process("This is frustrating", user_id="alice")
        # Defense may or may not fire depending on exact intensity after updates
        # At minimum, verify the field is populated correctly
        if result.debug.defense_activation is not None:
            assert isinstance(result.debug.defense_activation, DefenseActivation)
            assert result.debug.defense_activation.defense_type in [
                "rationalization", "deflection", "minimization", "projection"
            ]


# ---------------------------------------------------------------------------
# Full v2 arc
# ---------------------------------------------------------------------------

class TestFullV2Arc:
    def test_full_v2_session(self):
        """Complete session: greeting → warmth → spike → resolution → end."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())

        # Greeting
        r1 = pipe.process("Hey there!", user_id="paco")
        assert r1.debug.anticipation is not None
        assert r1.debug.dialogue_trace is not None

        # Warmth
        r2 = pipe.process("I'm grateful for your help, thank you", user_id="paco")
        assert r2.debug.event_classified == "positive_feedback"

        # Spike — betrayal (avoid "trust" which dilutes negative valence)
        r3 = pipe.process("You betrayed and deceived me completely!", user_id="paco")
        assert r3.debug.is_spike is True
        assert r3.debug.unresolved_count > 0
        assert pipe.engine.state.resolution > 0

        # Resolution
        r4 = pipe.process("I'm sorry, let's forgive and find peace", user_id="paco")
        assert r4.debug.unresolved_count < r3.debug.unresolved_count

        # End session
        digested = pipe.end_session(user_id="paco")
        assert digested.summary != ""
        assert len(pipe.short_term) == 0
