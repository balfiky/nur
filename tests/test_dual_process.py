"""Tests for dual process (generator + self-check) — Phase 5."""

import pytest

from core.types import (
    ModulatorState,
    OpenLoop,
    PersonProfile,
    PipelineContext,
    RelationshipContext,
    RelationshipEvent,
    SemanticMemoryEntry,
    SelfProfile,
    TopicProfile,
    ValueHierarchy,
    LongTermEntry,
)
from config.loader import get_config
from core.dual_process.generator import (
    GenerationResult,
    MockLLMBackend,
    ResponseGenerator,
    build_system_prompt,
)
from core.dual_process.self_check import SelfCheckResult, SelfChecker


# =========================================================================
# System prompt builder
# =========================================================================

class TestBuildSystemPrompt:
    def test_includes_modulator_state(self):
        ctx = PipelineContext(
            modulator_snapshot={"arousal": 0.8, "valence": 0.3, "certainty": 0.5,
                                "bonding": 0.5, "energy": 0.7},
        )
        prompt = build_system_prompt(ctx)
        assert "arousal: 0.80" in prompt
        assert "valence: 0.30" in prompt

    def test_includes_self_profile(self):
        ctx = PipelineContext(
            modulator_snapshot={},
            self_profile=SelfProfile(
                strengths=["empathetic"],
                flaws=["blunt"],
                triggers=["criticism"],
                dissonance=0.4,
            ),
        )
        prompt = build_system_prompt(ctx)
        assert "empathetic" in prompt
        assert "blunt" in prompt
        assert "criticism" in prompt
        assert "Dissonance" in prompt

    def test_includes_person_profile(self):
        ctx = PipelineContext(
            modulator_snapshot={},
            person_profile=PersonProfile(
                person_id="alice", name="Alice", trust=0.8,
                interaction_count=15, stress_response="seeks_support",
            ),
        )
        prompt = build_system_prompt(ctx)
        assert "Alice" in prompt
        assert "0.80" in prompt

    def test_includes_topic_avoidance(self):
        ctx = PipelineContext(
            modulator_snapshot={},
            topic_profiles=[TopicProfile(topic="work", emotional_charge=0.8, avoidance=True)],
        )
        prompt = build_system_prompt(ctx)
        assert "AVOID" in prompt
        assert "work" in prompt

    def test_includes_values(self):
        ctx = PipelineContext(modulator_snapshot={})
        prompt = build_system_prompt(ctx)
        assert "loyalty" in prompt
        assert "honesty" in prompt

    def test_includes_soul_profile(self):
        ctx = PipelineContext(
            modulator_snapshot={},
            soul_profile=get_config().soul,
        )
        prompt = build_system_prompt(ctx)
        assert "Soul Seed" in prompt
        assert get_config().soul.identity in prompt

    def test_includes_semantic_memories(self):
        ctx = PipelineContext(
            modulator_snapshot={},
            semantic_memories=[
                SemanticMemoryEntry(
                    kind="preference",
                    summary="User preference: concise replies",
                ),
            ],
        )
        prompt = build_system_prompt(ctx)
        assert "Semantic Memory" in prompt
        assert "concise replies" in prompt

    def test_includes_memories(self):
        ctx = PipelineContext(
            modulator_snapshot={},
            retrieved_memories=[
                LongTermEntry(summary="Big argument last week", emotional_valence=-0.7, spike=True),
            ],
        )
        prompt = build_system_prompt(ctx)
        assert "Big argument" in prompt
        assert "[SPIKE]" in prompt

    def test_includes_relationship_context(self):
        ctx = PipelineContext(
            modulator_snapshot={},
            relationship_context=RelationshipContext(
                summary="Open loops: unresolved tension about deadline.",
                active_loops=[
                    OpenLoop(
                        loop_kind="tension",
                        source_person="alice",
                        topic="deadline",
                        description="unresolved tension about deadline",
                        intensity=0.7,
                    )
                ],
                recent_events=[
                    RelationshipEvent(
                        event_kind="repair",
                        source_person="alice",
                        topic="deadline",
                        summary="Repair around deadline",
                        valence=0.6,
                        intensity=0.6,
                        confidence=0.8,
                    )
                ],
                open_loop_count=1,
            ),
        )
        prompt = build_system_prompt(ctx)
        assert "Relationship context" in prompt
        assert "Open loop" in prompt
        assert "Recent relationship event" in prompt

    def test_includes_contradiction_flags(self):
        ctx = PipelineContext(
            modulator_snapshot={},
            contradiction_flags=["Trust dropped unexpectedly"],
        )
        prompt = build_system_prompt(ctx)
        assert "Trust dropped" in prompt

    def test_behavioral_guidance_low_energy(self):
        ctx = PipelineContext(
            modulator_snapshot={"energy": 0.1, "valence": 0.5, "certainty": 0.5, "arousal": 0.5},
        )
        prompt = build_system_prompt(ctx)
        assert "LOW on energy" in prompt

    def test_behavioral_guidance_high_certainty(self):
        ctx = PipelineContext(
            modulator_snapshot={"energy": 0.7, "valence": 0.5, "certainty": 0.9, "arousal": 0.5},
        )
        prompt = build_system_prompt(ctx)
        assert "blunt" in prompt.lower()

    def test_empty_context(self):
        ctx = PipelineContext()
        prompt = build_system_prompt(ctx)
        assert "Jarvis" in prompt


# =========================================================================
# ResponseGenerator
# =========================================================================

class TestResponseGenerator:
    def test_generate_returns_result(self):
        backend = MockLLMBackend(response="Hello there!")
        gen = ResponseGenerator(backend=backend)
        result = gen.generate(PipelineContext(), "Hi")
        assert isinstance(result, GenerationResult)
        assert result.response == "Hello there!"
        assert result.system_prompt != ""

    def test_passes_context_to_backend(self):
        backend = MockLLMBackend()
        gen = ResponseGenerator(backend=backend)
        ctx = PipelineContext(
            modulator_snapshot={"arousal": 0.9, "valence": 0.5, "certainty": 0.5,
                                "bonding": 0.5, "energy": 0.5},
        )
        gen.generate(ctx, "How are you?")
        assert "arousal: 0.90" in backend.last_system_prompt
        assert "How are you?" in backend.last_user_message

    def test_includes_conversation_history(self):
        backend = MockLLMBackend()
        gen = ResponseGenerator(backend=backend)
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        gen.generate(PipelineContext(), "What's up?", conversation_history=history)
        assert "Hello" in backend.last_user_message
        assert "Hi there" in backend.last_user_message
        assert "What's up?" in backend.last_user_message

    def test_call_count(self):
        backend = MockLLMBackend()
        gen = ResponseGenerator(backend=backend)
        gen.generate(PipelineContext(), "a")
        gen.generate(PipelineContext(), "b")
        assert backend.call_count == 2

    def test_default_mock_backend(self):
        gen = ResponseGenerator()
        result = gen.generate(PipelineContext(), "test")
        assert result.response == "I understand."


# =========================================================================
# SelfChecker
# =========================================================================

class TestSelfChecker:
    def test_passes_neutral_response(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.5, "energy": 0.7, "arousal": 0.5},
        )
        result = checker.check("I see what you mean. Let me think about that.", ctx)
        assert result.passed

    def test_fails_positive_tone_in_negative_mood(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.1, "certainty": 0.5, "energy": 0.7, "arousal": 0.5},
        )
        result = checker.check(
            "That's great! What a wonderful and amazing opportunity!",
            ctx,
        )
        assert result.failed
        assert any("too positive" in i.lower() for i in result.issues)

    def test_fails_overconfident_when_uncertain(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.15, "energy": 0.7, "arousal": 0.5},
        )
        result = checker.check(
            "Obviously this is the right approach. Definitely go with option A.",
            ctx,
        )
        assert result.failed
        assert any("overconfident" in i.lower() for i in result.issues)

    def test_passes_hedging_when_uncertain(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.15, "energy": 0.7, "arousal": 0.5},
        )
        result = checker.check(
            "I'm not entirely sure, but it might be worth considering option A.",
            ctx,
        )
        assert result.passed

    def test_fails_blunt_when_known_flaw(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.9, "energy": 0.7, "arousal": 0.5},
            self_profile=SelfProfile(flaws=["blunt"]),
        )
        result = checker.check(
            "You should just do it. That's wrong and you need to stop.",
            ctx,
        )
        assert result.failed
        assert any("blunt" in i.lower() for i in result.issues)

    def test_no_bluntness_check_without_flaw(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.9, "energy": 0.7, "arousal": 0.5},
            self_profile=SelfProfile(flaws=[]),
        )
        result = checker.check(
            "You should just do it. That's wrong.",
            ctx,
        )
        # No bluntness issue since it's not a known flaw
        assert not any("blunt" in i.lower() for i in result.issues)

    def test_fails_verbose_when_exhausted(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.5, "energy": 0.1, "arousal": 0.5},
        )
        long_response = "This is a detailed explanation. " * 50
        result = checker.check(long_response, ctx)
        assert result.failed
        assert any("energy" in i.lower() for i in result.issues)

    def test_passes_short_when_exhausted(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.5, "energy": 0.1, "arousal": 0.5},
        )
        result = checker.check("Got it.", ctx)
        assert result.passed

    def test_flags_ignored_contradictions(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.5, "energy": 0.7, "arousal": 0.5},
            contradiction_flags=[
                "Trust behavior inconsistent",
                "Emotional volatility shifted",
            ],
        )
        result = checker.check(
            "Sure, let me help you with that task right away.",
            ctx,
        )
        assert result.failed
        assert any("contradiction" in i.lower() for i in result.issues)

    def test_passes_acknowledged_contradictions(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.5, "energy": 0.7, "arousal": 0.5},
            contradiction_flags=[
                "Trust behavior inconsistent",
                "Emotional volatility shifted",
            ],
        )
        result = checker.check(
            "I notice something seems different about our conversation today.",
            ctx,
        )
        # Acknowledges the shift, so contradiction check passes
        assert not any("contradiction" in i.lower() for i in result.issues)

    def test_correction_note_format(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.1, "certainty": 0.5, "energy": 0.7, "arousal": 0.5},
        )
        result = checker.check(
            "That's great! What a wonderful and amazing day!",
            ctx,
        )
        assert result.correction_note != ""
        assert "Self-check issues" in result.correction_note

    def test_multiple_issues(self):
        checker = SelfChecker()
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.1, "certainty": 0.1, "energy": 0.1, "arousal": 0.5},
            self_profile=SelfProfile(flaws=["blunt"]),
        )
        long_positive = "That's absolutely great! " * 30 + "You definitely need to do this."
        result = checker.check(long_positive, ctx)
        assert len(result.issues) >= 2

    def test_self_checker_with_llm_passing(self):
        """LLM-based self-check that passes."""
        import json
        llm_response = json.dumps({"passed": True, "issues": [], "correction_note": ""})
        backend = MockLLMBackend(response=llm_response)
        checker = SelfChecker(llm_client=backend)
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.5, "energy": 0.7, "arousal": 0.5},
        )
        result = checker.check("A reasonable response.", ctx)
        assert result.passed

    def test_self_checker_with_llm_failing(self):
        """LLM-based self-check that catches an issue."""
        import json
        llm_response = json.dumps({
            "passed": False,
            "issues": ["Tone mismatch: too cheerful for low valence"],
            "correction_note": "Dampen enthusiasm",
        })
        backend = MockLLMBackend(response=llm_response)
        checker = SelfChecker(llm_client=backend)
        ctx = PipelineContext(
            modulator_snapshot={"valence": 0.5, "certainty": 0.5, "energy": 0.7, "arousal": 0.5},
        )
        result = checker.check("A reasonable response.", ctx)
        # LLM found an issue, rule-based didn't — should still flag it
        assert any("Tone mismatch" in i for i in result.issues)
