"""Shared type contracts for Project Nūr.

All modules import from here. These are the data structures that flow
between components — the shared language of the system.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Modulators
# ---------------------------------------------------------------------------

class ModulatorName(str, Enum):
    """The five v1 modulators (resolution deferred to v2)."""
    AROUSAL = "arousal"
    VALENCE = "valence"
    CERTAINTY = "certainty"
    BONDING = "bonding"
    ENERGY = "energy"


@dataclass
class ModulatorState:
    """Snapshot of all five modulators at a point in time."""
    arousal: float = 0.5
    valence: float = 0.5
    certainty: float = 0.5
    bonding: float = 0.5
    energy: float = 1.0

    def __post_init__(self) -> None:
        self._clamp_all()

    def _clamp_all(self) -> None:
        for name in ModulatorName:
            val = getattr(self, name.value)
            setattr(self, name.value, max(0.0, min(1.0, val)))

    def to_dict(self) -> dict[str, float]:
        return {m.value: getattr(self, m.value) for m in ModulatorName}

    def copy(self) -> ModulatorState:
        return ModulatorState(**self.to_dict())


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """Categories of events that can shift modulators."""
    USER_MESSAGE = "user_message"
    POSITIVE_FEEDBACK = "positive_feedback"
    NEGATIVE_FEEDBACK = "negative_feedback"
    CONFLICT = "conflict"
    RESOLUTION = "resolution"
    SURPRISE = "surprise"
    BETRAYAL = "betrayal"
    WARMTH = "warmth"
    SILENCE = "silence"
    TOPIC_SHIFT = "topic_shift"


@dataclass
class EmotionalEvent:
    """An event that can update the emotional state."""
    event_type: EventType
    intensity: float  # 0.0 - 1.0
    source: str = ""  # who/what caused it
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.intensity = max(0.0, min(1.0, self.intensity))


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

@dataclass
class ShortTermEntry:
    """A single entry in short-term emotional memory."""
    timestamp: float
    event: EmotionalEvent
    emotion_snapshot: ModulatorState


@dataclass
class LongTermEntry:
    """A distilled long-term memory record."""
    id: int | None = None
    timestamp: float = field(default_factory=time.time)
    summary: str = ""
    emotional_valence: float = 0.0  # -1.0 to 1.0
    trust_delta: float = 0.0
    topic: str = ""
    source_person: str = ""
    confidence: float = 0.0  # 0.0 - 1.0
    spike: bool = False  # did this bypass gradual accumulation?
    activation: float = 0.0  # ACT-R activation score (computed at retrieval)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

@dataclass
class BaselineShift:
    """How a person shifts the AI's modulator resting state."""
    arousal: float = 0.0
    valence: float = 0.0
    certainty: float = 0.0
    bonding: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "arousal": self.arousal,
            "valence": self.valence,
            "certainty": self.certainty,
            "bonding": self.bonding,
        }


@dataclass
class PersonProfile:
    """Mental model of a person the AI interacts with."""
    person_id: str
    name: str = ""
    trust: float = 0.5
    reliability: float = 0.5
    emotional_volatility: float = 0.5
    stress_response: str = "unknown"  # withdraws, lashes_out, seeks_support, shuts_down
    baseline_shift: BaselineShift = field(default_factory=BaselineShift)
    primacy_weight: float = 0.8  # how much first interactions still matter
    interaction_count: int = 0


@dataclass
class SelfProfile:
    """The AI's model of itself — same mechanism as person profiles."""
    observed_traits: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    flaws: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    dissonance: float = 0.0  # gap between self-model and recent behavior


@dataclass
class TopicProfile:
    """Emotional charge associated with a topic."""
    topic: str
    emotional_charge: float = 0.0  # 0.0 - 1.0
    avoidance: bool = False
    conflict_count: int = 0


def _default_values() -> dict[str, float]:
    from config.loader import get_config
    return dict(get_config().values)


@dataclass
class ValueHierarchy:
    """Ranked, weighted value system. Static in v1."""
    values: dict[str, float] = field(default_factory=_default_values)

    def ranked(self) -> list[tuple[str, float]]:
        return sorted(self.values.items(), key=lambda x: x[1], reverse=True)


# ---------------------------------------------------------------------------
# Contagion
# ---------------------------------------------------------------------------

@dataclass
class DetectedEmotion:
    """User's emotional state as detected from their message."""
    arousal: float = 0.5
    valence: float = 0.5
    certainty: float = 0.5
    intensity: float = 0.0

    def __post_init__(self) -> None:
        self.arousal = max(0.0, min(1.0, self.arousal))
        self.valence = max(0.0, min(1.0, self.valence))
        self.certainty = max(0.0, min(1.0, self.certainty))
        self.intensity = max(0.0, min(1.0, self.intensity))


# ---------------------------------------------------------------------------
# Attachment (config, locked to secure in v1)
# ---------------------------------------------------------------------------

class AttachmentStyle(str, Enum):
    SECURE = "secure"
    ANXIOUS = "anxious"
    AVOIDANT = "avoidant"
    DISORGANIZED = "disorganized"


# ---------------------------------------------------------------------------
# Pipeline context (what the LLM sees)
# ---------------------------------------------------------------------------

@dataclass
class PipelineContext:
    """Full context assembled for the response generation LLM call."""
    modulator_snapshot: dict[str, float] = field(default_factory=dict)
    person_profile: PersonProfile | None = None
    self_profile: SelfProfile | None = None
    topic_profiles: list[TopicProfile] = field(default_factory=list)
    values: ValueHierarchy = field(default_factory=ValueHierarchy)
    retrieved_memories: list[LongTermEntry] = field(default_factory=list)
    short_term_history: list[ShortTermEntry] = field(default_factory=list)
    contradiction_flags: list[str] = field(default_factory=list)
    contagion: DetectedEmotion | None = None
