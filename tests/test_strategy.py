"""Tests for Phase 11.3 — Response Strategy Selector."""

from __future__ import annotations

import pytest

from core.strategy import select_strategy, STRATEGY_INSTRUCTIONS
from core.types import (
    AppraisalFrame,
    PersonProfile,
    RelationshipContext,
    ResponseStrategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _modulators(**overrides: float) -> dict[str, float]:
    base = {
        "arousal": 0.5,
        "valence": 0.5,
        "certainty": 0.5,
        "bonding": 0.5,
        "energy": 0.8,
        "resolution": 0.0,
    }
    base.update(overrides)
    return base


def _appraisal(**overrides) -> AppraisalFrame:
    return AppraisalFrame(**overrides)


def _person(trust: float = 0.5) -> PersonProfile:
    return PersonProfile(person_id="test", trust=trust)


def _relationship(open_loops: int = 0) -> RelationshipContext:
    return RelationshipContext(open_loop_count=open_loops)


# ---------------------------------------------------------------------------
# Priority 1: set_boundary — attack + low trust
# ---------------------------------------------------------------------------

class TestSetBoundary:
    def test_attack_low_trust(self):
        s = select_strategy(
            appraisal=_appraisal(social_move="attack", targets_assistant=True, blame=0.9),
            modulators=_modulators(),
            person=_person(trust=0.2),
        )
        assert s == ResponseStrategy.SET_BOUNDARY

    def test_attack_high_trust_is_not_boundary(self):
        """High trust means the attack is aberrant — repair, not boundary."""
        s = select_strategy(
            appraisal=_appraisal(social_move="attack", targets_assistant=True, blame=0.9),
            modulators=_modulators(),
            person=_person(trust=0.6),
        )
        assert s != ResponseStrategy.SET_BOUNDARY


# ---------------------------------------------------------------------------
# Priority 2: repair — assistant-targeted blame, moderate trust
# ---------------------------------------------------------------------------

class TestRepair:
    def test_targeted_blame_moderate_trust(self):
        s = select_strategy(
            appraisal=_appraisal(
                social_move="complaint",
                targets_assistant=True,
                blame=0.7,
            ),
            modulators=_modulators(),
            person=_person(trust=0.5),
        )
        assert s == ResponseStrategy.REPAIR

    def test_targeted_blame_very_low_trust_not_repair(self):
        """Trust below 0.3 → not worth repairing, may hit boundary instead."""
        s = select_strategy(
            appraisal=_appraisal(
                social_move="complaint",
                targets_assistant=True,
                blame=0.7,
            ),
            modulators=_modulators(),
            person=_person(trust=0.2),
        )
        assert s != ResponseStrategy.REPAIR


# ---------------------------------------------------------------------------
# Priority 3: give_space — low energy or withdrawal
# ---------------------------------------------------------------------------

class TestGiveSpace:
    def test_very_low_energy(self):
        s = select_strategy(
            appraisal=_appraisal(),
            modulators=_modulators(energy=0.1),
        )
        assert s == ResponseStrategy.GIVE_SPACE

    def test_normal_energy_not_give_space(self):
        """Normal energy should not trigger give_space."""
        s = select_strategy(
            appraisal=_appraisal(),
            modulators=_modulators(energy=0.5),
        )
        assert s != ResponseStrategy.GIVE_SPACE


# ---------------------------------------------------------------------------
# Priority 4: ground — high arousal + mixed/uncertain
# ---------------------------------------------------------------------------

class TestGround:
    def test_high_arousal_mixed_affect(self):
        s = select_strategy(
            appraisal=_appraisal(mixed_affect=True),
            modulators=_modulators(arousal=0.8),
        )
        assert s == ResponseStrategy.GROUND

    def test_high_arousal_low_certainty(self):
        s = select_strategy(
            appraisal=_appraisal(),
            modulators=_modulators(arousal=0.8, certainty=0.2),
        )
        assert s == ResponseStrategy.GROUND


# ---------------------------------------------------------------------------
# Priority 5: validate — vulnerability + external target
# ---------------------------------------------------------------------------

class TestValidate:
    def test_vulnerability_external(self):
        s = select_strategy(
            appraisal=_appraisal(
                vulnerability=0.7,
                primary_target="external",
                targets_assistant=False,
            ),
            modulators=_modulators(),
        )
        assert s == ResponseStrategy.VALIDATE

    def test_vulnerability_self_target(self):
        s = select_strategy(
            appraisal=_appraisal(
                vulnerability=0.7,
                primary_target="self",
                targets_assistant=False,
            ),
            modulators=_modulators(),
        )
        assert s == ResponseStrategy.VALIDATE

    def test_low_valence_fallback(self):
        """Negative mood without specific vulnerability → still validate."""
        s = select_strategy(
            appraisal=_appraisal(),
            modulators=_modulators(valence=0.2),
        )
        assert s == ResponseStrategy.VALIDATE


# ---------------------------------------------------------------------------
# Priority 6: reassure — moderate vulnerability + bonding
# ---------------------------------------------------------------------------

class TestReassure:
    def test_moderate_vulnerability_decent_bonding(self):
        s = select_strategy(
            appraisal=_appraisal(vulnerability=0.4, targets_assistant=False),
            modulators=_modulators(bonding=0.6),
        )
        assert s == ResponseStrategy.REASSURE

    def test_default_fallback(self):
        """Neutral everything → reassure as safe default."""
        s = select_strategy(
            appraisal=_appraisal(),
            modulators=_modulators(),
        )
        assert s == ResponseStrategy.REASSURE


# ---------------------------------------------------------------------------
# Priority 7: practical_help — action request + controllable
# ---------------------------------------------------------------------------

class TestPracticalHelp:
    def test_action_request_controllable(self):
        s = select_strategy(
            appraisal=_appraisal(
                inferred_intent="seek_action",
                controllability=0.7,
            ),
            modulators=_modulators(),
        )
        assert s == ResponseStrategy.PRACTICAL_HELP

    def test_support_request_controllable(self):
        s = select_strategy(
            appraisal=_appraisal(
                inferred_intent="seek_support",
                controllability=0.6,
            ),
            modulators=_modulators(),
        )
        assert s == ResponseStrategy.PRACTICAL_HELP


# ---------------------------------------------------------------------------
# Priority 8: challenge_gently — open loops + trust
# ---------------------------------------------------------------------------

class TestChallengeGently:
    def test_open_loops_with_trust(self):
        s = select_strategy(
            appraisal=_appraisal(),
            modulators=_modulators(),
            person=_person(trust=0.6),
            relationship=_relationship(open_loops=2),
        )
        assert s == ResponseStrategy.CHALLENGE_GENTLY

    def test_open_loops_low_trust_not_challenge(self):
        s = select_strategy(
            appraisal=_appraisal(),
            modulators=_modulators(),
            person=_person(trust=0.3),
            relationship=_relationship(open_loops=2),
        )
        assert s != ResponseStrategy.CHALLENGE_GENTLY


# ---------------------------------------------------------------------------
# Strategy instructions completeness
# ---------------------------------------------------------------------------

class TestStrategyInstructions:
    def test_all_strategies_have_instructions(self):
        for strat in ResponseStrategy:
            assert strat in STRATEGY_INSTRUCTIONS, f"Missing instruction for {strat}"
            assert len(STRATEGY_INSTRUCTIONS[strat]) > 10

    def test_enum_values_are_strings(self):
        for strat in ResponseStrategy:
            assert isinstance(strat.value, str)


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

class TestPipelineIntegration:
    def test_strategy_populated_in_debug(self):
        from core.dual_process.generator import MockLLMBackend
        from pipeline import CognitivePipeline

        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("I'm really stressed about work", user_id="alice")
        assert result.debug.response_strategy != ""
        assert result.debug.response_strategy in [s.value for s in ResponseStrategy]

    def test_strategy_validate_for_external_distress(self):
        from core.dual_process.generator import MockLLMBackend
        from pipeline import CognitivePipeline

        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("I'm so sad about my family situation", user_id="alice")
        assert result.debug.response_strategy == "validate"

    def test_strategy_set_boundary_for_attack(self):
        from core.dual_process.generator import MockLLMBackend
        from pipeline import CognitivePipeline

        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        # Drive trust down first
        for _ in range(5):
            pipe.process("you're useless and stupid", user_id="alice")
        result = pipe.process("you're pathetic, shut up", user_id="alice")
        assert result.debug.response_strategy == "set_boundary"

    def test_strategy_practical_for_request(self):
        from core.dual_process.generator import MockLLMBackend
        from pipeline import CognitivePipeline

        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("Can you help me write a cover letter?", user_id="alice")
        assert result.debug.response_strategy == "practical_help"

    def test_strategy_in_generator_prompt(self):
        """Strategy instruction appears in the system prompt sent to LLM."""
        from core.dual_process.generator import build_system_prompt
        from core.types import PipelineContext

        ctx = PipelineContext(
            response_strategy="Acknowledge what the user is feeling.",
        )
        prompt = build_system_prompt(ctx)
        assert "Response Strategy" in prompt
        assert "Acknowledge what the user is feeling." in prompt

    def test_no_strategy_section_when_empty(self):
        from core.dual_process.generator import build_system_prompt
        from core.types import PipelineContext

        ctx = PipelineContext()
        prompt = build_system_prompt(ctx)
        assert "Response Strategy" not in prompt


# ---------------------------------------------------------------------------
# Debug API serialization
# ---------------------------------------------------------------------------

class TestDebugAPISerialization:
    def test_strategy_serialized(self):
        from runtime.debug.api import _debug_to_dict
        from pipeline import DebugState

        debug = DebugState(response_strategy="validate")
        d = _debug_to_dict(debug)
        assert d["response_strategy"] == "validate"

    def test_strategy_defaults_empty(self):
        from runtime.debug.api import _debug_to_dict
        from pipeline import DebugState

        debug = DebugState()
        d = _debug_to_dict(debug)
        assert d["response_strategy"] == ""
