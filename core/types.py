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

from config.loader import SoulConfig


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


@dataclass
class SemanticMemoryEntry:
    """Explicit semantic memory for facts, preferences, decisions, and episodes."""

    id: int | None = None
    timestamp: float = field(default_factory=time.time)
    kind: str = "episode"  # episode | preference | decision | fact
    source_person: str = ""
    topic: str = ""
    summary: str = ""
    content: str = ""
    source: str = "conversation"
    confidence: float = 0.0
    salience: float = 0.0
    tags: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "kind": self.kind,
            "source_person": self.source_person,
            "topic": self.topic,
            "summary": self.summary,
            "content": self.content,
            "source": self.source,
            "confidence": self.confidence,
            "salience": self.salience,
            "tags": list(self.tags),
            "score": self.score,
        }


@dataclass
class RelationshipEvent:
    """A durable relational event in the user-assistant arc."""
    id: int | None = None
    event_kind: str = ""  # rupture | repair | commitment | recurring_tension
    source_person: str = ""
    topic: str = ""
    summary: str = ""
    valence: float = 0.0  # -1.0 to 1.0
    intensity: float = 0.0
    confidence: float = 0.0
    created_at: float = field(default_factory=time.time)
    related_key: str = ""

    def __post_init__(self) -> None:
        self.valence = max(-1.0, min(1.0, self.valence))
        self.intensity = max(0.0, min(1.0, self.intensity))
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "event_kind": self.event_kind,
            "source_person": self.source_person,
            "topic": self.topic,
            "summary": self.summary,
            "valence": self.valence,
            "intensity": self.intensity,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "related_key": self.related_key,
        }


@dataclass
class OpenLoop:
    """A still-unresolved relational thread that should carry across sessions."""
    id: int | None = None
    loop_kind: str = ""  # tension | commitment
    source_person: str = ""
    topic: str = ""
    description: str = ""
    intensity: float = 0.0
    status: Literal["open", "resolved"] = "open"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    related_key: str = ""

    def __post_init__(self) -> None:
        self.intensity = max(0.0, min(1.0, self.intensity))
        if self.status not in {"open", "resolved"}:
            self.status = "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "loop_kind": self.loop_kind,
            "source_person": self.source_person,
            "topic": self.topic,
            "description": self.description,
            "intensity": self.intensity,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
            "related_key": self.related_key,
        }


@dataclass
class RelationshipContext:
    """Compact relationship-memory summary passed into generation and debug."""
    summary: str = ""
    active_loops: list[OpenLoop] = field(default_factory=list)
    recent_events: list[RelationshipEvent] = field(default_factory=list)
    open_loop_count: int = 0

    def is_empty(self) -> bool:
        return not self.summary and not self.active_loops and not self.recent_events

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "active_loops": [loop.to_dict() for loop in self.active_loops],
            "recent_events": [event.to_dict() for event in self.recent_events],
            "open_loop_count": self.open_loop_count,
        }


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
# Appraisal
# ---------------------------------------------------------------------------

@dataclass
class AppraisalFrame:
    """Turn-level social appraisal inferred from the user's message.

    This captures how the message lands socially, beyond surface sentiment:
    who/what it is aimed at, what social move it is making, and how much it
    appears to seek connection, repair, or support.
    """

    speaker_role: str = "user"
    primary_target: str = "unknown"      # assistant | self | external | other_person | shared_problem | unknown
    social_move: str = "inform"          # attack | complaint | vulnerability | gratitude | apology | request | connection | inform
    inferred_intent: str = "inform"      # harm | repair | affiliate | seek_support | seek_action | share_state | inform
    blame: float = 0.0
    controllability: float = 0.5
    expectation_violation: float = 0.0
    vulnerability: float = 0.0
    affiliation_bid: float = 0.0
    mixed_affect: bool = False
    targets_assistant: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        for field_name in (
            "blame",
            "controllability",
            "expectation_violation",
            "vulnerability",
            "affiliation_bid",
        ):
            value = getattr(self, field_name)
            setattr(self, field_name, max(0.0, min(1.0, value)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker_role": self.speaker_role,
            "primary_target": self.primary_target,
            "social_move": self.social_move,
            "inferred_intent": self.inferred_intent,
            "blame": self.blame,
            "controllability": self.controllability,
            "expectation_violation": self.expectation_violation,
            "vulnerability": self.vulnerability,
            "affiliation_bid": self.affiliation_bid,
            "mixed_affect": self.mixed_affect,
            "targets_assistant": self.targets_assistant,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Response strategy
# ---------------------------------------------------------------------------

class ResponseStrategy(str, Enum):
    """High-level response approach selected before generation."""
    VALIDATE = "validate"
    REASSURE = "reassure"
    REPAIR = "repair"
    GROUND = "ground"
    GIVE_SPACE = "give_space"
    PRACTICAL_HELP = "practical_help"
    CHALLENGE_GENTLY = "challenge_gently"
    SET_BOUNDARY = "set_boundary"


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
    soul_profile: SoulConfig | None = None
    person_profile: PersonProfile | None = None
    self_profile: SelfProfile | None = None
    appraisal_frame: AppraisalFrame | None = None
    relationship_context: RelationshipContext | None = None
    topic_profiles: list[TopicProfile] = field(default_factory=list)
    values: ValueHierarchy = field(default_factory=ValueHierarchy)
    retrieved_memories: list[LongTermEntry] = field(default_factory=list)
    semantic_memories: list[SemanticMemoryEntry] = field(default_factory=list)
    short_term_history: list[ShortTermEntry] = field(default_factory=list)
    contradiction_flags: list[str] = field(default_factory=list)
    contagion: DetectedEmotion | None = None
    # v2: inner dialogue candidate and defense instruction
    candidate_response: str = ""
    defense_instruction: str = ""
    # Phase 11.3: response strategy hint for generator
    response_strategy: str = ""
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "intensity": self.intensity,
            "decay_rate": self.decay_rate,
            "resolved": self.resolved,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnresolvedItem:
        created_at_raw = data.get("created_at")
        resolved_at_raw = data.get("resolved_at")
        created_at = (
            datetime.fromisoformat(created_at_raw)
            if created_at_raw
            else datetime.utcnow()
        )
        resolved_at = (
            datetime.fromisoformat(resolved_at_raw)
            if resolved_at_raw
            else None
        )
        return cls(
            id=str(data.get("id", "")),
            source=str(data.get("source", "")),
            description=str(data.get("description", "")),
            created_at=created_at,
            intensity=float(data.get("intensity", 0.0)),
            decay_rate=float(data.get("decay_rate", 0.0)),
            resolved=bool(data.get("resolved", False)),
            resolved_at=resolved_at,
        )


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
    task_trace: Any = None  # TaskTrace | None — forward ref avoids circular


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


# ---------------------------------------------------------------------------
# Task Planning — multi-step task model (Phase 7)
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    """Status of a task step or plan."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class TaskStep:
    """A single step in a multi-step task plan."""
    id: str
    tool_name: str
    arguments: dict[str, Any]
    description: str
    status: TaskStatus = TaskStatus.PENDING
    result: ToolResult | None = None
    observation: ToolObservation | None = None
    started_at: float | None = None
    completed_at: float | None = None


@dataclass
class TaskPlan:
    """A bounded multi-step task plan.

    Max 5 steps hard cap.  Plans are session-scoped — they live in
    pipeline memory and are cleared on end_session().
    """
    id: str
    goal: str
    steps: list[TaskStep]
    status: TaskStatus = TaskStatus.PENDING
    current_step_index: int = 0
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    # Cognitive context
    persistence_drive: float = 0.5  # from action variables when plan was created

    @property
    def max_steps(self) -> int:
        return 5

    @property
    def steps_completed(self) -> int:
        return sum(1 for s in self.steps if s.status == TaskStatus.COMPLETED)

    @property
    def steps_failed(self) -> int:
        return sum(1 for s in self.steps if s.status == TaskStatus.FAILED)

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED)


@dataclass
class TaskTrace:
    """Debug trace for a multi-step task execution."""
    plan: TaskPlan | None = None
    steps_executed: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    total_latency_ms: float = 0.0
    continued_after_failure: bool = False
    plan_outcome: str = ""  # "completed" | "failed" | "blocked" | "partial" | ""


# ---------------------------------------------------------------------------
# Proactive Behavior — trigger model (Phase 8)
# ---------------------------------------------------------------------------

class ProactiveTriggerSource(str, Enum):
    """Source of a proactive trigger."""
    UNRESOLVED_ITEM = "unresolved_item"
    PENDING_TASK = "pending_task"
    COMMITMENT = "commitment"
    TEMPORAL = "temporal"
    EMOTIONAL_SALIENCE = "emotional_salience"


@dataclass
class ProactiveTrigger:
    """A single trigger that may compel proactive behavior."""
    source: ProactiveTriggerSource
    description: str
    intensity: float  # 0.0-1.0
    item_id: str | None = None  # reference to unresolved item or task plan

    def __post_init__(self) -> None:
        self.intensity = max(0.0, min(1.0, self.intensity))


@dataclass
class ProactiveAction:
    """A proactive action Jarvis wants to take.

    action_type:
      - follow_up: send a follow-up message about something unresolved
      - continue_task: resume a pending multi-step plan
      - suggest: suggest an action without executing
      - autonomous_step: execute a bounded tool step on an existing plan
      - none: evaluation ran but no action warranted
    """
    action_type: Literal["follow_up", "continue_task", "suggest", "autonomous_step", "none"]
    trigger: ProactiveTrigger
    message: str  # candidate message for the generator
    rationale: str


@dataclass
class ProactiveTrace:
    """Debug trace for a proactive evaluation cycle."""
    triggers_found: list[ProactiveTrigger] = field(default_factory=list)
    action_taken: ProactiveAction | None = None
    suppressed_reasons: list[str] = field(default_factory=list)
    limits_applied: dict[str, Any] = field(default_factory=dict)
    idle_seconds: float = 0.0
    proactive_count: int = 0
    timestamp: float = field(default_factory=time.time)
