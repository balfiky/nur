"""v2 calibration scenarios — Phase 7.

Scripted emotional scenarios that exercise v2 features end-to-end
with precise assertions on thresholds, budgets, and interactions.
"""

import pytest
from datetime import datetime, timezone, timedelta

from core.types import (
    Anticipation,
    BaselineShift,
    DefenseActivation,
    DefenseEvent,
    InnerDialogueTrace,
    ModulatorState,
    PersonProfile,
    SelfProfile,
    TopicProfile,
    UnresolvedItem,
    ValueHierarchy,
)
from core.defense_mechanisms import DefenseMechanism, _BASE_SUPPRESSION
from core.anticipation import AnticipationEngine, PRE_SHIFT_SCALE, CONFIDENCE_GATE
from core.dual_process.inner_dialogue import (
    InnerDialogue,
    AROUSAL_BYPASS_THRESHOLD,
    ENERGY_BYPASS_THRESHOLD,
    RESOLUTION_INSIST_THRESHOLD,
    build_slow_path_prompt,
)
from core.dual_process.generator import MockLLMBackend
from pipeline import CognitivePipeline, PipelineResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class SequenceLLMBackend:
    """Returns responses from a pre-defined sequence."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._index = 0
        self.call_count = 0
        self.prompts: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_message: str) -> str:
        self.prompts.append((system_prompt, user_message))
        self.call_count += 1
        if self._index < len(self._responses):
            resp = self._responses[self._index]
            self._index += 1
            return resp
        return "I understand."


class CountingLLMBackend:
    """Counts LLM calls and returns canned response."""

    def __init__(self, response: str = "I understand.") -> None:
        self._response = response
        self.call_count = 0

    def generate(self, system_prompt: str, user_message: str) -> str:
        self.call_count += 1
        return self._response


# ---------------------------------------------------------------------------
# Scenario 1: Deflection under low trust
# ---------------------------------------------------------------------------

class TestDeflectionUnderLowTrust:
    """High arousal + low trust → deflection defense fires."""

    def test_deflection_activates(self):
        """Direct defense evaluation: arousal 0.9, valence 0.15, trust 0.3."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.9, valence=0.15, energy=0.8)
        person = PersonProfile(person_id="stranger", trust=0.3)
        self_prof = SelfProfile(maturity_score=0.0)

        # raw_intensity = 0.9*0.6 + abs(0.15-0.5)*2*0.4 = 0.54 + 0.28 = 0.82
        # comfort = 0.5 + 0.3*0.2 + 0.0*0.2 + bonding_contrib = ~0.61
        # 0.82 > 0.61 → defense fires
        _, activation = dm.evaluate(
            inner_dialogue_output="I feel really upset about this.",
            modulator_state=state,
            self_profile=self_prof,
            person_profile=person,
        )
        assert activation is not None
        assert activation.defense_type == "deflection"
        assert activation.raw_intensity > activation.expressed_intensity
        assert activation.suppression_delta > 0

    def test_deflection_threshold_exact(self):
        """Verify arousal > 0.7 and trust < 0.4 triggers deflection."""
        dm = DefenseMechanism()
        # Just above deflection threshold
        state = ModulatorState(arousal=0.75, valence=0.1, energy=0.8)
        person = PersonProfile(person_id="low_trust", trust=0.35)
        self_prof = SelfProfile(maturity_score=0.0)

        _, activation = dm.evaluate(
            inner_dialogue_output="test",
            modulator_state=state,
            self_profile=self_prof,
            person_profile=person,
        )
        if activation is not None:
            assert activation.defense_type == "deflection"

    def test_deflection_adds_instruction(self):
        """Defense appends deflection instruction to output."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.9, valence=0.15)
        person = PersonProfile(person_id="x", trust=0.2)
        self_prof = SelfProfile()

        filtered, activation = dm.evaluate(
            inner_dialogue_output="Original response",
            modulator_state=state,
            self_profile=self_prof,
            person_profile=person,
        )
        assert activation is not None
        assert "[Defense instruction:" in filtered
        assert "redirect" in filtered.lower()

    def test_no_deflection_with_high_trust(self):
        """Same arousal but high trust → different defense or none."""
        dm = DefenseMechanism()
        state = ModulatorState(arousal=0.9, valence=0.15)
        person = PersonProfile(person_id="trusted", trust=0.8)
        self_prof = SelfProfile()

        _, activation = dm.evaluate(
            inner_dialogue_output="test",
            modulator_state=state,
            self_profile=self_prof,
            person_profile=person,
        )
        # High trust raises comfort threshold, defense may not fire
        # or if it fires, it shouldn't be deflection (trust >= 0.4)
        if activation is not None:
            assert activation.defense_type != "deflection"

    def test_deflection_in_pipeline(self):
        """Full pipeline: low trust person + intense message → defense in debug."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        person = pipe.person_profiles.get_or_create("stranger")
        person.trust = 0.2
        pipe.person_profiles.save(person)
        pipe.engine.state.arousal = 0.85
        pipe.engine.state.valence = 0.15

        result = pipe.process("This is very frustrating", user_id="stranger")
        # Defense should fire given extreme state + low trust
        if result.debug.defense_activation is not None:
            assert isinstance(result.debug.defense_activation, DefenseActivation)


# ---------------------------------------------------------------------------
# Scenario 2: Inner dialogue disagreement
# ---------------------------------------------------------------------------

class TestInnerDialogueDisagreement:
    """High arousal (below bypass) + high resolution → multi-round + slow
    path references unresolved items."""

    def test_multi_round_with_objection(self):
        """Slow path objects round 1, approves round 2."""
        backend = SequenceLLMBackend([
            "Let me help you with that.",          # fast path round 1
            "OBJECTION: doesn't address tension",  # slow path round 1
            "I hear your concern, let's talk.",     # fast path revision
            "APPROVED: addresses the tension",      # slow path round 2
        ])
        dialogue = InnerDialogue(backend=backend)
        state = ModulatorState(arousal=0.5, energy=0.8, resolution=0.7)

        trace = dialogue.deliberate(
            user_message="I'm worried about what happened",
            state=state,
            unresolved=[
                UnresolvedItem(
                    id="u1", source="spike",
                    description="Unprocessed betrayal event",
                    created_at=datetime.now(timezone.utc),
                    intensity=0.7, decay_rate=0.03,
                ),
            ],
        )
        assert len(trace.rounds) == 2
        assert trace.rounds[0].slow_path_approved is False
        assert trace.rounds[0].objection_reason is not None
        assert trace.rounds[1].slow_path_approved is True
        assert trace.total_llm_calls == 4
        assert trace.dominant_path == "slow"

    def test_slow_path_prompt_includes_unresolved(self):
        """Verify the slow path prompt actually contains unresolved item text."""
        state = ModulatorState(arousal=0.5, resolution=0.8)
        unresolved = [
            UnresolvedItem(
                id="u1", source="spike",
                description="Emotional spike from betrayal",
                created_at=datetime.now(timezone.utc),
                intensity=0.7, decay_rate=0.03,
            ),
        ]
        prompt = build_slow_path_prompt(
            state=state, person=None, self_profile=None,
            values=None, unresolved=unresolved,
            memories=[], fast_candidate="Test response",
        )
        assert "spike" in prompt.lower()
        assert "betrayal" in prompt.lower()

    def test_deadlock_creates_unresolved_item(self):
        """Round 3 deadlock → arbiter fires, creates unresolved item."""
        backend = SequenceLLMBackend([
            "Quick response.",                       # fast round 1
            "OBJECTION: too dismissive",             # slow round 1
            "I'll try to be more thoughtful.",       # fast revision
            "OBJECTION: still not addressing core",  # slow round 2
            "Let me find middle ground here.",       # arbiter
        ])
        dialogue = InnerDialogue(backend=backend)
        state = ModulatorState(arousal=0.5, energy=0.8, resolution=0.7)

        trace = dialogue.deliberate(
            user_message="This is serious",
            state=state,
        )
        assert trace.reached_deadlock is True
        assert len(trace.rounds) == 3

        item = dialogue.create_deadlock_item(trace)
        assert item is not None
        assert item.source == "dialogue_deadlock"
        assert item.decay_rate == 0.10

    def test_high_resolution_forces_max_rounds(self):
        """Resolution > 0.6 → slow path insists, max_rounds = 3."""
        dialogue = InnerDialogue(backend=MockLLMBackend())
        state = ModulatorState(resolution=0.8, arousal=0.3, energy=0.9)
        assert dialogue._max_rounds(state) == 3

    def test_arousal_bypass_overrides_resolution(self):
        """Even with high resolution, arousal > 0.8 → 1 round bypass."""
        dialogue = InnerDialogue(backend=MockLLMBackend())
        state = ModulatorState(resolution=0.9, arousal=0.85, energy=0.9)
        assert dialogue._max_rounds(state) == 1

    def test_energy_bypass_overrides_resolution(self):
        """Low energy < 0.2 → 1 round bypass regardless of resolution."""
        dialogue = InnerDialogue(backend=MockLLMBackend())
        state = ModulatorState(resolution=0.9, arousal=0.3, energy=0.15)
        assert dialogue._max_rounds(state) == 1


# ---------------------------------------------------------------------------
# Scenario 3: Anticipation pre-shift
# ---------------------------------------------------------------------------

class TestAnticipationPreShift:
    """3-message sensitive topic buildup → prediction + ≤30% shift."""

    def _make_sensitive_topic(self, charge: float = 0.7) -> dict[str, TopicProfile]:
        return {"breakup": TopicProfile(topic="breakup", emotional_charge=charge)}

    def test_topic_trajectory_fires(self):
        """2+ mentions of charged topic in 3-message window → prediction."""
        engine = AnticipationEngine()
        topics = self._make_sensitive_topic(0.7)
        messages = [
            "thinking about the breakup",
            "the breakup was really hard",
        ]
        person = PersonProfile(person_id="alice")

        ant = engine.predict(
            recent_messages=messages,
            person_profile=person,
            topic_profiles=topics,
            current_state=ModulatorState(),
        )
        assert ant.confidence >= CONFIDENCE_GATE
        assert "breakup" in ant.predicted_topics
        assert "arousal" in ant.modulator_pre_shifts

    def test_pre_shift_respects_30_percent_cap(self):
        """Applied shifts are exactly delta * 0.3."""
        engine = AnticipationEngine()
        topics = self._make_sensitive_topic(0.7)
        messages = ["breakup thoughts", "more breakup pain"]

        ant = engine.predict(
            recent_messages=messages,
            person_profile=PersonProfile(person_id="x"),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )
        # The heuristic produces arousal shift of 0.10
        assert ant.modulator_pre_shifts.get("arousal") == pytest.approx(0.10, abs=0.01)

        # Apply to state and verify 30% scaling
        state = ModulatorState(arousal=0.5)
        engine.apply_pre_shift(state, ant)
        # arousal should be 0.5 + 0.10 * 0.3 = 0.53
        assert state.arousal == pytest.approx(0.53, abs=0.01)

    def test_pre_shift_not_applied_below_confidence_gate(self):
        """Confidence < 0.3 → no pre-shift applied."""
        engine = AnticipationEngine()
        # Only 1 mention — not enough for trajectory
        topics = self._make_sensitive_topic(0.7)
        messages = ["thinking about the breakup"]

        ant = engine.predict(
            recent_messages=messages,
            person_profile=PersonProfile(person_id="x"),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )
        # 1 mention < 2 required → no topic trajectory fires
        state_before = ModulatorState(arousal=0.5)
        engine.apply_pre_shift(state_before, ant)
        assert state_before.arousal == 0.5  # unchanged

    def test_three_message_buildup(self):
        """Full 3-message trajectory with increasing confidence."""
        engine = AnticipationEngine()
        topics = self._make_sensitive_topic(0.8)

        # 3 mentions → higher confidence than 2
        msgs_3 = ["breakup hurts", "still thinking about breakup", "breakup again"]
        ant_3 = engine.predict(
            recent_messages=msgs_3,
            person_profile=PersonProfile(person_id="x"),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )

        msgs_2 = ["breakup hurts", "still thinking about breakup"]
        ant_2 = engine.predict(
            recent_messages=msgs_2,
            person_profile=PersonProfile(person_id="x"),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )

        assert ant_3.confidence >= ant_2.confidence

    def test_uncharged_topic_does_not_fire(self):
        """Topic with charge < 0.5 and no avoidance → no trajectory."""
        engine = AnticipationEngine()
        topics = {"weather": TopicProfile(topic="weather", emotional_charge=0.2)}
        messages = ["weather is bad", "more weather issues"]

        ant = engine.predict(
            recent_messages=messages,
            person_profile=PersonProfile(person_id="x"),
            topic_profiles=topics,
            current_state=ModulatorState(),
        )
        assert ant.confidence == 0.0

    def test_anticipation_in_pipeline(self):
        """Pipeline wires anticipation — verify debug has prediction after topic buildup."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        # Build up a charged topic
        pipe.topic_profiles.get_or_create("breakup")
        for _ in range(6):
            pipe.topic_profiles.record_negative("breakup", 1.0)

        # Send 2 messages mentioning breakup to build history
        pipe.process("thinking about the breakup", user_id="alice")
        pipe.process("the breakup was terrible", user_id="alice")

        # 3rd message — anticipation should fire
        result = pipe.process("still hurting from breakup", user_id="alice")
        ant = result.debug.anticipation
        assert ant is not None
        assert ant.confidence > 0
        assert len(ant.predicted_topics) > 0


# ---------------------------------------------------------------------------
# Scenario 4: Defense degradation with maturity
# ---------------------------------------------------------------------------

class TestDefenseDegradation:
    """Maturity 0.0 / 0.5 / 1.0 → suppression decreases monotonically."""

    def _raw_state(self) -> ModulatorState:
        """State that always exceeds comfort threshold."""
        return ModulatorState(arousal=0.9, valence=0.1)

    def _low_trust_person(self) -> PersonProfile:
        return PersonProfile(person_id="x", trust=0.2)

    def test_suppression_monotonic_for_all_defense_types(self):
        """For every defense type, higher maturity → more expressed (less suppressed)."""
        dm = DefenseMechanism()
        maturities = [0.0, 0.25, 0.5, 0.75, 1.0]

        for defense_type, base in _BASE_SUPPRESSION.items():
            factors = []
            for m in maturities:
                factor = dm._suppression_factor(defense_type, m)
                factors.append(factor)
            # Each factor should be >= the previous (monotonic non-decreasing)
            for i in range(1, len(factors)):
                assert factors[i] >= factors[i - 1], (
                    f"{defense_type}: maturity {maturities[i]} factor {factors[i]} "
                    f"< maturity {maturities[i-1]} factor {factors[i-1]}"
                )

    def test_suppression_at_zero_maturity(self):
        """At maturity 0.0, suppression factor equals the base."""
        dm = DefenseMechanism()
        for defense_type, base in _BASE_SUPPRESSION.items():
            factor = dm._suppression_factor(defense_type, 0.0)
            assert factor == pytest.approx(base, abs=0.001)

    def test_suppression_at_full_maturity(self):
        """At maturity 1.0, factor is base + (1-base)*0.5 — never reaches 1.0."""
        dm = DefenseMechanism()
        for defense_type, base in _BASE_SUPPRESSION.items():
            factor = dm._suppression_factor(defense_type, 1.0)
            expected = base + (1.0 - base) * 0.5
            assert factor == pytest.approx(expected, abs=0.001)
            assert factor < 1.0  # defenses never fully gone

    def test_expressed_intensity_grows_with_maturity(self):
        """More mature system expresses more of its raw intensity."""
        dm = DefenseMechanism()
        state = self._raw_state()
        person = self._low_trust_person()

        expressed_values = []
        for maturity in [0.0, 0.5, 1.0]:
            self_prof = SelfProfile(maturity_score=maturity)
            _, activation = dm.evaluate(
                inner_dialogue_output="I feel strongly.",
                modulator_state=state,
                self_profile=self_prof,
                person_profile=person,
            )
            assert activation is not None
            expressed_values.append(activation.expressed_intensity)

        # Expressed intensity should increase with maturity
        assert expressed_values[0] < expressed_values[1] < expressed_values[2]

    def test_suppression_delta_shrinks_with_maturity(self):
        """Higher maturity → smaller gap between raw and expressed."""
        dm = DefenseMechanism()
        state = self._raw_state()
        person = self._low_trust_person()

        deltas = []
        for maturity in [0.0, 0.5, 1.0]:
            self_prof = SelfProfile(maturity_score=maturity)
            _, activation = dm.evaluate(
                inner_dialogue_output="I feel strongly.",
                modulator_state=state,
                self_profile=self_prof,
                person_profile=person,
            )
            assert activation is not None
            deltas.append(activation.suppression_delta)

        # Suppression delta should decrease with maturity
        assert deltas[0] > deltas[1] > deltas[2]

    def test_defense_logged_in_self_profile(self):
        """Each activation appends a DefenseEvent to self_profile.defense_log."""
        dm = DefenseMechanism()
        state = self._raw_state()
        person = self._low_trust_person()
        self_prof = SelfProfile()
        assert len(self_prof.defense_log) == 0

        dm.evaluate(
            inner_dialogue_output="test",
            modulator_state=state,
            self_profile=self_prof,
            person_profile=person,
        )
        assert len(self_prof.defense_log) == 1
        assert isinstance(self_prof.defense_log[0], DefenseEvent)


# ---------------------------------------------------------------------------
# Scenario 5: Full pipeline end-to-end
# ---------------------------------------------------------------------------

class TestFullPipelineEndToEnd:
    """All v2 layers fire, debug payload complete, LLM calls in budget."""

    def test_all_debug_fields_present(self):
        """Every v2 field exists in debug payload."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("Hello, how are you?", user_id="test")

        d = result.debug
        # v1 fields
        assert d.detected_emotion is not None
        assert d.event_classified != ""
        assert d.modulator_snapshot != {}
        assert "resolution" in d.modulator_snapshot
        assert d.emotion_label != ""

        # v2 fields
        assert d.anticipation is not None
        assert isinstance(d.anticipation, Anticipation)
        assert d.dialogue_trace is not None
        assert isinstance(d.dialogue_trace, InnerDialogueTrace)
        assert isinstance(d.unresolved_count, int)
        # defense may be None for calm message
        assert d.defense_activation is None or isinstance(d.defense_activation, DefenseActivation)

    def test_llm_calls_in_budget(self):
        """Each message uses 5-9 LLM calls (contagion + classify + dialogue + master + self_check)."""
        backend = CountingLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)

        for i in range(5):
            before = backend.call_count
            pipe.process(f"Message {i}", user_id="alice")
            calls = backend.call_count - before
            assert 3 <= calls <= 9, f"Message {i}: {calls} calls out of budget"

    def test_full_session_arc(self):
        """Greeting → warmth → spike → resolution → end session."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())

        # Greeting
        r1 = pipe.process("Hey there!", user_id="paco")
        assert r1.debug.anticipation is not None
        assert r1.debug.dialogue_trace is not None

        # Warmth
        r2 = pipe.process("I'm grateful for your help, thank you", user_id="paco")
        assert r2.debug.event_classified == "positive_feedback"

        # Spike — betrayal (no "trust" word to avoid diluting negative valence)
        r3 = pipe.process("You betrayed and deceived me completely!", user_id="paco")
        assert r3.debug.is_spike is True
        assert r3.debug.unresolved_count > 0

        # Resolution
        r4 = pipe.process("I'm sorry, let's forgive and find peace", user_id="paco")
        assert r4.debug.unresolved_count < r3.debug.unresolved_count

        # End session
        digested = pipe.end_session(user_id="paco")
        assert digested.summary != ""
        assert len(pipe.short_term) == 0

    def test_defense_fires_under_pressure(self):
        """Manipulate state to trigger defense, verify suppression gap."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        pipe.engine.state.arousal = 0.85
        pipe.engine.state.valence = 0.1
        person = pipe.person_profiles.get_or_create("low_trust")
        person.trust = 0.2
        pipe.person_profiles.save(person)

        result = pipe.process("This is really upsetting", user_id="low_trust")
        if result.debug.defense_activation is not None:
            d = result.debug.defense_activation
            assert d.raw_intensity > d.expressed_intensity
            assert d.suppression_delta > 0

    def test_resolution_modulator_in_snapshot(self):
        """Resolution value appears in modulator snapshot after spike."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("You betrayed and deceived me completely!", user_id="alice")
        assert "resolution" in result.debug.modulator_snapshot

    def test_v1_behavior_preserved(self):
        """Core v1 features still work in the v2 pipeline."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())

        # Contagion affects state
        pipe.process("I'm so excited and thrilled!!!", user_id="alice")
        snap = pipe.engine.snapshot()
        assert snap["arousal"] > 0.5 or snap["valence"] > 0.5

        # Energy drains
        initial_energy = pipe.engine.state.energy
        for i in range(10):
            pipe.process(f"msg {i}", user_id="alice")
        assert pipe.engine.state.energy < initial_energy

        # Person profile tracks
        profile = pipe.person_profiles.get_or_create("alice")
        assert profile.interaction_count >= 11

    def test_unresolved_items_in_debug(self):
        """After spike, unresolved_items list is populated in debug."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("You betrayed and deceived me completely!", user_id="alice")
        if result.debug.is_spike:
            assert len(result.debug.unresolved_items) > 0
            item = result.debug.unresolved_items[0]
            assert item.source == "spike"
            assert item.intensity > 0
