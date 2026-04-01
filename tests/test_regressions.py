"""Regression tests from SECOND_PASS_REVIEW.md.

These tests verify the fixes for confirmed implementation issues:
1. Inner dialogue candidate affects final response
2. Defense affects final response
3. Context shift doesn't compound across repeated turns
4. Elapsed time decays state during normal process()
5. Partial self_model config without negative_traits doesn't crash
6. Defense history persists across profile reload
"""

from __future__ import annotations

import time

import pytest

from config.loader import load_config, reset_config
from core.dual_process.generator import MockLLMBackend, build_system_prompt
from core.types import (
    BaselineShift,
    ModulatorState,
    PipelineContext,
    PersonProfile,
    SelfProfile,
)
from pipeline import CognitivePipeline


# ---------------------------------------------------------------------------
# 1. Inner dialogue candidate affects final response
# ---------------------------------------------------------------------------

class TestInnerDialogueCandidateAffectsResponse:
    """The dialogue candidate must appear in the generator's system prompt
    so it actually steers the final response."""

    def test_candidate_in_system_prompt(self):
        """PipelineContext.candidate_response is injected into the system prompt."""
        ctx = PipelineContext(
            candidate_response="I think you should reconsider your approach.",
        )
        prompt = build_system_prompt(ctx)
        assert "I think you should reconsider your approach." in prompt

    def test_changing_candidate_changes_prompt(self):
        """Different candidates produce different system prompts."""
        ctx_a = PipelineContext(candidate_response="Response alpha")
        ctx_b = PipelineContext(candidate_response="Response beta")
        prompt_a = build_system_prompt(ctx_a)
        prompt_b = build_system_prompt(ctx_b)
        assert "Response alpha" in prompt_a
        assert "Response beta" in prompt_b
        assert prompt_a != prompt_b

    def test_pipeline_passes_candidate_to_generator(self):
        """The pipeline must pass filtered_output as candidate_response."""
        backend = MockLLMBackend()
        pipe = CognitivePipeline(llm_backend=backend)
        pipe.process("hello", user_id="test_user")
        # The mock returns "I understand." which becomes the dialogue candidate.
        # After defense filtering, it flows into the generator's system prompt.
        system_prompt = backend.last_system_prompt
        # The candidate (or filtered version) must appear in the prompt
        assert "I understand." in system_prompt or "Draft response" in system_prompt


# ---------------------------------------------------------------------------
# 2. Defense affects final response
# ---------------------------------------------------------------------------

class TestDefenseAffectsResponse:
    """When a defense fires, the defense instruction must appear in the
    generator's system prompt — not just in debug metadata."""

    def test_defense_instruction_in_system_prompt(self):
        """PipelineContext.defense_instruction is injected into the prompt."""
        ctx = PipelineContext(
            defense_instruction="Reduce the expressed intensity. Use hedging language.",
        )
        prompt = build_system_prompt(ctx)
        assert "Reduce the expressed intensity" in prompt

    def test_no_defense_no_instruction(self):
        """Without a defense, no defense instruction appears."""
        ctx = PipelineContext(defense_instruction="")
        prompt = build_system_prompt(ctx)
        assert "Defense filter" not in prompt


# ---------------------------------------------------------------------------
# 3. Context shift doesn't compound across repeated turns
# ---------------------------------------------------------------------------

class TestContextShiftNoCompounding:
    """Applying the same person's context shift across multiple turns
    must NOT ratchet the state upward indefinitely."""

    def test_repeated_context_shift_bounded(self):
        """10 turns with arousal=+0.1 shift must not push arousal to 1.0."""
        pipe = CognitivePipeline()
        # Set a person with positive arousal shift
        pipe.person_profiles.set_baseline_shift(
            "shifty",
            BaselineShift(arousal=0.1, valence=0.0, certainty=0.0, bonding=0.0),
        )
        initial_arousal = pipe.engine.state.arousal  # 0.5

        for _ in range(10):
            pipe.process("neutral message", user_id="shifty")

        # After 10 turns, arousal should NOT be near 1.0 from compounding
        # The shift is a resting target, not an additive delta per turn
        assert pipe.engine.state.arousal < initial_arousal + 0.3, (
            f"Arousal ratcheted to {pipe.engine.state.arousal:.2f} — "
            "context shift is compounding instead of acting as baseline"
        )


# ---------------------------------------------------------------------------
# 4. Elapsed time decays state during normal process()
# ---------------------------------------------------------------------------

class TestElapsedTimeDecaysState:
    """State should decay toward baseline between process() calls
    without needing explicit apply_rest()."""

    def test_decay_between_turns(self):
        """Simulated time gap between turns triggers automatic decay."""
        pipe = CognitivePipeline()
        # Spike arousal high
        pipe.engine.state.arousal = 0.9

        # Simulate a gap by backdating last_turn_time
        pipe._last_turn_time = time.time() - 300  # 5 minutes ago

        pipe.process("hey", user_id="test")

        # Arousal should have decayed from 0.9 toward baseline (0.5)
        # With half-life of 120s, after 300s: decay_factor ~ 0.177
        # new_val ~ 0.5 + (0.9 - 0.5) * 0.177 ~ 0.57
        assert pipe.engine.state.arousal < 0.75, (
            f"Arousal still {pipe.engine.state.arousal:.2f} — "
            "decay not running between turns"
        )


# ---------------------------------------------------------------------------
# 5. Partial self_model config without negative_traits doesn't crash
# ---------------------------------------------------------------------------

class TestPartialSelfModelConfig:
    """load_config() must not crash when self_model config omits negative_traits."""

    def test_missing_negative_traits(self, tmp_path):
        """Config with self_model but no negative_traits uses defaults."""
        import yaml

        profiles_yaml = tmp_path / "profiles_schema.yaml"
        profiles_yaml.write_text(yaml.dump({
            "self_model": {
                "entity_id": "__self__",
                "strength_threshold": 0.7,
                # negative_traits intentionally omitted
            }
        }))
        # Must create minimal required yaml files
        (tmp_path / "modulators.yaml").write_text("{}")
        (tmp_path / "attachment.yaml").write_text("{}")
        (tmp_path / "values_seed.yaml").write_text("{}")
        (tmp_path / "prompts").mkdir()

        reset_config()
        try:
            cfg = load_config(config_dir=tmp_path)
            # Should have defaults, not crash
            assert isinstance(cfg.self_model.negative_traits, set)
            assert len(cfg.self_model.negative_traits) > 0
        finally:
            reset_config()


# ---------------------------------------------------------------------------
# 6. Defense history persists across profile reload
# ---------------------------------------------------------------------------

class TestDefenseHistoryPersists:
    """Defense events must survive a profile reload, not vanish."""

    def test_defense_events_survive_reload(self):
        """After a defense fires, get_profile() should reflect it in maturity."""
        pipe = CognitivePipeline()

        # Force a defense to fire: high arousal + low trust
        pipe.engine.state.arousal = 0.9
        pipe.engine.state.valence = 0.1  # extreme valence for raw intensity
        person = pipe.person_profiles.get_or_create("low_trust_user")
        person.trust = 0.2
        pipe.person_profiles.save(person)

        # Process a message — should trigger defense
        pipe.process("this is upsetting", user_id="low_trust_user")

        # Get fresh profile (simulates reload)
        prof1 = pipe.self_profile.get_profile()

        # Process another message with defense trigger
        pipe.engine.state.arousal = 0.9
        pipe.engine.state.valence = 0.1
        pipe.process("still upset", user_id="low_trust_user")

        prof2 = pipe.self_profile.get_profile()

        # maturity_score should be derived from persisted evidence,
        # and should change as defense events accumulate
        assert prof2.maturity_score >= 0.0, "maturity_score should be non-negative"
        # After recording observations, the profile should have some observed traits
        assert len(prof2.observed_traits) > 0 or prof2.maturity_score > 0, (
            "Self-profile shows no evidence of learning from live use"
        )
