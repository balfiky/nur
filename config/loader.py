"""Configuration loader for Project Nur.

Reads YAML files from the config/ directory and provides typed access.
All values have hardcoded defaults so modules work without config files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Config directory
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_DIR = Path(__file__).parent


def _load_yaml(filename: str, config_dir: Path | None = None) -> dict[str, Any]:
    """Load a YAML file from the config directory. Returns {} on failure."""
    base = config_dir or _DEFAULT_CONFIG_DIR
    path = base / filename
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_prompt(filename: str, config_dir: Path | None = None) -> str:
    """Load a markdown prompt template from config/prompts/."""
    base = config_dir or _DEFAULT_CONFIG_DIR
    path = base / "prompts" / filename
    if not path.exists():
        return ""
    return path.read_text()


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EnergyConfig:
    drain_per_message: float = 0.02
    drain_per_spike: float = 0.08
    recovery_rate_per_hour: float = 0.1


@dataclass
class ContagionEngineConfig:
    factor: float = 0.3
    cap: float = 0.15


@dataclass
class MemoryConfig:
    trust_positive_delta: float = 0.02
    trust_negative_delta: float = -0.15
    confidence_threshold: float = 0.6
    act_r_decay: float = 0.5
    emotional_bias_weight: float = 0.3
    spike_retrieval_bonus: float = 3.0
    topic_context_bonus: float = 2.0
    person_context_bonus: float = 1.5
    short_term_max_entries: int = 200


@dataclass
class ProfilingConfig:
    primacy_default: float = 0.8
    primacy_decay_rate: float = 0.02
    observation_window: int = 20


@dataclass
class PersonConfig:
    trust_positive_delta: float = 0.02
    trust_negative_delta: float = -0.15
    primacy_interaction_threshold: int = 5
    primacy_decay_per_interaction: float = 0.02
    primacy_floor: float = 0.3


@dataclass
class SelfModelConfig:
    entity_id: str = "__self__"
    strength_threshold: float = 0.7
    flaw_threshold: float = 0.6
    trigger_threshold: float = 0.7
    dissonance_window: int = 10
    trigger_min_observations: int = 3
    negative_traits: set[str] = field(default_factory=lambda: {
        "blunt", "impatient", "avoidant", "defensive", "dismissive",
        "over_cautious", "verbose", "cold", "impulsive", "rigid",
    })


@dataclass
class TopicConfig:
    charge_positive_delta: float = 0.03
    charge_negative_delta: float = 0.10
    avoidance_charge_threshold: float = 0.7
    conflict_avoidance_threshold: int = 3


@dataclass
class ContradictionConfig:
    threshold: float = 0.3
    recent_window: int = 10


@dataclass
class ContagionDetectionConfig:
    caps_ratio_threshold: float = 0.5
    exclamation_boost: float = 0.1
    question_arousal: float = 0.05
    ellipsis_valence: float = -0.05


@dataclass
class NurConfig:
    """Top-level configuration for all Project Nur modules."""

    # Modulators
    half_lives: dict[str, float] = field(default_factory=lambda: {
        "arousal": 120.0, "valence": 1800.0,
        "certainty": 600.0, "bonding": 86400.0,
    })
    baselines: dict[str, float] = field(default_factory=lambda: {
        "arousal": 0.5, "valence": 0.5, "certainty": 0.5,
        "bonding": 0.5, "energy": 1.0,
    })
    spike_threshold: float = 0.8
    attachment_style: str = "secure"

    # Sub-configs
    energy: EnergyConfig = field(default_factory=EnergyConfig)
    contagion_engine: ContagionEngineConfig = field(default_factory=ContagionEngineConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    profiling: ProfilingConfig = field(default_factory=ProfilingConfig)
    person: PersonConfig = field(default_factory=PersonConfig)
    self_model: SelfModelConfig = field(default_factory=SelfModelConfig)
    topic: TopicConfig = field(default_factory=TopicConfig)
    contradiction: ContradictionConfig = field(default_factory=ContradictionConfig)
    contagion_detection: ContagionDetectionConfig = field(default_factory=ContagionDetectionConfig)

    # Event impacts
    event_impacts: dict[str, dict[str, float]] = field(default_factory=dict)

    # Values
    values: dict[str, float] = field(default_factory=lambda: {
        "loyalty": 0.9, "honesty": 0.85, "kindness": 0.8,
        "justice": 0.7, "autonomy": 0.6,
    })

    # Prompt templates
    generator_prompt: str = ""
    self_check_prompt: str = ""
    digestion_prompt: str = ""
    contagion_prompt: str = ""
    classify_event_prompt: str = ""
    detect_topics_prompt: str = ""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(config_dir: str | Path | None = None) -> NurConfig:
    """Load configuration from YAML files.

    Falls back to hardcoded defaults for any missing values.
    """
    cdir = Path(config_dir) if config_dir is not None else None

    mod = _load_yaml("modulators.yaml", cdir)
    att = _load_yaml("attachment.yaml", cdir)
    prof = _load_yaml("profiles_schema.yaml", cdir)
    vals = _load_yaml("values_seed.yaml", cdir)

    cfg = NurConfig()

    # --- Modulators ---
    if "half_lives" in mod:
        cfg.half_lives = mod["half_lives"]
    if "baselines" in mod:
        cfg.baselines = mod["baselines"]
    if "spike_threshold" in mod:
        cfg.spike_threshold = mod["spike_threshold"]

    # Energy
    if "energy" in mod:
        e = mod["energy"]
        cfg.energy = EnergyConfig(
            drain_per_message=e.get("drain_per_message", 0.02),
            drain_per_spike=e.get("drain_per_spike", 0.08),
            recovery_rate_per_hour=e.get("recovery_rate_per_hour", 0.1),
        )

    # Contagion engine
    if "contagion" in mod:
        c = mod["contagion"]
        cfg.contagion_engine = ContagionEngineConfig(
            factor=c.get("factor", 0.3),
            cap=c.get("cap", 0.15),
        )

    # Event impacts
    if "event_impacts" in mod:
        cfg.event_impacts = mod["event_impacts"]

    # Memory
    if "memory" in mod:
        m = mod["memory"]
        cfg.memory = MemoryConfig(
            trust_positive_delta=m.get("trust_positive_delta", 0.02),
            trust_negative_delta=m.get("trust_negative_delta", -0.15),
            confidence_threshold=m.get("confidence_threshold", 0.6),
            act_r_decay=m.get("act_r_decay", 0.5),
            emotional_bias_weight=m.get("emotional_bias_weight", 0.3),
            spike_retrieval_bonus=m.get("spike_retrieval_bonus", 3.0),
            topic_context_bonus=m.get("topic_context_bonus", 2.0),
            person_context_bonus=m.get("person_context_bonus", 1.5),
            short_term_max_entries=m.get("short_term_max_entries", 200),
        )

    # --- Attachment ---
    if "active_style" in att:
        cfg.attachment_style = att["active_style"]

    # --- Profiles ---
    if "profiling" in prof:
        p = prof["profiling"]
        cfg.profiling = ProfilingConfig(
            primacy_default=p.get("primacy_default", 0.8),
            primacy_decay_rate=p.get("primacy_decay_rate", 0.02),
            observation_window=p.get("observation_window", 20),
        )

    if "person" in prof:
        p = prof["person"]
        cfg.person = PersonConfig(
            trust_positive_delta=p.get("trust_positive_delta", 0.02),
            trust_negative_delta=p.get("trust_negative_delta", -0.15),
            primacy_interaction_threshold=p.get("primacy_interaction_threshold", 5),
            primacy_decay_per_interaction=p.get("primacy_decay_per_interaction", 0.02),
            primacy_floor=p.get("primacy_floor", 0.3),
        )

    if "self_model" in prof:
        s = prof["self_model"]
        negative = s.get("negative_traits", [])
        cfg.self_model = SelfModelConfig(
            entity_id=s.get("entity_id", "__self__"),
            strength_threshold=s.get("strength_threshold", 0.7),
            flaw_threshold=s.get("flaw_threshold", 0.6),
            trigger_threshold=s.get("trigger_threshold", 0.7),
            dissonance_window=s.get("dissonance_window", 10),
            trigger_min_observations=s.get("trigger_min_observations", 3),
            negative_traits=set(negative) if negative else SelfModelConfig.negative_traits,
        )

    if "topic" in prof:
        t = prof["topic"]
        cfg.topic = TopicConfig(
            charge_positive_delta=t.get("charge_positive_delta", 0.03),
            charge_negative_delta=t.get("charge_negative_delta", 0.10),
            avoidance_charge_threshold=t.get("avoidance_charge_threshold", 0.7),
            conflict_avoidance_threshold=t.get("conflict_avoidance_threshold", 3),
        )

    if "contradiction" in prof:
        c = prof["contradiction"]
        cfg.contradiction = ContradictionConfig(
            threshold=c.get("threshold", 0.3),
            recent_window=c.get("recent_window", 10),
        )

    if "contagion" in prof:
        c = prof["contagion"]
        cfg.contagion_detection = ContagionDetectionConfig(
            caps_ratio_threshold=c.get("caps_ratio_threshold", 0.5),
            exclamation_boost=c.get("exclamation_boost", 0.1),
            question_arousal=c.get("question_arousal", 0.05),
            ellipsis_valence=c.get("ellipsis_valence", -0.05),
        )

    # --- Values ---
    if "values" in vals:
        cfg.values = vals["values"]

    # --- Prompts ---
    cfg.generator_prompt = _load_prompt("generator.md", cdir)
    cfg.self_check_prompt = _load_prompt("self_check.md", cdir)
    cfg.digestion_prompt = _load_prompt("digestion.md", cdir)
    cfg.contagion_prompt = _load_prompt("contagion.md", cdir)
    cfg.classify_event_prompt = _load_prompt("classify_event.md", cdir)
    cfg.detect_topics_prompt = _load_prompt("detect_topics.md", cdir)

    return cfg


# ---------------------------------------------------------------------------
# Module-level singleton (lazy loaded)
# ---------------------------------------------------------------------------

_config: NurConfig | None = None


def get_config() -> NurConfig:
    """Get the global config singleton. Loads from files on first call."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reset_config() -> None:
    """Reset the config singleton (for testing)."""
    global _config
    _config = None
