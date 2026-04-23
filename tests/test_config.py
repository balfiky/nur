"""Tests for the config system — Phase 8."""

import pytest
import tempfile
import os
from pathlib import Path

import yaml

from config.loader import (
    NurConfig,
    load_config,
    get_config,
    reset_config,
    SoulConfig,
    SemanticMemoryConfig,
    EnergyConfig,
    MemoryConfig,
    ProfilingConfig,
    PersonConfig,
    SelfModelConfig,
    TopicConfig,
    ContradictionConfig,
    ContagionDetectionConfig,
    ContagionEngineConfig,
)


class TestNurConfigDefaults:
    """Config defaults should match the hardcoded values from all modules."""

    def test_default_half_lives(self):
        cfg = NurConfig()
        assert cfg.half_lives["arousal"] == 120.0
        assert cfg.half_lives["valence"] == 1800.0
        assert cfg.half_lives["certainty"] == 600.0
        assert cfg.half_lives["bonding"] == 86400.0

    def test_default_baselines(self):
        cfg = NurConfig()
        assert cfg.baselines["arousal"] == 0.5
        assert cfg.baselines["energy"] == 1.0

    def test_default_spike_threshold(self):
        cfg = NurConfig()
        assert cfg.spike_threshold == 0.8

    def test_default_energy(self):
        cfg = NurConfig()
        assert cfg.energy.drain_per_message == 0.02
        assert cfg.energy.drain_per_spike == 0.08
        assert cfg.energy.recovery_rate_per_hour == 0.1

    def test_default_memory(self):
        cfg = NurConfig()
        assert cfg.memory.trust_positive_delta == 0.02
        assert cfg.memory.trust_negative_delta == -0.15
        assert cfg.memory.confidence_threshold == 0.6
        assert cfg.memory.act_r_decay == 0.5
        assert cfg.memory.spike_retrieval_bonus == 3.0

    def test_default_contagion_engine(self):
        cfg = NurConfig()
        assert cfg.contagion_engine.factor == 0.3
        assert cfg.contagion_engine.cap == 0.15

    def test_default_profiling(self):
        cfg = NurConfig()
        assert cfg.profiling.primacy_default == 0.8
        assert cfg.profiling.observation_window == 20

    def test_default_person(self):
        cfg = NurConfig()
        assert cfg.person.trust_positive_delta == 0.02
        assert cfg.person.trust_negative_delta == -0.15
        assert cfg.person.primacy_interaction_threshold == 5

    def test_default_self_model(self):
        cfg = NurConfig()
        assert cfg.self_model.entity_id == "__self__"
        assert cfg.self_model.strength_threshold == 0.7
        assert cfg.self_model.flaw_threshold == 0.6
        assert "blunt" in cfg.self_model.negative_traits

    def test_default_topic(self):
        cfg = NurConfig()
        assert cfg.topic.charge_positive_delta == 0.03
        assert cfg.topic.charge_negative_delta == 0.10
        assert cfg.topic.avoidance_charge_threshold == 0.7

    def test_default_contradiction(self):
        cfg = NurConfig()
        assert cfg.contradiction.threshold == 0.3
        assert cfg.contradiction.recent_window == 10

    def test_default_values(self):
        cfg = NurConfig()
        assert cfg.values["loyalty"] == 0.9
        assert cfg.values["honesty"] == 0.85

    def test_default_soul(self):
        cfg = NurConfig()
        assert isinstance(cfg.soul, SoulConfig)
        assert cfg.soul.name == "Nūr"
        assert "clarity" in cfg.soul.likes
        assert cfg.soul.initial_traits["calm"] == 0.8

    def test_default_semantic_memory(self):
        cfg = NurConfig()
        assert isinstance(cfg.semantic_memory, SemanticMemoryConfig)
        assert cfg.semantic_memory.enabled is True
        assert cfg.semantic_memory.backend == "sqlite"


class TestLoadConfig:
    """Test loading config from YAML files."""

    def test_loads_from_project_config(self):
        cfg = load_config()
        # Should load the actual YAML files from config/
        assert cfg.half_lives["arousal"] == 120.0
        assert cfg.spike_threshold == 0.8

    def test_loads_event_impacts(self):
        cfg = load_config()
        assert "betrayal" in cfg.event_impacts
        assert cfg.event_impacts["betrayal"]["valence"] == -0.50

    def test_loads_memory_config(self):
        cfg = load_config()
        assert cfg.memory.trust_positive_delta == 0.02
        assert cfg.memory.spike_retrieval_bonus == 3.0

    def test_loads_profile_config(self):
        cfg = load_config()
        assert cfg.person.primacy_interaction_threshold == 5
        assert cfg.self_model.strength_threshold == 0.7
        assert cfg.topic.charge_negative_delta == 0.10

    def test_loads_values(self):
        cfg = load_config()
        assert cfg.values["loyalty"] == 0.9

    def test_loads_soul(self):
        cfg = load_config()
        assert cfg.soul.name == "Nūr"
        assert "steady collaboration" in cfg.soul.likes
        assert cfg.soul.initial_traits["thoughtful"] == 0.78

    def test_loads_semantic_memory(self):
        cfg = load_config()
        assert cfg.semantic_memory.enabled is True
        assert cfg.semantic_memory.backend == "sqlite"
        assert cfg.semantic_memory.retrieval_limit == 5

    def test_loads_prompts(self):
        cfg = load_config()
        assert "Nūr" in cfg.generator_prompt
        assert "Tone Fit" in cfg.self_check_prompt
        assert "Digestion" in cfg.digestion_prompt

    def test_loads_attachment(self):
        cfg = load_config()
        assert cfg.attachment_style == "secure"


class TestLoadConfigCustomDir:
    """Test loading from a custom config directory."""

    def test_custom_config_dir(self, tmp_path):
        # Create a minimal modulators.yaml in tmp dir
        mod_config = {
            "half_lives": {"arousal": 60.0, "valence": 900.0, "certainty": 300.0, "bonding": 43200.0},
            "spike_threshold": 0.9,
        }
        (tmp_path / "modulators.yaml").write_text(yaml.dump(mod_config))

        cfg = load_config(config_dir=tmp_path)
        assert cfg.half_lives["arousal"] == 60.0
        assert cfg.spike_threshold == 0.9
        # Missing files should fall back to defaults
        assert cfg.person.trust_positive_delta == 0.02

        # Restore default config dir
        reset_config()

    def test_missing_config_dir_uses_defaults(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        cfg = load_config(config_dir=empty_dir)
        # All defaults should apply
        assert cfg.half_lives["arousal"] == 120.0
        assert cfg.spike_threshold == 0.8
        reset_config()


class TestGetConfigSingleton:
    """Test the singleton pattern."""

    def test_returns_same_instance(self):
        reset_config()
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_reset_forces_reload(self):
        cfg1 = get_config()
        reset_config()
        cfg2 = get_config()
        assert cfg1 is not cfg2


class TestModulesUseConfig:
    """Verify that modules are actually loading from config."""

    def test_emotional_engine_uses_config(self):
        from core.emotional_engine import (
            DEFAULT_HALF_LIVES, SPIKE_INTENSITY_THRESHOLD,
            ENERGY_DRAIN_PER_MESSAGE, ENERGY_DRAIN_PER_SPIKE,
            ENERGY_RECOVERY_RATE, EVENT_IMPACTS,
        )
        cfg = get_config()
        assert DEFAULT_HALF_LIVES == cfg.half_lives
        assert SPIKE_INTENSITY_THRESHOLD == cfg.spike_threshold
        assert ENERGY_DRAIN_PER_MESSAGE == cfg.energy.drain_per_message
        assert ENERGY_DRAIN_PER_SPIKE == cfg.energy.drain_per_spike
        assert ENERGY_RECOVERY_RATE == cfg.energy.recovery_rate_per_hour

    def test_long_term_uses_config(self):
        from core.memory.long_term import (
            POSITIVE_DELTA, NEGATIVE_DELTA, CONFIDENCE_THRESHOLD,
            ACT_R_DECAY, SPIKE_RETRIEVAL_BONUS,
        )
        cfg = get_config()
        assert POSITIVE_DELTA == cfg.memory.trust_positive_delta
        assert NEGATIVE_DELTA == cfg.memory.trust_negative_delta
        assert CONFIDENCE_THRESHOLD == cfg.memory.confidence_threshold
        assert ACT_R_DECAY == cfg.memory.act_r_decay
        assert SPIKE_RETRIEVAL_BONUS == cfg.memory.spike_retrieval_bonus

    def test_profiles_use_config(self):
        from core.profiles.base import PRIMACY_DEFAULT, OBSERVATION_WINDOW
        from core.profiles.person import TRUST_POSITIVE_DELTA, TRUST_NEGATIVE_DELTA
        from core.profiles.self_model import SELF_ENTITY_ID, STRENGTH_THRESHOLD
        from core.profiles.topic import AVOIDANCE_CHARGE_THRESHOLD, CHARGE_NEGATIVE_DELTA
        from core.profiles.contradiction import CONTRADICTION_THRESHOLD

        cfg = get_config()
        assert PRIMACY_DEFAULT == cfg.profiling.primacy_default
        assert OBSERVATION_WINDOW == cfg.profiling.observation_window
        assert TRUST_POSITIVE_DELTA == cfg.person.trust_positive_delta
        assert SELF_ENTITY_ID == cfg.self_model.entity_id
        assert STRENGTH_THRESHOLD == cfg.self_model.strength_threshold
        assert AVOIDANCE_CHARGE_THRESHOLD == cfg.topic.avoidance_charge_threshold
        assert CHARGE_NEGATIVE_DELTA == cfg.topic.charge_negative_delta
        assert CONTRADICTION_THRESHOLD == cfg.contradiction.threshold

    def test_contagion_uses_config(self):
        from core.contagion import (
            _CAPS_RATIO_THRESHOLD, _EXCLAMATION_BOOST,
            _QUESTION_AROUSAL, _ELLIPSIS_VALENCE,
        )
        cfg = get_config()
        assert _CAPS_RATIO_THRESHOLD == cfg.contagion_detection.caps_ratio_threshold
        assert _EXCLAMATION_BOOST == cfg.contagion_detection.exclamation_boost

    def test_digestion_uses_config(self):
        from core.memory.digestion import SPIKE_INTENSITY_THRESHOLD
        cfg = get_config()
        assert SPIKE_INTENSITY_THRESHOLD == cfg.spike_threshold

    def test_values_from_config(self):
        from core.types import ValueHierarchy
        vh = ValueHierarchy()
        cfg = get_config()
        assert vh.values == cfg.values


class TestYAMLFiles:
    """Verify that the YAML files parse correctly and contain expected keys."""

    def test_modulators_yaml_structure(self):
        cfg = load_config()
        assert "arousal" in cfg.half_lives
        assert len(cfg.event_impacts) == 10  # all 10 event types

    def test_all_event_types_in_config(self):
        from core.types import EventType
        cfg = load_config()
        for et in EventType:
            assert et.value in cfg.event_impacts, f"Missing event type: {et.value}"

    def test_profiles_schema_completeness(self):
        cfg = load_config()
        # All negative traits present
        assert len(cfg.self_model.negative_traits) == 10
        assert "blunt" in cfg.self_model.negative_traits
        assert "rigid" in cfg.self_model.negative_traits

    def test_values_seed_completeness(self):
        cfg = load_config()
        assert len(cfg.values) == 5
        assert "loyalty" in cfg.values
        assert "autonomy" in cfg.values

    def test_soul_yaml_completeness(self):
        cfg = load_config()
        assert len(cfg.soul.boundaries) == 3
        assert "manipulation" in cfg.soul.dislikes

    def test_semantic_memory_yaml_completeness(self):
        cfg = load_config()
        assert cfg.semantic_memory.write_raw_turns is True
        assert cfg.semantic_memory.write_preferences is True


class TestPromptTemplates:
    """Verify prompt template files exist and have content."""

    def test_generator_prompt_exists(self):
        cfg = load_config()
        assert len(cfg.generator_prompt) > 50
        assert "Nūr" in cfg.generator_prompt

    def test_self_check_prompt_exists(self):
        cfg = load_config()
        assert len(cfg.self_check_prompt) > 50
        assert "Tone Fit" in cfg.self_check_prompt

    def test_digestion_prompt_exists(self):
        cfg = load_config()
        assert len(cfg.digestion_prompt) > 50
        assert "Digestion" in cfg.digestion_prompt
