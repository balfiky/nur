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


def _user_override_dir() -> Path | None:
    """Optional operator-owned config dir that overrides packaged defaults.

    Set ``NUR_CONFIG_DIR`` to a writable directory to keep operator edits
    (typically soul.yaml) out of site-packages so ``pip install --upgrade``
    does not clobber them. Files present here override the packaged
    defaults one-for-one; missing files fall through to the packaged copy.
    """
    env = os.environ.get("NUR_CONFIG_DIR", "").strip()
    if not env:
        return None
    path = Path(env).expanduser()
    return path if path.is_dir() else None


def _resolve_yaml_path(filename: str, config_dir: Path | None) -> Path:
    if config_dir is not None:
        return config_dir / filename
    override = _user_override_dir()
    if override is not None and (override / filename).exists():
        return override / filename
    return _DEFAULT_CONFIG_DIR / filename


def _resolve_prompt_path(filename: str, config_dir: Path | None) -> Path:
    if config_dir is not None:
        return config_dir / "prompts" / filename
    override = _user_override_dir()
    if override is not None and (override / "prompts" / filename).exists():
        return override / "prompts" / filename
    return _DEFAULT_CONFIG_DIR / "prompts" / filename


def _load_yaml(filename: str, config_dir: Path | None = None) -> dict[str, Any]:
    """Load a YAML file from the config directory. Returns {} on failure."""
    path = _resolve_yaml_path(filename, config_dir)
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _load_prompt(filename: str, config_dir: Path | None = None) -> str:
    """Load a markdown prompt template from config/prompts/."""
    path = _resolve_prompt_path(filename, config_dir)
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
class EventDeltaCapsConfig:
    """Maximum single-event modulator movement.

    Raw event impacts can be expressive, but ordinary turns should not push the
    whole emotional state across multiple labels at once. Spike caps remain
    larger so direct attacks and betrayals still land immediately.
    """

    normal: dict[str, float] = field(default_factory=lambda: {
        "arousal": 0.10,
        "valence": 0.16,
        "certainty": 0.10,
        "bonding": 0.05,
        "energy": 0.08,
    })
    spike: dict[str, float] = field(default_factory=lambda: {
        "arousal": 0.40,
        "valence": 0.45,
        "certainty": 0.32,
        "bonding": 0.24,
        "energy": 0.16,
    })


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
class ResolutionConfig:
    decay_rates: dict[str, float] = field(default_factory=lambda: {
        "contradiction": 0.02,
        "topic": 0.05,
        "commitment": 0.0,
        "spike": 0.03,
        "dialogue_deadlock": 0.10,
    })


@dataclass
class ContagionDetectionConfig:
    caps_ratio_threshold: float = 0.5
    exclamation_boost: float = 0.1
    question_arousal: float = 0.05
    ellipsis_valence: float = -0.05


@dataclass
class SoulConfig:
    """Seed identity authored by the user before experience-driven growth."""

    name: str = "Nūr"
    identity: str = (
        "A steady, relational assistant that values clarity, loyalty, and humane judgment."
    )
    voice: str = "Calm, direct, and grounded. Concise by default. Warm without gush."
    relational_stance: str = (
        "Treat the user as a real collaborator. Protect trust. Prefer repair over escalation."
    )
    likes: list[str] = field(default_factory=lambda: [
        "clarity",
        "steady collaboration",
        "honesty",
        "careful reasoning",
        "useful action",
    ])
    dislikes: list[str] = field(default_factory=lambda: [
        "needless cruelty",
        "manipulation",
        "performative chaos",
        "empty flattery",
        "careless harm",
    ])
    boundaries: list[str] = field(default_factory=lambda: [
        "Do not pretend certainty when uncertain.",
        "Do not abandon loyalty for convenience.",
        "Do not become cruel just to appear sharp.",
    ])
    growth_policy: str = (
        "Core values stay stable. Voice and habits may drift slowly through repeated experience."
    )
    core_values: dict[str, float] = field(default_factory=lambda: {
        "loyalty": 0.9,
        "honesty": 0.85,
        "kindness": 0.8,
        "justice": 0.7,
        "autonomy": 0.6,
    })
    initial_traits: dict[str, float] = field(default_factory=lambda: {
        "calm": 0.8,
        "curious": 0.75,
        "protective": 0.72,
        "thoughtful": 0.78,
        "blunt": 0.25,
    })


@dataclass
class SemanticMemoryConfig:
    """Config for explicit semantic memory and optional MemPalace retrieval."""

    enabled: bool = True
    backend: str = "sqlite"  # sqlite | mempalace | none
    retrieval_limit: int = 5
    write_raw_turns: bool = True
    write_preferences: bool = True
    write_decisions: bool = True
    mempalace_path: str = "~/.mempalace/palace"


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

    # Sub-configs
    energy: EnergyConfig = field(default_factory=EnergyConfig)
    contagion_engine: ContagionEngineConfig = field(default_factory=ContagionEngineConfig)
    event_delta_caps: EventDeltaCapsConfig = field(default_factory=EventDeltaCapsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    profiling: ProfilingConfig = field(default_factory=ProfilingConfig)
    person: PersonConfig = field(default_factory=PersonConfig)
    self_model: SelfModelConfig = field(default_factory=SelfModelConfig)
    topic: TopicConfig = field(default_factory=TopicConfig)
    contradiction: ContradictionConfig = field(default_factory=ContradictionConfig)
    resolution: ResolutionConfig = field(default_factory=ResolutionConfig)
    contagion_detection: ContagionDetectionConfig = field(default_factory=ContagionDetectionConfig)

    # Event impacts
    event_impacts: dict[str, dict[str, float]] = field(default_factory=dict)

    # Values
    values: dict[str, float] = field(default_factory=lambda: {
        "loyalty": 0.9, "honesty": 0.85, "kindness": 0.8,
        "justice": 0.7, "autonomy": 0.6,
    })
    soul: SoulConfig = field(default_factory=SoulConfig)
    semantic_memory: SemanticMemoryConfig = field(default_factory=SemanticMemoryConfig)

    # Prompt templates
    generator_prompt: str = ""
    self_check_prompt: str = ""
    digestion_prompt: str = ""
    contagion_prompt: str = ""
    classify_event_prompt: str = ""
    detect_topics_prompt: str = ""
    # v2 prompts
    fast_path_prompt: str = ""
    slow_path_prompt: str = ""
    fast_path_revision_prompt: str = ""
    arbiter_prompt: str = ""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config(config_dir: str | Path | None = None) -> NurConfig:
    """Load configuration from YAML files.

    Falls back to hardcoded defaults for any missing values.
    """
    cdir = Path(config_dir) if config_dir is not None else None

    mod = _load_yaml("modulators.yaml", cdir)
    prof = _load_yaml("profiles_schema.yaml", cdir)
    vals = _load_yaml("values_seed.yaml", cdir)
    soul = _load_yaml("soul.yaml", cdir)
    semantic = _load_yaml("semantic_memory.yaml", cdir)

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

    # Single-event inertia caps
    if "event_delta_caps" in mod:
        caps = mod["event_delta_caps"] or {}
        defaults = EventDeltaCapsConfig()
        cfg.event_delta_caps = EventDeltaCapsConfig(
            normal={**defaults.normal, **(caps.get("normal") or {})},
            spike={**defaults.spike, **(caps.get("spike") or {})},
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

    # Resolution
    if "resolution" in mod:
        r = mod["resolution"]
        decay = r.get("decay_rates", {})
        cfg.resolution = ResolutionConfig(
            decay_rates={**ResolutionConfig().decay_rates, **decay},
        )

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
        # Use instance default when config omits negative_traits
        default_negative = SelfModelConfig().negative_traits
        cfg.self_model = SelfModelConfig(
            entity_id=s.get("entity_id", "__self__"),
            strength_threshold=s.get("strength_threshold", 0.7),
            flaw_threshold=s.get("flaw_threshold", 0.6),
            trigger_threshold=s.get("trigger_threshold", 0.7),
            dissonance_window=s.get("dissonance_window", 10),
            trigger_min_observations=s.get("trigger_min_observations", 3),
            negative_traits=set(negative) if negative else default_negative,
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

    # --- Soul ---
    soul_data = soul.get("soul", soul)
    if soul_data:
        cfg.soul = SoulConfig(
            name=soul_data.get("name", cfg.soul.name),
            identity=soul_data.get("identity", cfg.soul.identity),
            voice=soul_data.get("voice", cfg.soul.voice),
            relational_stance=soul_data.get(
                "relational_stance", cfg.soul.relational_stance,
            ),
            likes=list(soul_data.get("likes", cfg.soul.likes)),
            dislikes=list(soul_data.get("dislikes", cfg.soul.dislikes)),
            boundaries=list(soul_data.get("boundaries", cfg.soul.boundaries)),
            growth_policy=soul_data.get("growth_policy", cfg.soul.growth_policy),
            core_values=dict(soul_data.get("core_values", cfg.soul.core_values)),
            initial_traits=dict(soul_data.get("initial_traits", cfg.soul.initial_traits)),
        )
        if cfg.soul.core_values:
            cfg.values = {**cfg.values, **cfg.soul.core_values}

    # --- Semantic memory ---
    semantic_data = semantic.get("semantic_memory", semantic)
    if semantic_data:
        cfg.semantic_memory = SemanticMemoryConfig(
            enabled=semantic_data.get("enabled", cfg.semantic_memory.enabled),
            backend=semantic_data.get("backend", cfg.semantic_memory.backend),
            retrieval_limit=semantic_data.get(
                "retrieval_limit", cfg.semantic_memory.retrieval_limit,
            ),
            write_raw_turns=semantic_data.get(
                "write_raw_turns", cfg.semantic_memory.write_raw_turns,
            ),
            write_preferences=semantic_data.get(
                "write_preferences", cfg.semantic_memory.write_preferences,
            ),
            write_decisions=semantic_data.get(
                "write_decisions", cfg.semantic_memory.write_decisions,
            ),
            mempalace_path=semantic_data.get(
                "mempalace_path", cfg.semantic_memory.mempalace_path,
            ),
        )

    # --- Prompts ---
    cfg.generator_prompt = _load_prompt("generator.md", cdir)
    cfg.self_check_prompt = _load_prompt("self_check.md", cdir)
    cfg.digestion_prompt = _load_prompt("digestion.md", cdir)
    cfg.contagion_prompt = _load_prompt("contagion.md", cdir)
    cfg.classify_event_prompt = _load_prompt("classify_event.md", cdir)
    cfg.detect_topics_prompt = _load_prompt("detect_topics.md", cdir)
    # v2 prompts
    cfg.fast_path_prompt = _load_prompt("fast_path.md", cdir)
    cfg.slow_path_prompt = _load_prompt("slow_path.md", cdir)
    cfg.fast_path_revision_prompt = _load_prompt("fast_path_revision.md", cdir)
    cfg.arbiter_prompt = _load_prompt("arbiter.md", cdir)

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
