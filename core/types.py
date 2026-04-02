"""Shared type contracts for Project Nūr.

All modules import from here. These are the data structures that flow
between components — the shared language of the system.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional


# ---------------------------------------------------------------------------
# Modulators
# ---------------------------------------------------------------------------

class ModulatorName(str, Enum):
    """The six modulators (resolution added in v2)."""
    AROUSAL = "arousal"
    VALENCE = "valence"
    CERTAINTY = "certainty"
    BONDING = "bonding"
    ENERGY = "energy"
    RESOLUTION = "resolution"


@dataclass
class ModulatorState:
    """Snapshot of all six modulators at a point in time."""
    arousal: float = 0.5
    valence: float = 0.5
    certainty: float = 0.5
    bonding: float = 0.5
    energy: float = 1.0
    resolution: float = 0.0

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
class DefenseEvent:
    """Record of a single defense activation, stored in self-profile."""
    timestamp: float
    defense_type: str  # "rationalization" | "deflection" | "minimization" | "projection"
    raw_intensity: float
    expressed_intensity: float
    suppression_delta: float


@dataclass
class SelfProfile:
    """The AI's model of itself — same mechanism as person profiles."""
    observed_traits: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    flaws: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    dissonance: float = 0.0  # gap between self-model and recent behavior
    maturity_score: float = 0.0  # 0.0-1.0, grows with self-awareness (v2)
    defense_log: list[DefenseEvent] = field(default_factory=list)


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
    # v2: inner dialogue candidate and defense instruction
    candidate_response: str = ""
    defense_instruction: str = ""
    # Agentic tools: summarized tool execution context for generator
    tool_context_summary: str = ""


# ---------------------------------------------------------------------------
# v2 Types — Resolution, Anticipation, Inner Dialogue, Defense
# ---------------------------------------------------------------------------

@dataclass
class UnresolvedItem:
    """An unresolved cognitive/emotional tension tracked by the resolution modulator."""
    id: str
    source: str  # "contradiction" | "topic" | "commitment" | "spike" | "dialogue_deadlock"
    description: str
    created_at: datetime
    intensity: float  # 0.0-1.0
    decay_rate: float  # per-hour decay
    resolved: bool = False
    resolved_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        self.intensity = max(0.0, min(1.0, self.intensity))
        self.decay_rate = max(0.0, self.decay_rate)


@dataclass
class Anticipation:
    """Forward emotional prediction — what the system expects before processing."""
    predicted_topics: list[str]
    predicted_emotional_tone: str
    modulator_pre_shifts: dict[str, float]
    confidence: float  # 0-1
    basis: str  # why this prediction


@dataclass
class DialogueRound:
    """One round of fast/slow path negotiation."""
    round_number: int
    fast_path_candidate: str
    slow_path_evaluation: str
    slow_path_approved: bool
    objection_reason: Optional[str] = None
    revision_notes: Optional[str] = None


@dataclass
class InnerDialogueTrace:
    """Full trace of the inner dialogue deliberation."""
    rounds: list[DialogueRound]
    final_candidate: str
    total_llm_calls: int
    reached_deadlock: bool
    deadlock_resolution: Optional[str] = None  # "arbiter" | None
    dominant_path: str = "fast"  # "fast" | "slow" | "arbiter"
    tension_level: float = 0.0  # 0-1


@dataclass
class DefenseActivation:
    """Record of a defense mechanism activation."""
    defense_type: str  # "rationalization" | "deflection" | "minimization" | "projection"
    raw_intensity: float
    expressed_intensity: float
    suppression_delta: float  # the gap — how much is being hidden
    reason: str


# ---------------------------------------------------------------------------
# Agentic Tools — tool data model (Phase 0)
# ---------------------------------------------------------------------------

class ToolCategory(str, Enum):
    """Category that every registered tool must declare."""
    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL_ACTION = "external_action"
    COGNITIVE = "cognitive"


@dataclass
class ToolCapability:
    """A registered tool's static description."""
    name: str
    description: str
    category: ToolCategory
    arg_schema: dict[str, Any] = field(default_factory=dict)
    supports_streaming: bool = False
    requires_network: bool = False
    mcp_backed: bool = False


@dataclass
class ToolIntent:
    """What Jarvis wants to do — the key cognitive action object."""
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    expected_outcome: str
    urgency: float = 0.5               # 0.0-1.0
    risk_tolerance: float = 0.5        # 0.0-1.0
    autonomy_bias: float = 0.5         # 0.0-1.0 (act now vs ask first)
    clarification_threshold: float = 0.5  # 0.0-1.0
    persistence_drive: float = 0.5     # 0.0-1.0
    confidence: float = 0.5            # action confidence, not emotional certainty


@dataclass
class ToolDecision:
    """Result of the cognitive action arbiter."""
    decision: Literal["execute", "clarify", "defer", "refuse"]
    intent: ToolIntent | None = None
    rationale: str = ""


@dataclass
class ToolResult:
    """Execution output from a tool, always structured."""
    tool_name: str
    success: bool
    output: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    side_effect_summary: str = ""


@dataclass
class ToolObservation:
    """Cognitively appraised version of a tool result."""
    summary: str
    emotional_delta: dict[str, float] = field(default_factory=dict)
    certainty_delta: float = 0.0
    resolution_delta: float = 0.0
    self_observation: str | None = None
    unresolved_item: UnresolvedItem | None = None
    continue_tool_loop: bool = False


@dataclass
class ToolTrace:
    """Debug trace for every tool-involved turn."""
    proposed_intents: list[ToolIntent] = field(default_factory=list)
    final_decision: ToolDecision | None = None
    executed_results: list[ToolResult] = field(default_factory=list)
    observations: list[ToolObservation] = field(default_factory=list)
    loop_count: int = 0


# ---------------------------------------------------------------------------
# Action Variables — derived per-turn from modulators (Section 8)
# ---------------------------------------------------------------------------

@dataclass
class ActionVariables:
    """Turn-level derived variables that shape tool decisions.

    Not stored modulators — computed fresh each turn from ModulatorState,
    trust level, and defense state.
    """
    risk_tolerance: float = 0.5
    action_urgency: float = 0.3
    clarification_threshold: float = 0.5
    persistence_drive: float = 0.5
    autonomy_bias: float = 0.5

    def __post_init__(self) -> None:
        for attr in (
            "risk_tolerance", "action_urgency", "clarification_threshold",
            "persistence_drive", "autonomy_bias",
        ):
            setattr(self, attr, max(0.0, min(1.0, getattr(self, attr))))
