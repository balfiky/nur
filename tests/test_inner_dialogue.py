"""Tests for the inner dialogue — v2 Phase 3."""

from datetime import datetime, timedelta, timezone

import pytest

from core.types import (
    DialogueRound,
    InnerDialogueTrace,
    LongTermEntry,
    ModulatorState,
    PersonProfile,
    SelfProfile,
    UnresolvedItem,
    ValueHierarchy,
)
from core.dual_process.inner_dialogue import (
    AROUSAL_BYPASS_THRESHOLD,
    ENERGY_BYPASS_THRESHOLD,
    MAX_ROUNDS,
    RESOLUTION_INSIST_THRESHOLD,
    InnerDialogue,
    parse_slow_path_response,
    build_fast_path_prompt,
    build_slow_path_prompt,
    build_revision_prompt,
    build_arbiter_prompt,
)


# ---------------------------------------------------------------------------
# Controllable mock backend
# ---------------------------------------------------------------------------

class SequenceLLMBackend:
    """Returns responses from a pre-defined sequence.

    Allows tests to script exact fast/slow/arbiter outputs.
    """

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


# ---------------------------------------------------------------------------
# parse_slow_path_response
# ---------------------------------------------------------------------------

class TestParseSlowPath:
    def test_approved_with_reason(self):
        approved, reason, parsed = parse_slow_path_response("APPROVED: tone is appropriate")
        assert approved is True
        assert parsed is True
        assert "tone is appropriate" in reason

    def test_objection_with_reason(self):
        approved, reason, parsed = parse_slow_path_response("OBJECTION: too aggressive for low trust")
        assert approved is False
        assert parsed is True
        assert "too aggressive" in reason

    def test_approved_case_insensitive(self):
        approved, _, parsed = parse_slow_path_response("approved: looks good")
        assert approved is True
        assert parsed is True

    def test_objection_case_insensitive(self):
        approved, _, parsed = parse_slow_path_response("objection: needs softening")
        assert approved is False
        assert parsed is True

    def test_mock_response_treated_as_objection(self):
        """MockLLMBackend returns 'I understand.' — unparseable = objection, not auto-approve."""
        approved, _, parsed = parse_slow_path_response("I understand.")
        assert approved is False
        assert parsed is False

    def test_approved_keyword_in_text(self):
        approved, _, parsed = parse_slow_path_response("The response is APPROVED because it fits.")
        assert approved is True
        assert parsed is True

    def test_objection_keyword_in_text(self):
        approved, _, parsed = parse_slow_path_response("I have an OBJECTION to the tone used here.")
        assert approved is False
        assert parsed is True

    def test_both_keywords_objection_wins(self):
        """If both keywords present, OBJECTION takes precedence."""
        approved, _, parsed = parse_slow_path_response("OBJECTION despite being APPROVED before")
        assert approved is False
        assert parsed is True


# ---------------------------------------------------------------------------
# Round 1 — approval path
# ---------------------------------------------------------------------------

class TestRound1Approval:
    def test_approved_round_1_two_llm_calls(self):
        """Fast generates, slow approves → 2 LLM calls, done."""
        backend = SequenceLLMBackend([
            "I hear you and I'm here for you.",          # fast path
            "APPROVED: empathetic and appropriate tone",  # slow path
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="I'm feeling down",
            state=ModulatorState(),
        )
        assert len(trace.rounds) == 1
        assert trace.rounds[0].slow_path_approved is True
        assert trace.total_llm_calls == 2
        assert trace.final_candidate == "I hear you and I'm here for you."
        assert trace.reached_deadlock is False
        assert trace.tension_level == 0.0

    def test_approved_round_1_trace_structure(self):
        backend = SequenceLLMBackend([
            "Gut response here.",
            "APPROVED: all good",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate("hello", state=ModulatorState())
        r = trace.rounds[0]
        assert r.round_number == 1
        assert r.fast_path_candidate == "Gut response here."
        assert "APPROVED" in r.slow_path_evaluation
        assert r.objection_reason is None


# ---------------------------------------------------------------------------
# Round 2 — revision after objection
# ---------------------------------------------------------------------------

class TestRound2Revision:
    def test_objection_then_approval(self):
        """Slow objects round 1, fast revises, slow approves round 2 → 4 calls."""
        backend = SequenceLLMBackend([
            "You're overreacting.",                              # fast round 1
            "OBJECTION: too dismissive for someone in distress", # slow round 1
            "I hear your frustration. Let's talk about it.",     # fast revision
            "APPROVED: much better tone",                        # slow round 2
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="I'm really upset",
            state=ModulatorState(),
        )
        assert len(trace.rounds) == 2
        assert trace.rounds[0].slow_path_approved is False
        assert trace.rounds[0].objection_reason is not None
        assert trace.rounds[1].slow_path_approved is True
        assert trace.total_llm_calls == 4
        assert trace.final_candidate == "I hear your frustration. Let's talk about it."
        assert trace.reached_deadlock is False
        assert trace.dominant_path == "slow"

    def test_revision_notes_recorded(self):
        backend = SequenceLLMBackend([
            "Whatever.",
            "OBJECTION: dismissive",
            "I see your point.",
            "APPROVED: ok now",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate("help", state=ModulatorState())
        assert trace.rounds[1].revision_notes is not None
        assert "dismissive" in trace.rounds[1].revision_notes

    def test_tension_level_after_one_objection(self):
        backend = SequenceLLMBackend([
            "response 1",
            "OBJECTION: reason",
            "response 2",
            "APPROVED: ok",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate("msg", state=ModulatorState())
        # 1 objection out of 2 rounds = 0.5 tension
        assert trace.tension_level == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# Round 3 — deadlock + arbiter
# ---------------------------------------------------------------------------

class TestRound3Deadlock:
    def test_deadlock_fires_arbiter(self):
        """Two objections → arbiter synthesizes → 5 calls."""
        backend = SequenceLLMBackend([
            "Blunt response.",                              # fast round 1
            "OBJECTION: too blunt",                         # slow round 1
            "Slightly less blunt.",                         # fast revision
            "OBJECTION: still too harsh",                   # slow round 2
            "I understand your concern, and here's my take.",  # arbiter
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="What do you think?",
            state=ModulatorState(),
        )
        assert len(trace.rounds) == 3
        assert trace.total_llm_calls == 5
        assert trace.reached_deadlock is True
        assert trace.deadlock_resolution == "arbiter"
        assert trace.dominant_path == "arbiter"
        assert trace.final_candidate == "I understand your concern, and here's my take."

    def test_deadlock_tension_is_high(self):
        backend = SequenceLLMBackend([
            "r1", "OBJECTION: x",
            "r2", "OBJECTION: y",
            "synthesis",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate("msg", state=ModulatorState())
        # 2 objections out of 3 rounds ≈ 0.67
        assert trace.tension_level > 0.5

    def test_deadlock_creates_unresolved_item(self):
        backend = SequenceLLMBackend([
            "r1", "OBJECTION: tone wrong",
            "r2", "OBJECTION: still wrong",
            "arbiter synthesis",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate("msg", state=ModulatorState())
        item = dialogue.create_deadlock_item(trace)
        assert item is not None
        assert item.source == "dialogue_deadlock"
        assert item.decay_rate == 0.10
        assert 0.4 <= item.intensity <= 0.6
        assert "tone wrong" in item.description or "still wrong" in item.description

    def test_no_deadlock_no_unresolved_item(self):
        backend = SequenceLLMBackend([
            "good response", "APPROVED: fine",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate("msg", state=ModulatorState())
        assert dialogue.create_deadlock_item(trace) is None


# ---------------------------------------------------------------------------
# Control dynamics — arousal bypass
# ---------------------------------------------------------------------------

class TestArousalBypass:
    def test_high_arousal_skips_slow_path(self):
        """Arousal > 0.8 → fast path only, 1 LLM call."""
        backend = SequenceLLMBackend([
            "Quick emotional response!",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="This is urgent!",
            state=ModulatorState(arousal=0.9),
        )
        assert len(trace.rounds) == 1
        assert trace.total_llm_calls == 1
        assert trace.dominant_path == "fast"
        assert trace.rounds[0].slow_path_approved is True
        assert "bypassed" in trace.rounds[0].slow_path_evaluation.lower()

    def test_arousal_at_threshold_bypasses(self):
        """Arousal exactly at 0.8 still triggers (> check)."""
        backend = SequenceLLMBackend([
            "fast only",
        ])
        dialogue = InnerDialogue(backend=backend)
        # 0.8 is not > 0.8, so should NOT bypass
        trace = dialogue.deliberate(
            user_message="test",
            state=ModulatorState(arousal=0.8),
        )
        # At exactly 0.8, does not bypass (> not >=)
        assert trace.total_llm_calls >= 2

    def test_arousal_just_above_bypasses(self):
        backend = SequenceLLMBackend(["fast only"])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="test",
            state=ModulatorState(arousal=0.81),
        )
        assert trace.total_llm_calls == 1


# ---------------------------------------------------------------------------
# Control dynamics — energy bypass
# ---------------------------------------------------------------------------

class TestEnergyBypass:
    def test_low_energy_skips_slow_path(self):
        """Energy < 0.2 → fast path only, 1 LLM call."""
        backend = SequenceLLMBackend([
            "Too tired to think deeply.",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="How are you?",
            state=ModulatorState(energy=0.1),
        )
        assert len(trace.rounds) == 1
        assert trace.total_llm_calls == 1
        assert trace.dominant_path == "fast"
        assert "bypassed" in trace.rounds[0].slow_path_evaluation.lower()

    def test_energy_at_threshold_does_not_bypass(self):
        """Energy exactly 0.2 is not < 0.2."""
        backend = SequenceLLMBackend([
            "response", "APPROVED: ok",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="test",
            state=ModulatorState(energy=0.2),
        )
        assert trace.total_llm_calls >= 2

    def test_energy_just_below_bypasses(self):
        backend = SequenceLLMBackend(["tired response"])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="test",
            state=ModulatorState(energy=0.19),
        )
        assert trace.total_llm_calls == 1


# ---------------------------------------------------------------------------
# Control dynamics — resolution insistence
# ---------------------------------------------------------------------------

class TestResolutionInsistence:
    def test_high_resolution_allows_all_rounds(self):
        """Resolution > 0.6 → slow path gets all 3 rounds."""
        backend = SequenceLLMBackend([
            "r1", "OBJECTION: ignoring unresolved tension",
            "r2", "OBJECTION: still not addressing it",
            "arbiter synthesis",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="Let's talk about something else",
            state=ModulatorState(resolution=0.7),
        )
        assert len(trace.rounds) == 3
        assert trace.reached_deadlock is True
        assert trace.total_llm_calls == 5

    def test_resolution_below_threshold_still_allows_rounds(self):
        """Default is also 3 rounds max, but resolution insistence is explicit."""
        backend = SequenceLLMBackend([
            "r1", "OBJECTION: reason",
            "r2", "APPROVED: ok",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="test",
            state=ModulatorState(resolution=0.3),
        )
        assert len(trace.rounds) == 2

    def test_unresolved_items_in_slow_path_prompt(self):
        """Slow path prompt should include unresolved items."""
        items = [
            UnresolvedItem(
                id="u1", source="contradiction",
                description="said they love mornings but hate waking up",
                created_at=datetime.now(timezone.utc),
                intensity=0.6, decay_rate=0.02,
            ),
        ]
        backend = SequenceLLMBackend([
            "response",
            "APPROVED: ok",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="good morning",
            state=ModulatorState(),
            unresolved=items,
        )
        # Check that the slow path prompt contained the unresolved item
        assert len(backend.prompts) == 2
        slow_prompt = backend.prompts[1][0]
        assert "mornings" in slow_prompt or "waking up" in slow_prompt


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

class TestPromptBuilders:
    def test_fast_path_prompt_contains_state(self):
        prompt = build_fast_path_prompt(
            state=ModulatorState(arousal=0.9),
            person=PersonProfile(person_id="alice", name="Alice", trust=0.8),
            contagion_summary="user seems upset",
            short_term_summary="3 recent exchanges",
        )
        assert "arousal=0.90" in prompt
        assert "Alice" in prompt

    def test_slow_path_prompt_contains_candidate(self):
        prompt = build_slow_path_prompt(
            state=ModulatorState(),
            person=None,
            self_profile=SelfProfile(flaws=["blunt"]),
            values=None,
            unresolved=[],
            memories=[],
            fast_candidate="You're wrong about that.",
        )
        assert "You're wrong about that." in prompt

    def test_revision_prompt_contains_objection(self):
        prompt = build_revision_prompt(
            state=ModulatorState(),
            person=None,
            previous_candidate="Blunt thing",
            objection="Too aggressive for low trust",
        )
        assert "Blunt thing" in prompt
        assert "Too aggressive" in prompt

    def test_arbiter_prompt_contains_both_sides(self):
        prompt = build_arbiter_prompt(
            state=ModulatorState(),
            fast_final="I feel strongly about this",
            slow_final="We should be more careful",
        )
        assert "I feel strongly" in prompt
        assert "more careful" in prompt

    def test_slow_path_prompt_with_memories(self):
        memories = [
            LongTermEntry(summary="Big fight last week", emotional_valence=-0.8, spike=True),
        ]
        prompt = build_slow_path_prompt(
            state=ModulatorState(),
            person=None,
            self_profile=None,
            values=ValueHierarchy(),
            unresolved=[],
            memories=memories,
            fast_candidate="test",
        )
        assert "Big fight" in prompt


# ---------------------------------------------------------------------------
# Trace integrity
# ---------------------------------------------------------------------------

class TestTraceIntegrity:
    def test_trace_llm_calls_match_actual(self):
        """Total LLM calls in trace must match backend call count."""
        backend = SequenceLLMBackend([
            "r1", "OBJECTION: x",
            "r2", "APPROVED: ok",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate("msg", state=ModulatorState())
        assert trace.total_llm_calls == backend.call_count

    def test_round_numbers_sequential(self):
        backend = SequenceLLMBackend([
            "r1", "OBJECTION: x",
            "r2", "OBJECTION: y",
            "arbiter",
        ])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate("msg", state=ModulatorState())
        for i, r in enumerate(trace.rounds):
            assert r.round_number == i + 1

    def test_default_mock_backend_unparseable_triggers_retry(self):
        """Default MockLLMBackend returns 'I understand.' — unparseable triggers
        retry then objection, leading to multi-round deliberation."""
        dialogue = InnerDialogue()
        trace = dialogue.deliberate("hello", state=ModulatorState())
        # Mock returns "I understand." which is unparseable → retry → still unparseable → objection
        # This causes round 2+ (revision + slow check + retry + possibly arbiter)
        assert len(trace.rounds) >= 2
        assert trace.rounds[0].slow_path_approved is False
        assert trace.total_llm_calls >= 4


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_combined_high_arousal_and_high_resolution(self):
        """Arousal bypass takes precedence over resolution insistence."""
        backend = SequenceLLMBackend(["fast only"])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="emergency!",
            state=ModulatorState(arousal=0.9, resolution=0.8),
        )
        # Arousal bypass wins
        assert trace.total_llm_calls == 1
        assert trace.dominant_path == "fast"

    def test_combined_low_energy_and_high_resolution(self):
        """Energy bypass takes precedence over resolution insistence."""
        backend = SequenceLLMBackend(["tired response"])
        dialogue = InnerDialogue(backend=backend)
        trace = dialogue.deliberate(
            user_message="let's discuss",
            state=ModulatorState(energy=0.1, resolution=0.8),
        )
        assert trace.total_llm_calls == 1
        assert trace.dominant_path == "fast"

    def test_person_profile_in_prompts(self):
        person = PersonProfile(
            person_id="bob", name="Bob",
            trust=0.3, interaction_count=20,
        )
        backend = SequenceLLMBackend([
            "response", "APPROVED: ok",
        ])
        dialogue = InnerDialogue(backend=backend)
        dialogue.deliberate("hi bob", state=ModulatorState(), person=person)
        fast_prompt = backend.prompts[0][0]
        assert "Bob" in fast_prompt
        assert "trust=0.30" in fast_prompt

    def test_self_profile_in_slow_path(self):
        self_p = SelfProfile(
            strengths=["empathetic"],
            flaws=["avoidant"],
            triggers=["criticism"],
        )
        backend = SequenceLLMBackend([
            "response", "APPROVED: ok",
        ])
        dialogue = InnerDialogue(backend=backend)
        dialogue.deliberate("test", state=ModulatorState(), self_profile=self_p)
        slow_prompt = backend.prompts[1][0]
        assert "empathetic" in slow_prompt
        assert "avoidant" in slow_prompt
