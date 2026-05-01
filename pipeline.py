"""Cognitive pipeline orchestrator.

v2 processing flow:
 1. Anticipation: predict emotional trajectory (0 LLM calls)
 2. Contagion: detect user tone → bounded mirror (0 LLM calls — rule-based)
 3. Appraisal: infer target, intent, vulnerability, affiliation (0 LLM calls)
 4. Context switch: load person profile baseline_shift
 5. Event classification: categorize input (0 LLM calls — rule-based)
 6. PSI engine: update 6 modulators from input + drives + energy
 7. Resolution update: check for new/resolved tension items (0 LLM calls)
 8. Short-term memory: store emotional reaction
 9. Spike check: if intensity > 0.8 → heavy write to LT
10. Memory retrieval: ACT-R activation biased by current state
11. Profile lookup: person + self + topic
12. Contradiction check: compare against profiles (self + others)
13. Inner dialogue: fast/slow deliberation only for non-spike unresolved tension
    (0-5 LLM calls; spike-only turns skip)
13b. Tool loop: detect intent → arbiter → execute → appraise (0+ tool calls;
     skipped if no tool_executor configured)
14. Defense mechanisms: filter output if needed (0 LLM calls)
15. Master LLM: generate final response (1 LLM call)
16. Self-check: rule-based default; LLM only for extreme/high-risk turns
    (0-1 LLM calls)
17. Post-processing: update memory, drain energy
18. [Session end] Digestion (0-1 LLM call)

LLM call budget: 1-6 per message (typical: 1). Spike-only hostility should stay
on the generator path unless a separate high-risk condition warrants LLM
self-check. Inner dialogue only fires when non-spike unresolved items exist
(contradictions, deadlocks, etc.).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from config.loader import get_config
from core.types import (
    ActionVariables,
    AffectState,
    AgencyDecision,
    AppraisalFrame,
    Anticipation,
    DefenseActivation,
    DetectedEmotion,
    EmotionalEvent,
    EventType,
    InnerDialogueTrace,
    LongTermEntry,
    ModulatorState,
    PipelineContext,
    PersonProfile,
    RelationshipContext,
    ResponseStrategy,
    SemanticMemoryEntry,
    SelfProfile,
    StrategyDecisionTrace,
    ToolCategory,
    TopicProfile,
    ToolTrace,
    UnresolvedItem,
    ValueHierarchy,
)
from core.appraisal import appraise_message
from core.affect import decide_agency, resolve_affect
from core.strategy import select_strategy_with_trace, STRATEGY_INSTRUCTIONS
from core.grounding import grounding_correction_response, verify_response_grounding
from core.emotional_engine import EmotionalEngine, SPIKE_INTENSITY_THRESHOLD
from core.memory.short_term import ShortTermMemory
from core.memory.long_term import LongTermMemory
from core.memory.digestion import digest_session, DigestedSession
from core.memory.relationship import NullRelationshipMemory, RelationshipMemory
from core.memory.semantic import (
    NullSemanticMemory,
    create_semantic_memory,
    derive_semantic_entries,
)
from core.life_influence import LifeInfluence, curiosity_salience_bonus, derive_life_influence
from core.pipeline_features import PipelineFeatures
from core.contagion import detect_emotion
from core.profiles.base import ProfileStore
from core.profiles.person import PersonProfileManager
from core.profiles.self_model import SelfProfileManager, SELF_ENTITY_ID
from core.profiles.topic import TopicProfileManager
from core.profiles.contradiction import ContradictionDetector
from core.dual_process.generator import (
    LLMBackend,
    MockLLMBackend,
    ResponseGenerator,
)
from core.dual_process.self_check import SelfChecker
from core.dual_process.inner_dialogue import InnerDialogue
from core.anticipation import AnticipationEngine
from core.defense_mechanisms import DEFENSE_INSTRUCTIONS, DefenseMechanism
from core.dual_process.tool_loop import run_tool_loop
from core.proactive import evaluate_proactive
from core.tool_memory import (
    ToolMemoryEffects,
    compute_tool_trust_delta,
    create_long_term_entry,
    create_task_long_term_entry,
    create_task_unresolved_item,
    create_tool_event,
    create_tool_unresolved_item,
    derive_task_self_observations,
    derive_tool_self_observations,
    is_salient_episode,
)
from core.types import ProactiveTrace, TaskPlan, TaskTrace

log = logging.getLogger(__name__)

_TOOL_FOLLOWUP_COMMAND_RE = re.compile(
    r"("
    r"\b(?:issue|run|execute|use)\s+(?:the\s+)?"
    r"(?:needed|required|necessary|right)\s+command\b"
    r"|^\s*(?:go|do\s+it|do\s+that|please\s+do|run\s+it|check\s+again|try\s+again|"
    r"create\s+it|make\s+it|i\s+want\s+you\s+to\s+create\s+it|for\s+yourself|"
    r"now\s+check\s+again|give\s+me\s+(?:the\s+)?(?:raw\s+)?output\b.*|"
    r"show\s+me\s+(?:the\s+)?output\b.*)\s*[.!?]*\s*$"
    r")",
    re.IGNORECASE,
)

_TOOL_HISTORY_ACTION_HINT_RE = re.compile(
    r"\b(?:hostname|host\s*name|machine\s+name|uname|/etc/hostname|"
    r"disk|drive|filesystem|storage|space|df\s+-h|"
    r"skill|capability|capabilities|module|integration|registry|"
    r"search|web|internet|fetch|read\s+file|list\s+files|"
    r"cat\s+/|grep|calendar|events?|"
    r"(?:run|execute|issue)\s+(?:the\s+)?(?:command\s+)?[A-Za-z0-9_./~+-]|"
    r"^\s*do\s+[A-Za-z0-9_./~+-])\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Debug state — full transparency into what happened
# ---------------------------------------------------------------------------

@dataclass
class DebugState:
    """Complete debug snapshot of a single pipeline run."""
    timestamp: float = field(default_factory=time.time)
    user_message: str = ""
    user_id: str = ""

    # Step 2: Contagion
    detected_emotion: DetectedEmotion | None = None

    # Step 3: Social appraisal
    appraisal_frame: AppraisalFrame | None = None

    # Step 4: Context switch
    baseline_shift_applied: dict[str, float] = field(default_factory=dict)

    # Step 5: Modulator update
    event_classified: str = ""
    event_intensity: float = 0.0
    is_spike: bool = False
    modulator_snapshot: dict[str, float] = field(default_factory=dict)

    # Step 7: Memory retrieval
    retrieved_memories: list[LongTermEntry] = field(default_factory=list)
    semantic_memories: list[SemanticMemoryEntry] = field(default_factory=list)
    life_history_context: dict[str, Any] = field(default_factory=dict)
    life_influence: LifeInfluence = field(default_factory=LifeInfluence)
    life_influence_effects: dict[str, Any] = field(default_factory=dict)
    skill_context: dict[str, Any] = field(default_factory=dict)
    relationship_context: RelationshipContext | None = None

    # Step 8: Profiles
    person_profile: PersonProfile | None = None
    self_profile: SelfProfile | None = None
    topic_profiles: list[TopicProfile] = field(default_factory=list)

    # Step 9: Contradictions
    contradiction_flags: list[str] = field(default_factory=list)

    # Steps 10-11: Generation
    response: str = ""
    self_check_passed: bool = True
    self_check_issues: list[str] = field(default_factory=list)
    correction_note: str = ""
    generation_attempts: int = 0

    # Step 14: Energy
    energy_after: float = 0.0

    # Emotion label (for display)
    emotion_label: str = ""

    # v2: Anticipation
    anticipation: Anticipation | None = None

    # v2: Inner dialogue
    dialogue_trace: InnerDialogueTrace | None = None

    # v2: Defense mechanisms
    defense_activation: DefenseActivation | None = None

    # v2: Resolution
    unresolved_count: int = 0
    unresolved_items: list[UnresolvedItem] = field(default_factory=list)

    # Agentic tools (Phase 0+)
    tool_trace: ToolTrace | None = None
    action_variables: ActionVariables | None = None
    tool_memory_effects: ToolMemoryEffects | None = None
    task_trace: TaskTrace | None = None
    strategy_trace: StrategyDecisionTrace | None = None

    # Phase 11.3: Response strategy
    response_strategy: str = ""

    # Derived affect and agency
    affect_state: AffectState | None = None
    agency_decision: AgencyDecision | None = None
    autonomy_level: str = ""

    # Proactive behavior (Phase 8)
    proactive_trace: ProactiveTrace | None = None

    # Timing instrumentation (ms)
    stage_timings_ms: dict[str, float] = field(default_factory=dict)


def _message_for_tool_detection(
    user_message: str,
    conversation_history: list[dict[str, str]],
) -> str:
    """Attach the prior user request for terse tool follow-ups."""
    if not _TOOL_FOLLOWUP_COMMAND_RE.search(user_message):
        return user_message
    for message in reversed(conversation_history):
        if message.get("role") == "user" and message.get("content"):
            content = message["content"]
            if _TOOL_HISTORY_ACTION_HINT_RE.search(content):
                return f"{content}\n{user_message}"
    return user_message


def _message_for_model_tool_routing(
    user_message: str,
    conversation_history: list[dict[str, str]],
) -> str:
    """Attach recent turns so model-native routers can resolve follow-ups."""
    if not conversation_history:
        return user_message
    lines = ["Recent conversation for tool routing:"]
    for message in conversation_history[-8:]:
        role = "User" if message.get("role") == "user" else "Assistant"
        content = str(message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    lines.append(f"Current user message: {user_message}")
    return "\n".join(lines)


def _relationship_topic_hint(text: str) -> str:
    """Small topic hint so relationship-memory loops can be prioritized."""
    lower = text.lower()
    match = re.search(r"\b(?:about|regarding|around|on)\s+([a-z0-9' -]{2,40})", lower)
    if match:
        return _clean_topic_hint(match.group(1))
    words = re.findall(r"[a-z0-9']+", lower)
    stopwords = {
        "the", "and", "that", "this", "with", "your", "you", "for", "from",
        "have", "been", "just", "really", "very", "again", "still", "please",
        "drop", "topic", "issue", "thing", "can", "could", "would", "what",
        "when", "where", "why", "how", "any", "follow", "up",
        "unresolved", "resolved", "matters", "matter", "pattern",
    }
    informative = [word for word in words if len(word) > 2 and word not in stopwords]
    return " ".join(informative[:3])


def _clean_topic_hint(text: str) -> str:
    phrase = re.sub(r"[^a-z0-9' -]", " ", text.lower())
    phrase = " ".join(phrase.split())
    return phrase[:40].strip()


# ---------------------------------------------------------------------------
# Pipeline response
# ---------------------------------------------------------------------------

@dataclass
class PipelineResponse:
    """What the pipeline returns to the caller."""
    response: str
    debug: DebugState


# ---------------------------------------------------------------------------
# CognitivePipeline
# ---------------------------------------------------------------------------

class CognitivePipeline:
    """Orchestrates the full v2 cognitive processing flow."""

    def __init__(
        self,
        llm_backend: LLMBackend | None = None,
        llm_backend_fast: LLMBackend | None = None,
        db_path: str = ":memory:",
        self_db_path: str | None = None,
        tool_executor: Any | None = None,
        autonomy_level: str = "autonomous",
        features: PipelineFeatures | None = None,
        life_history_provider: Callable[[], dict[str, Any] | None] | None = None,
        skill_provider: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        """Create a cognitive pipeline.

        Args:
            llm_backend: Primary LLM backend.
            llm_backend_fast: Fast backend (thinking off). Falls back to primary.
            db_path: Per-user database path for memories, person profiles,
                     topic profiles, and person observations.
            self_db_path: Shared database path for self-model observations and
                          defense history.  When None, falls back to db_path
                          (single-DB mode, backward compatible).
            tool_executor: Optional ToolExecutor for agentic tool use.
                           When None, the tool loop is skipped entirely.
            autonomy_level: Tool autonomy mode (off, assisted, autonomous,
                            high_risk). Direct pipeline use defaults to
                            autonomous for backwards compatibility.
            features: Feature toggles for ablation runs. Default (None)
                      enables every component. See ``PipelineFeatures``.
            life_history_provider: Optional callable returning a compact
                                   identity-level life-history context for
                                   generation. Runtime sessions wire this to
                                   ``data/shared/life_history.db``.
            skill_provider: Optional callable returning enabled imported skill
                            guidance for generation.
        """
        self._features = features or PipelineFeatures()
        # Primary backend (used if no fast backend provided)
        self._llm_backend = llm_backend or MockLLMBackend()
        # Fast backend (thinking mode off) — used for ALL calls
        # Generator prompt already has full context; thinking overhead not needed
        self._llm_backend_fast = llm_backend_fast or self._llm_backend
        self._config = get_config()
        self.soul = self._config.soul
        self._semantic_cfg = self._config.semantic_memory

        # Core engine
        self.engine = EmotionalEngine()

        # Memory
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory(db_path=db_path)
        # Relationship and semantic memory honor the feature toggles.
        # Disabled components return a null object whose writes are no-ops
        # and whose reads are empty — see core/pipeline_features.py.
        if self._features.relationship_memory:
            self.relationship_memory = RelationshipMemory(db_path=db_path)
        else:
            self.relationship_memory = NullRelationshipMemory()
        if self._features.semantic_memory:
            self.semantic_memory = create_semantic_memory(
                db_path=db_path,
                config=self._semantic_cfg,
            )
        else:
            self.semantic_memory = NullSemanticMemory()

        # Per-user profile store (person observations + extracted traits)
        self._person_profile_store = ProfileStore(db_path=db_path)
        self.person_profiles = PersonProfileManager(self._person_profile_store, db_path=db_path)
        self.topic_profiles = TopicProfileManager(db_path=db_path)

        # Shared self-model store (self observations + defense events)
        # WAL mode + busy timeout when shared DB is explicitly separated
        effective_self_db = self_db_path if self_db_path is not None else db_path
        self._self_profile_store = ProfileStore(
            db_path=effective_self_db,
            wal_mode=(self_db_path is not None),
        )
        self.self_profile = SelfProfileManager(self._self_profile_store)

        # Contradiction detectors — one per store
        self._person_contradiction = ContradictionDetector(self._person_profile_store)
        self._self_contradiction = ContradictionDetector(self._self_profile_store)

        # Master generator uses fast backend (thinking off — prompt has full context)
        self.generator = ResponseGenerator(backend=self._llm_backend_fast)
        # Self-checker: rule-based by default; LLM only for high-intensity turns
        self.self_checker = SelfChecker(llm_client=None)
        self._self_check_llm = SelfChecker(llm_client=self._llm_backend_fast)

        # Inner dialogue uses fast backend (no thinking needed for gut reaction + evaluation)
        self.inner_dialogue = InnerDialogue(backend=self._llm_backend_fast)
        self.anticipation_engine = AnticipationEngine()
        self.defense_mechanism = DefenseMechanism()

        # Values are seeded from the authored soul and remain stable unless updated deliberately.
        self.values = ValueHierarchy(values=dict(self._config.values))

        # Conversation history for context
        self._conversation_history: list[dict[str, str]] = []

        # Agentic tools (Phase 2+)
        self._tool_executor = tool_executor
        self._tool_runner = getattr(tool_executor, "_tool_runner", None)
        self._autonomy_level = autonomy_level
        self._life_history_provider = life_history_provider
        self._skill_provider = skill_provider
        # Session-scoped task plan (Phase 7)
        self._active_task_plan: TaskPlan | None = None
        # Proactive behavior tracking (Phase 8)
        self._proactive_count: int = 0
        self._last_proactive_at: float | None = None

        # Time tracking for auto-decay between turns
        self._last_turn_time: float | None = None
        # Pipelines are session-scoped; changing users on one instance leaks state.
        self._bound_user_id: str | None = None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self, user_message: str, user_id: str = "default") -> PipelineResponse:
        """Process a user message through the full v2 cognitive pipeline.

        Flow: anticipation → contagion → appraisal → context → event →
              resolution → memory → profiles → contradiction → inner dialogue →
              defense → master LLM → post-processing.

        Returns the response text and full debug state.
        """
        self._ensure_bound_user(user_id)
        debug = DebugState(user_message=user_message, user_id=user_id)
        person = self.person_profiles.get_or_create(user_id)
        _t_total = time.perf_counter()
        timings: dict[str, float] = {}

        # ---- Step 0: Auto-decay based on elapsed time ----
        now = time.time()
        if self._last_turn_time is not None:
            elapsed = now - self._last_turn_time
            if elapsed > 0:
                self.engine.decay(elapsed)
        self._last_turn_time = now

        # ---- Step 1: ANTICIPATION (0 LLM calls) ----
        _ts = time.perf_counter()
        recent_msgs = [
            m["content"] for m in self._conversation_history
            if m["role"] == "user"
        ][-5:]
        all_topics = self.topic_profiles.all_profiles()
        topic_dict = {tp.topic: tp for tp in all_topics}

        anticipation = self.anticipation_engine.predict(
            recent_messages=recent_msgs,
            person_profile=person,
            topic_profiles=topic_dict,
            current_state=self.engine.state,
            unresolved_items=self.engine.active_unresolved(),
        )
        self.anticipation_engine.apply_pre_shift(self.engine.state, anticipation)
        debug.anticipation = anticipation
        timings["anticipation"] = (time.perf_counter() - _ts) * 1000

        # ---- Step 2: CONTAGION (0 LLM calls — rule-based) ----
        _ts = time.perf_counter()
        detected = detect_emotion(user_message, llm_client=None)
        debug.detected_emotion = detected
        self.engine.apply_contagion(detected.arousal, detected.valence, person.trust)
        timings["contagion"] = (time.perf_counter() - _ts) * 1000

        # ---- Step 3: APPRAISAL (0 LLM calls — deterministic) ----
        _ts = time.perf_counter()
        appraisal = appraise_message(user_message, detected)
        debug.appraisal_frame = appraisal
        timings["appraisal"] = (time.perf_counter() - _ts) * 1000

        # ---- Step 4: Context switch (non-additive — sets resting target) ----
        shift = self.person_profiles.get_baseline_shift(user_id)
        self.engine.set_context_shift(shift)
        debug.baseline_shift_applied = shift.to_dict()

        # ---- Step 5: Event classification + PSI engine update ----
        _ts = time.perf_counter()
        event = self._classify_event(user_message, detected, appraisal)
        is_spike = self.engine.update(event)
        debug.event_classified = event.event_type.value
        debug.event_intensity = event.intensity
        debug.is_spike = is_spike
        timings["event_classification"] = (time.perf_counter() - _ts) * 1000

        # ---- Step 6: Resolution update (0 LLM calls) ----
        # Check if this event creates new unresolved items
        self._check_resolution_sources(event, user_message, user_id)
        active_unresolved = self.engine.active_unresolved()
        debug.unresolved_count = len(active_unresolved)
        debug.unresolved_items = list(active_unresolved)

        debug.modulator_snapshot = self.engine.snapshot()

        # ---- Step 7: Short-term memory ----
        self.short_term.record(event, self.engine.state)

        # ---- Step 8: Spike check ----
        if is_spike:
            spike_entry = LongTermEntry(
                timestamp=time.time(),
                summary=f"Spike: {event.event_type.value} from {user_id}",
                emotional_valence=self._event_valence(event),
                trust_delta=LongTermMemory.compute_trust_delta(self._event_valence(event)),
                source_person=user_id,
                confidence=1.0,
                spike=True,
            )
            self.long_term.store_spike(spike_entry)

        # ---- Step 9: Memory retrieval ----
        _ts = time.perf_counter()
        retrieved = self.long_term.retrieve(
            self.engine.state,
            source_person=user_id,
            limit=5,
        )
        debug.retrieved_memories = retrieved
        timings["memory_retrieval"] = (time.perf_counter() - _ts) * 1000

        # ---- Step 10: Profile lookup ----
        person = self.person_profiles.get_or_create(user_id)
        self_prof = self.self_profile.get_profile()
        active_topics = self._detect_topics(user_message)
        relationship_topic = active_topics[0].topic if active_topics else _relationship_topic_hint(user_message)
        _ts = time.perf_counter()
        relationship_context = self.relationship_memory.build_context(
            user_id,
            topic=relationship_topic,
            event_limit=3,
            loop_limit=3,
        )
        if relationship_context.is_empty():
            relationship_context = None
        debug.person_profile = person
        debug.self_profile = self_prof
        debug.topic_profiles = active_topics
        debug.relationship_context = relationship_context
        timings["relationship_retrieval"] = (time.perf_counter() - _ts) * 1000

        _ts = time.perf_counter()
        semantic_memories = self.semantic_memory.retrieve(
            user_message,
            source_person=user_id,
            topic=relationship_topic,
            limit=self._semantic_cfg.retrieval_limit,
        )
        debug.semantic_memories = semantic_memories
        timings["semantic_memory_retrieval"] = (time.perf_counter() - _ts) * 1000

        _ts = time.perf_counter()
        life_history_context = self._load_life_history_context()
        debug.life_history_context = life_history_context
        life_influence = derive_life_influence(life_history_context)
        debug.life_influence = life_influence
        timings["life_history_retrieval"] = (time.perf_counter() - _ts) * 1000

        _ts = time.perf_counter()
        skill_context = self._load_skill_context()
        debug.skill_context = skill_context
        timings["skill_context_retrieval"] = (time.perf_counter() - _ts) * 1000

        # ---- Step 11: Contradiction check ----
        contradiction_flags: list[str] = []

        person_expected = self.person_profiles.get_expected_traits(user_id)
        if person_expected:
            person_result = self._person_contradiction.detect(user_id, person_expected)
            for c in person_result.contradictions:
                contradiction_flags.append(c.description)

        self_expected = self._effective_self_expected_traits()
        if self_expected:
            self_result = self._self_contradiction.detect(SELF_ENTITY_ID, self_expected)
            for c in self_result.contradictions:
                contradiction_flags.append(c.description)

        debug.contradiction_flags = contradiction_flags

        # Wire contradictions and topics as unresolved sources
        if contradiction_flags:
            self._check_contradiction_resolution(contradiction_flags, user_id)
        if active_topics:
            self._check_topic_resolution(active_topics)
        # Update resolution counts after new sources
        active_unresolved = self.engine.active_unresolved()
        debug.unresolved_count = len(active_unresolved)
        debug.unresolved_items = list(active_unresolved)

        # ---- Step 11b: Derived affect + agency (0 LLM calls) ----
        _ts = time.perf_counter()
        affect_state = resolve_affect(
            text=user_message,
            state=self.engine.state,
            appraisal=appraisal,
            person=person,
        )
        agency_decision = decide_agency(affect_state, appraisal, person)
        debug.affect_state = affect_state
        debug.agency_decision = agency_decision
        debug.autonomy_level = self._autonomy_level
        timings["affect_agency"] = (time.perf_counter() - _ts) * 1000

        # ---- Step 12: INNER DIALOGUE (0-5 LLM calls; 0 for calm) ----
        _ts = time.perf_counter()
        contagion_summary = (
            f"arousal={detected.arousal:.2f}, valence={detected.valence:.2f}, "
            f"intensity={detected.intensity:.2f}"
        )
        short_term_summary = f"{len(self.short_term)} entries in short-term memory"

        if self._features.inner_dialogue:
            dialogue_trace = self.inner_dialogue.deliberate(
                user_message=user_message,
                state=self.engine.state,
                person=person,
                self_profile=self_prof,
                values=self.values,
                memories=retrieved,
                unresolved=self.engine.active_unresolved(),
                contagion_summary=contagion_summary,
                short_term_summary=short_term_summary,
                current_event_intensity=event.intensity,
            )
        else:
            # Disabled: produce an empty trace so downstream shape is stable.
            dialogue_trace = InnerDialogueTrace(
                rounds=[],
                final_candidate="",
                total_llm_calls=0,
                reached_deadlock=False,
                dominant_path="fast",
                tension_level=0.0,
            )
        debug.dialogue_trace = dialogue_trace
        timings["inner_dialogue"] = (time.perf_counter() - _ts) * 1000

        # If deadlock, feed it to resolution modulator.
        # (No-op when inner dialogue is disabled — trace.reached_deadlock=False.)
        deadlock_item = (
            self.inner_dialogue.create_deadlock_item(dialogue_trace)
            if self._features.inner_dialogue
            else None
        )
        if deadlock_item:
            self.engine.add_unresolved(deadlock_item)
            active_unresolved = self.engine.active_unresolved()
            debug.unresolved_count = len(active_unresolved)
            debug.unresolved_items = list(active_unresolved)

        # ---- Step 12b: TOOL LOOP (0+ tool executions; skipped if no executor) ----
        tool_context_summary = ""
        if self._tool_executor is not None:
            _ts_tool = time.perf_counter()
            if self._tool_runner is not None:
                tool_user_message = _message_for_model_tool_routing(
                    user_message,
                    self._conversation_history,
                )
                tool_loop_result = self._tool_runner.run_tool_loop(
                    user_message=tool_user_message,
                    state=self.engine.state,
                    person=person,
                    defense_active=False,  # defense hasn't fired yet
                    engine=self.engine,
                    active_plan=self._active_task_plan,
                    agency_decision=agency_decision,
                    autonomy_level=self._autonomy_level,
                    life_influence=life_influence,
                )
            else:
                tool_user_message = _message_for_tool_detection(
                    user_message,
                    self._conversation_history,
                )
                tool_loop_result = run_tool_loop(
                    user_message=tool_user_message,
                    state=self.engine.state,
                    person=person,
                    defense_active=False,  # defense hasn't fired yet
                    executor=self._tool_executor,
                    engine=self.engine,
                    active_plan=self._active_task_plan,
                    agency_decision=agency_decision,
                    autonomy_level=self._autonomy_level,
                    life_influence=life_influence,
                )
            debug.tool_trace = tool_loop_result.trace
            debug.action_variables = tool_loop_result.action_variables
            tool_context_summary = tool_loop_result.tool_context_summary
            if tool_loop_result.life_influence_effects:
                debug.life_influence_effects.update(tool_loop_result.life_influence_effects)

            # Track task trace and active plan (Phase 7)
            task_trace = tool_loop_result.trace.task_trace
            if task_trace and task_trace.plan:
                debug.task_trace = task_trace
                self._active_task_plan = task_trace.plan
                # Clear terminal plans from session state
                if task_trace.plan.is_terminal:
                    self._active_task_plan = None

            # ---- Step 12c: TOOL MEMORY COUPLING ----
            trace = tool_loop_result.trace
            if trace.executed_results:
                effects = ToolMemoryEffects()
                failure_count = 0

                for result, observation in zip(trace.executed_results, trace.observations):
                    cap = self._tool_executor._registry.get(result.tool_name)
                    category = cap.category if cap else ToolCategory.READ_ONLY

                    if not result.success:
                        failure_count += 1

                    # Short-term memory record
                    tool_event = create_tool_event(result, observation, self.engine.state)
                    self.short_term.record(tool_event, self.engine.state)
                    effects.short_term_recorded = True

                    # Long-term memory (salient episodes only)
                    if is_salient_episode(result, observation, category, failure_count):
                        lt_entry = create_long_term_entry(
                            result, observation, category, user_id=user_id,
                        )
                        self.long_term.store(lt_entry)
                        effects.long_term_written = True
                        effects.long_term_summary = lt_entry.summary

                    # Self-observations from tool behavior
                    self_obs = derive_tool_self_observations(
                        result, observation, category,
                        tool_loop_result.action_variables,
                        trace.final_decision,
                        failure_count=failure_count,
                    )
                    for trait, value, context in self_obs:
                        self.self_profile.record_behavior(trait, value, context)
                        effects.self_observations.append(f"{trait}={value:.2f}")

                    # Unresolved items from tool failures
                    unresolved = create_tool_unresolved_item(
                        result, observation, category, failure_count=failure_count,
                    )
                    if unresolved:
                        self.engine.add_unresolved(unresolved)
                        effects.unresolved_items_created.append(unresolved.id)

                    # Trust delta from tool outcome
                    trust_delta = compute_tool_trust_delta(
                        result, category, tool_loop_result.action_variables,
                    )
                    if trust_delta != 0.0 and person:
                        person.trust = max(0.0, min(1.0, person.trust + trust_delta))
                        self.person_profiles.save(person)
                        effects.trust_delta += trust_delta

                debug.tool_memory_effects = effects

            # ---- Step 12d: TASK MEMORY COUPLING (Phase 7) ----
            if task_trace and task_trace.plan and task_trace.plan.is_terminal:
                plan = task_trace.plan
                effects = debug.tool_memory_effects or ToolMemoryEffects()

                # Unresolved items from plan outcome
                task_unresolved = create_task_unresolved_item(plan, task_trace)
                if task_unresolved:
                    self.engine.add_unresolved(task_unresolved)
                    effects.unresolved_items_created.append(task_unresolved.id)

                # Self-observations from multi-step behavior
                task_obs = derive_task_self_observations(
                    plan, task_trace, tool_loop_result.action_variables,
                )
                for trait, value, context in task_obs:
                    self.self_profile.record_behavior(trait, value, context)
                    effects.self_observations.append(f"{trait}={value:.2f}")

                # Long-term memory for salient plans
                task_lt = create_task_long_term_entry(
                    plan, task_trace, user_id=user_id,
                )
                if task_lt:
                    self.long_term.store(task_lt)
                    effects.long_term_written = True
                    effects.long_term_summary = task_lt.summary

                debug.tool_memory_effects = effects

            timings["tool_loop"] = (time.perf_counter() - _ts_tool) * 1000

        # ---- Step 13: DEFENSE MECHANISMS (0 LLM calls) ----
        if self._features.defense:
            filtered_output, defense = self.defense_mechanism.evaluate(
                inner_dialogue_output=dialogue_trace.final_candidate,
                modulator_state=self.engine.state,
                self_profile=self_prof,
                person_profile=person,
                topic_profiles=active_topics,
            )
        else:
            # Disabled: candidate flows to generator unmodified, no activation recorded.
            filtered_output = dialogue_trace.final_candidate
            defense = None
        debug.defense_activation = defense

        # ---- Step 13b: RESPONSE STRATEGY (0 LLM calls) ----
        strategy_trace = select_strategy_with_trace(
            appraisal=appraisal,
            modulators=self.engine.snapshot(),
            person=person,
            relationship=relationship_context,
            life_influence=life_influence,
        )
        debug.strategy_trace = strategy_trace
        strategy = ResponseStrategy(strategy_trace.selected)
        debug.response_strategy = strategy.value
        if strategy_trace.matched_rule.startswith("life_"):
            debug.life_influence_effects["strategy_tiebreak_used"] = True

        # ---- Step 14: MASTER LLM (1 LLM call) ----
        _ts = time.perf_counter()
        defense_instruction = ""
        if defense:
            defense_instruction = DEFENSE_INSTRUCTIONS.get(defense.defense_type, "")

        ctx = PipelineContext(
            modulator_snapshot=self.engine.snapshot(),
            soul_profile=self.soul,
            person_profile=person,
            self_profile=self_prof,
            appraisal_frame=appraisal,
            relationship_context=relationship_context,
            topic_profiles=active_topics,
            values=self.values,
            retrieved_memories=retrieved,
            semantic_memories=semantic_memories,
            life_history_context=life_history_context,
            skill_context=skill_context,
            short_term_history=self.short_term.recent(5),
            contradiction_flags=contradiction_flags,
            contagion=detected,
            candidate_response=filtered_output,
            defense_instruction=defense_instruction,
            response_strategy=STRATEGY_INSTRUCTIONS.get(strategy, ""),
            affect_state=affect_state,
            agency_decision=agency_decision,
            autonomy_level=self._autonomy_level,
            tool_context_summary=tool_context_summary,
        )

        gen_result = self.generator.generate(
            ctx, user_message, self._conversation_history
        )
        debug.generation_attempts = 1
        debug.correction_note = gen_result.correction_note
        timings["generator"] = (time.perf_counter() - _ts) * 1000

        # ---- Self-check (rule-based default; LLM only when warranted) ----
        _ts = time.perf_counter()
        checker = (
            self._self_check_llm
            if self._should_use_llm_self_check(
                event, contradiction_flags, dialogue_trace,
            )
            else self.self_checker
        )
        check_result = checker.check(gen_result.response, ctx)
        debug.self_check_passed = check_result.passed
        debug.self_check_issues = check_result.issues

        if check_result.failed:
            # Retry with correction note — preserve v2 candidate/defense context
            correction_candidate = (
                f"{ctx.candidate_response}\n\n"
                f"[Self-check correction: {check_result.correction_note}]"
            ) if ctx.candidate_response else check_result.correction_note
            correction_ctx = PipelineContext(
                modulator_snapshot=ctx.modulator_snapshot,
                soul_profile=ctx.soul_profile,
                person_profile=ctx.person_profile,
                self_profile=ctx.self_profile,
                appraisal_frame=ctx.appraisal_frame,
                relationship_context=ctx.relationship_context,
                topic_profiles=ctx.topic_profiles,
                values=ctx.values,
                retrieved_memories=ctx.retrieved_memories,
                semantic_memories=ctx.semantic_memories,
                life_history_context=ctx.life_history_context,
                skill_context=ctx.skill_context,
                short_term_history=ctx.short_term_history,
                contradiction_flags=ctx.contradiction_flags,
                contagion=ctx.contagion,
                candidate_response=correction_candidate,
                defense_instruction=ctx.defense_instruction,
                response_strategy=ctx.response_strategy,
                affect_state=ctx.affect_state,
                agency_decision=ctx.agency_decision,
                autonomy_level=ctx.autonomy_level,
                tool_context_summary=ctx.tool_context_summary,
            )
            gen_result = self.generator.generate(
                correction_ctx,
                user_message,
                self._conversation_history,
            )
            gen_result.correction_note = check_result.correction_note
            debug.correction_note = check_result.correction_note
            debug.generation_attempts = 2

        grounding_issues = verify_response_grounding(
            gen_result.response,
            tool_trace=debug.tool_trace,
        )
        if grounding_issues:
            grounding_issue = grounding_issues[0]
            gen_result.response = grounding_correction_response(
                grounding_issues,
                tool_trace=debug.tool_trace,
            )
            debug.self_check_passed = False
            if grounding_issue.message not in debug.self_check_issues:
                debug.self_check_issues.append(grounding_issue.message)
            debug.correction_note = grounding_issue.message

        timings["self_check"] = (time.perf_counter() - _ts) * 1000
        debug.response = gen_result.response

        # ---- Step 15: Post-processing ----
        outcome_event = EmotionalEvent(
            event_type=EventType.USER_MESSAGE,
            intensity=0.1,
            source="self",
            metadata={"type": "response_delivered"},
        )
        self.short_term.record(outcome_event, self.engine.state)

        self.engine.drain_energy(intensity=event.intensity)
        debug.energy_after = self.engine.state.energy
        debug.emotion_label = self.engine.to_emotion_label()

        # Update conversation history
        self._conversation_history.append({"role": "user", "content": user_message})
        self._conversation_history.append({"role": "assistant", "content": gen_result.response})

        # Record interaction for person profile
        self.person_profiles.record_interaction(
            user_id,
            {"engagement": event.intensity},
            context=event.event_type.value,
        )

        # Update trust per-message based on event valence
        event_valence = self._event_valence(event)
        if event_valence != 0.0:
            self.person_profiles.update_trust(user_id, valence=event_valence)

        # ---- Self-observations (1-3 per turn) ----
        self._record_self_observations(
            event, detected, appraisal, defense, self.engine.state, gen_result.response,
        )

        # ---- Semantic memory writes ----
        salience_delta = self._record_semantic_memory(
            user_message=user_message,
            assistant_response=gen_result.response,
            user_id=user_id,
            topic=relationship_topic,
            event=event,
            life_influence=life_influence,
        )
        if salience_delta:
            debug.life_influence_effects["memory_salience_delta"] = salience_delta

        # ---- Persist defense event ----
        if defense:
            from core.types import DefenseEvent
            de = DefenseEvent(
                timestamp=time.time(),
                defense_type=defense.defense_type,
                raw_intensity=defense.raw_intensity,
                expressed_intensity=defense.expressed_intensity,
                suppression_delta=defense.suppression_delta,
            )
            self.self_profile.persist_defense_event(de)

        timings["total"] = (time.perf_counter() - _t_total) * 1000
        debug.stage_timings_ms = timings

        return PipelineResponse(response=gen_result.response, debug=debug)

    # ------------------------------------------------------------------
    # Proactive behavior (Phase 8)
    # ------------------------------------------------------------------

    def process_proactive(
        self,
        user_id: str = "default",
        *,
        max_proactive: int = 3,
        idle_threshold: float = 300.0,
        cooldown: float = 300.0,
    ) -> PipelineResponse | None:
        """Evaluate proactive triggers and generate response if warranted.

        Called by the runtime when a session has been idle. Response is
        generated through Nūr (defense + generator), not a hardcoded path.

        Returns PipelineResponse if proactive action was taken, None otherwise.
        The debug state always contains the ProactiveTrace for observability.
        """
        self._ensure_bound_user(user_id)
        debug = DebugState(user_message="[proactive]", user_id=user_id)
        person = self.person_profiles.get_or_create(user_id)
        self_prof = self.self_profile.get_profile()
        life_history_context = self._load_life_history_context()
        life_influence = derive_life_influence(life_history_context)
        debug.life_history_context = life_history_context
        debug.life_influence = life_influence

        # Elapsed decay — same as process() Step 0
        now = time.time()
        idle_seconds = now - self._last_turn_time if self._last_turn_time else 0.0
        if self._last_turn_time is not None:
            elapsed = now - self._last_turn_time
            if elapsed > 0:
                self.engine.decay(elapsed)
        self._last_turn_time = now

        # 1. Evaluate proactive triggers
        action, trace = evaluate_proactive(
            state=self.engine.state,
            unresolved_items=self.engine.active_unresolved(),
            active_plan=self._active_task_plan,
            person=person,
            idle_seconds=idle_seconds,
            proactive_count=self._proactive_count,
            last_proactive_at=self._last_proactive_at,
            max_proactive=max_proactive,
            idle_threshold=idle_threshold,
            cooldown=cooldown,
            life_influence=life_influence,
        )
        debug.proactive_trace = trace
        if trace.life_influence_score_deltas:
            debug.life_influence_effects["proactive_score_deltas"] = dict(trace.life_influence_score_deltas)
            debug.life_influence_effects["proactive_score_delta"] = round(
                sum(trace.life_influence_score_deltas.values()),
                6,
            )

        if action is None:
            return None

        # 2. For task continuation, run tool loop
        tool_context = ""
        if (
            action.action_type == "continue_task"
            and self._tool_executor is not None
            and self._active_task_plan is not None
        ):
            if self._tool_runner is not None:
                tool_loop_result = self._tool_runner.run_tool_loop(
                    user_message="continue",
                    state=self.engine.state,
                    person=person,
                    defense_active=False,
                    engine=self.engine,
                    active_plan=self._active_task_plan,
                    autonomy_level=self._autonomy_level,
                    life_influence=life_influence,
                )
            else:
                tool_loop_result = run_tool_loop(
                    user_message="continue",
                    state=self.engine.state,
                    person=person,
                    defense_active=False,
                    executor=self._tool_executor,
                    engine=self.engine,
                    active_plan=self._active_task_plan,
                    life_influence=life_influence,
                )
            tool_context = tool_loop_result.tool_context_summary
            debug.tool_trace = tool_loop_result.trace
            debug.action_variables = tool_loop_result.action_variables
            if tool_loop_result.life_influence_effects:
                debug.life_influence_effects.update(tool_loop_result.life_influence_effects)

            # Update plan state
            task_trace = tool_loop_result.trace.task_trace
            if task_trace and task_trace.plan:
                debug.task_trace = task_trace
                self._active_task_plan = task_trace.plan
                if task_trace.plan.is_terminal:
                    self._active_task_plan = None

        # 3. Defense filter (proactive messages go through defense unless disabled)
        if self._features.defense:
            filtered, defense = self.defense_mechanism.evaluate(
                inner_dialogue_output=action.message,
                modulator_state=self.engine.state,
                self_profile=self_prof,
                person_profile=person,
                topic_profiles=[],
            )
        else:
            filtered = action.message
            defense = None
        debug.defense_activation = defense

        defense_instruction = ""
        if defense:
            defense_instruction = DEFENSE_INSTRUCTIONS.get(defense.defense_type, "")

        # 4. Generate through Nūr
        proactive_semantic = self.semantic_memory.retrieve(
            action.message,
            source_person=user_id,
            limit=self._semantic_cfg.retrieval_limit,
        )
        debug.semantic_memories = proactive_semantic
        skill_context = self._load_skill_context()
        debug.skill_context = skill_context
        ctx = PipelineContext(
            modulator_snapshot=self.engine.snapshot(),
            soul_profile=self.soul,
            person_profile=person,
            self_profile=self_prof,
            relationship_context=None,
            semantic_memories=proactive_semantic,
            life_history_context=life_history_context,
            skill_context=skill_context,
            candidate_response=filtered,
            defense_instruction=defense_instruction,
            tool_context_summary=tool_context,
        )
        proactive_relationship = self.relationship_memory.build_context(user_id)
        if not proactive_relationship.is_empty():
            ctx.relationship_context = proactive_relationship
        gen_result = self.generator.generate(
            ctx, action.message, self._conversation_history,
        )
        debug.response = gen_result.response
        debug.modulator_snapshot = self.engine.snapshot()

        # 5. Update proactive tracking
        self._proactive_count += 1
        self._last_proactive_at = time.time()
        self._conversation_history.append({
            "role": "assistant", "content": gen_result.response,
        })

        # 6. Self-observation: proactive behavior recorded
        self.self_profile.record_behavior(
            "proactive", 0.6, f"proactive_{action.action_type}",
        )

        return PipelineResponse(response=gen_result.response, debug=debug)

    def _effective_self_expected_traits(self) -> dict[str, float]:
        """Seed identity provides the starting expectation; learned traits can override it."""
        expected = dict(self.soul.initial_traits)
        expected.update(self.self_profile.get_expected_traits())
        return expected

    def _load_life_history_context(self) -> dict[str, Any]:
        """Read compact identity-level life context for generation."""
        if not self._features.life_history_context:
            return {}
        if self._life_history_provider is None:
            return {}
        try:
            context = self._life_history_provider()
        except Exception as exc:
            log.warning("Life history context unavailable: %s", exc)
            return {"error": exc.__class__.__name__}
        return context if isinstance(context, dict) else {}

    def _load_skill_context(self) -> dict[str, Any]:
        """Read enabled imported skill guidance for generation."""
        if self._skill_provider is None:
            return {}
        try:
            context = self._skill_provider()
        except Exception as exc:
            log.warning("Skill context unavailable: %s", exc)
            return {"error": exc.__class__.__name__}
        return context if isinstance(context, dict) else {}

    def _record_semantic_memory(
        self,
        *,
        user_message: str,
        assistant_response: str,
        user_id: str,
        topic: str,
        event: EmotionalEvent,
        life_influence: LifeInfluence | None = None,
    ) -> float:
        """Persist semantic memories derived from the completed turn."""
        salience_delta = 0.0
        if life_influence is not None:
            raw_text = f"User: {user_message}\nAssistant: {assistant_response}"
            salience_delta = curiosity_salience_bonus(life_influence, raw_text)
        for entry in derive_semantic_entries(
            config=self._semantic_cfg,
            user_id=user_id,
            user_message=user_message,
            assistant_response=assistant_response,
            topic=topic,
            event_intensity=event.intensity,
            life_influence=life_influence,
        ):
            self.semantic_memory.store(entry)
        return round(salience_delta, 6)

    # ------------------------------------------------------------------
    # Self-observation recording (v2 — Phase 3)
    # ------------------------------------------------------------------

    def _record_self_observations(
        self,
        event: EmotionalEvent,
        detected: DetectedEmotion,
        appraisal: AppraisalFrame,
        defense: DefenseActivation | None,
        state: ModulatorState,
        response: str,
    ) -> None:
        """Record 1-3 behavioral self-observations after each turn."""
        observations: list[tuple[str, float, str]] = []  # (trait, value, context)

        # Blunt: high certainty + directive response
        if state.certainty > 0.7:
            observations.append(("blunt", state.certainty, "high_certainty"))

        # Empathetic: user distress that is not aimed at Nūr
        if detected.valence < 0.3 and not appraisal.targets_assistant:
            observations.append(("empathetic", 0.6, "negative_user_emotion"))

        # Defensive: defense mechanism fired
        if defense is not None:
            observations.append(("defensive", defense.raw_intensity, defense.defense_type))

        # Avoidant: avoidance topic active and energy low
        if state.energy < 0.3:
            observations.append(("avoidant", 0.5, "low_energy"))

        # Cap at 3 observations per turn
        for trait, value, context in observations[:3]:
            self.self_profile.record_behavior(trait, value, context)

    # ------------------------------------------------------------------
    # Self-check gating
    # ------------------------------------------------------------------

    @staticmethod
    def _should_use_llm_self_check(
        event: EmotionalEvent,
        contradiction_flags: list[str],
        dialogue_trace: InnerDialogueTrace | None,
    ) -> bool:
        """Use the LLM self-check only when the turn truly warrants it."""
        if event.intensity > 0.85:
            return True
        if contradiction_flags:
            return True
        if dialogue_trace is not None and dialogue_trace.reached_deadlock:
            return True
        return False

    # ------------------------------------------------------------------
    # Resolution source detection (v2)
    # ------------------------------------------------------------------

    def _check_resolution_sources(
        self, event: EmotionalEvent, text: str, user_id: str,
    ) -> None:
        """Check if the current event should create or resolve unresolved items."""
        from datetime import datetime, timezone
        from core.types import UnresolvedItem
        import uuid

        # Spike not processed → unresolved (resolution events are healing, not tension)
        if event.intensity >= SPIKE_INTENSITY_THRESHOLD and event.event_type != EventType.RESOLUTION:
            self.engine.add_unresolved(UnresolvedItem(
                id=f"spike_{uuid.uuid4().hex[:8]}",
                source="spike",
                description=f"Emotional spike: {event.event_type.value} (intensity {event.intensity:.2f})",
                created_at=datetime.now(timezone.utc),
                intensity=min(0.9, event.intensity * 0.8),
                decay_rate=0.03,
            ))

        # Resolution events resolve matching items (or oldest if no match)
        if event.event_type == EventType.RESOLUTION:
            active = self.engine.active_unresolved()
            if active:
                # Try to match by text overlap with description
                lower = text.lower()
                matched = None
                for item in active:
                    if any(word in lower for word in item.description.lower().split()
                           if len(word) > 3):
                        matched = item
                        break
                self.engine.resolve_item((matched or active[0]).id)

    def _check_contradiction_resolution(
        self, contradiction_flags: list[str], user_id: str,
    ) -> None:
        """Wire contradictions as unresolved items (deduplicated)."""
        from datetime import datetime, timezone
        import uuid

        active_descs = {i.description for i in self.engine.active_unresolved()}
        for flag in contradiction_flags:
            desc = flag[:120]
            if desc not in active_descs:
                self.engine.add_unresolved(UnresolvedItem(
                    id=f"contradiction_{uuid.uuid4().hex[:8]}",
                    source="contradiction",
                    description=desc,
                    created_at=datetime.now(timezone.utc),
                    intensity=0.5,
                    decay_rate=0.02,
                ))
                active_descs.add(desc)

    def _check_topic_resolution(
        self, topics: list[TopicProfile],
    ) -> None:
        """Wire avoidance/charged topics as unresolved items."""
        from datetime import datetime, timezone
        import uuid

        for tp in topics:
            if tp.avoidance or tp.emotional_charge >= 0.5:
                # Don't add duplicate for same topic
                active_descs = {i.description for i in self.engine.active_unresolved()}
                desc = f"Charged topic: {tp.topic} (charge={tp.emotional_charge:.2f})"
                if desc not in active_descs:
                    self.engine.add_unresolved(UnresolvedItem(
                        id=f"topic_{uuid.uuid4().hex[:8]}",
                        source="topic",
                        description=desc,
                        created_at=datetime.now(timezone.utc),
                        intensity=min(0.7, tp.emotional_charge),
                        decay_rate=0.05,
                    ))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close all database connections held by this pipeline.

        Call this when the pipeline is no longer needed (session eviction,
        shutdown). Safe to call multiple times. Also releases HTTP session
        pools held by the LLM backend and tool executor providers if they
        expose ``close()``.
        """
        self.long_term.close()
        self.relationship_memory.close()
        self.semantic_memory.close()
        self._person_profile_store.close()
        self._self_profile_store.close()
        self.person_profiles.close()
        self.topic_profiles.close()
        # Release HTTP connection pools on LLM backends (safe for backends
        # without a close method, e.g. MockLLMBackend). Avoid double-close
        # when fast and primary share the same instance.
        seen: set[int] = set()
        for backend in (self._llm_backend, self._llm_backend_fast):
            if backend is None or id(backend) in seen:
                continue
            seen.add(id(backend))
            close = getattr(backend, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # pragma: no cover — defensive
                    pass
        # Release HTTP connection pools on tool providers registered via the
        # ToolExecutor (e.g., RequestsWebProvider). The executor exposes any
        # owned resources via ``_owned_resources``.
        if self._tool_executor is not None:
            for resource in getattr(self._tool_executor, "_owned_resources", []):
                close = getattr(resource, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # pragma: no cover — defensive
                        pass

    def export_conversation_history(self) -> list[dict[str, str]]:
        """Return the hot in-session transcript for runtime persistence."""
        return [
            {"role": item["role"], "content": item["content"]}
            for item in self._conversation_history
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]

    def restore_conversation_history(self, history: list[dict[str, str]]) -> None:
        """Restore a hot transcript saved by the runtime session manager."""
        self._conversation_history = [
            {"role": str(item["role"]), "content": str(item["content"])}
            for item in history
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]

    def restore_state(
        self,
        snapshot: dict[str, float],
        saved_at: float | None = None,
        unresolved_items: list[UnresolvedItem] | None = None,
    ) -> None:
        """Restore emotional engine state from a persisted snapshot.

        Convenience wrapper around EmotionalEngine.restore() for runtime use.
        """
        self.engine.restore(
            snapshot,
            saved_at=saved_at,
            unresolved_items=unresolved_items,
        )

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def end_session(self, user_id: str = "default") -> DigestedSession:
        """End a session: run digestion, clear short-term, apply energy drain."""
        self._ensure_bound_user(user_id)
        result = digest_session(
            self.short_term,
            self.long_term,
            source_person=user_id,
            relationship_memory=self.relationship_memory,
            llm_client=self._llm_backend,
            conversation_history=self._conversation_history,
            spikes_already_stored=True,
        )

        # Trust is updated per-turn (in process()), not again at session end.
        # The digested trust_delta is informational only.

        # Apply energy drain
        self.engine.state.energy = max(
            0.0, self.engine.state.energy - result.energy_drain
        )

        # Clear conversation history and session-scoped state
        self._conversation_history.clear()
        self._active_task_plan = None
        self._proactive_count = 0
        self._last_proactive_at = None

        return result

    def apply_rest(self, hours: float) -> None:
        """Simulate time passing between sessions. Recovers energy, decays modulators."""
        seconds = hours * 3600.0
        self.engine.decay(seconds)

    def _ensure_bound_user(self, user_id: str) -> None:
        """Bind the pipeline to a single relational user for its lifetime."""
        if self._bound_user_id is None:
            self._bound_user_id = user_id
            return
        if user_id != self._bound_user_id:
            raise RuntimeError(
                "CognitivePipeline instances are single-user/session scoped. "
                f"Bound to '{self._bound_user_id}', got '{user_id}'. "
                "Create a separate pipeline per user."
            )

    # ------------------------------------------------------------------
    # Event classification (LLM with rule-based fallback)
    # ------------------------------------------------------------------

    def _classify_event(
        self,
        text: str,
        detected: DetectedEmotion,
        appraisal: AppraisalFrame,
    ) -> EmotionalEvent:
        """Classify user message into an EmotionalEvent.

        Always uses rule-based heuristics (0 LLM calls).
        LLM classification available via _classify_event_via_llm() if needed.
        """
        return self._classify_event_via_rules(text, detected, appraisal)

    def _classify_event_via_llm(
        self,
        text: str,
        detected: DetectedEmotion,
        appraisal: AppraisalFrame,
    ) -> EmotionalEvent | None:
        """LLM-based event classification. Returns None on failure."""
        prompt_template = get_config().classify_event_prompt
        if not prompt_template:
            return None

        prompt = prompt_template.replace("{arousal}", f"{detected.arousal:.2f}")
        prompt = prompt.replace("{valence}", f"{detected.valence:.2f}")
        prompt = prompt.replace("{certainty}", f"{detected.certainty:.2f}")
        prompt = prompt.replace("{intensity}", f"{detected.intensity:.2f}")

        try:
            raw = self._llm_backend.generate(prompt, text)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)

            event_type_str = data.get("event_type", "user_message")
            intensity = float(data.get("intensity", 0.3))

            # Validate event type
            try:
                event_type = EventType(event_type_str)
            except ValueError:
                return None

            return EmotionalEvent(
                event_type=event_type,
                intensity=max(0.0, min(1.0, intensity)),
                source="user",
                metadata=self._appraisal_metadata(appraisal),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def _classify_event_via_rules(
        self,
        text: str,
        detected: DetectedEmotion,
        appraisal: AppraisalFrame,
    ) -> EmotionalEvent:
        """Rule-based fallback for event classification."""
        lower = text.lower()
        metadata = self._appraisal_metadata(appraisal)

        if appraisal.social_move == "apology":
            return EmotionalEvent(
                event_type=EventType.RESOLUTION,
                intensity=max(0.4, appraisal.affiliation_bid),
                source="user",
                metadata=metadata,
            )

        if appraisal.social_move == "gratitude":
            return EmotionalEvent(
                event_type=EventType.POSITIVE_FEEDBACK,
                intensity=max(0.3, detected.valence, appraisal.affiliation_bid * 0.7),
                source="user",
                metadata=metadata,
            )

        if appraisal.social_move == "connection" and appraisal.targets_assistant:
            return EmotionalEvent(
                event_type=EventType.WARMTH,
                intensity=max(0.3, appraisal.affiliation_bid * 0.6, detected.valence),
                source="user",
                metadata=metadata,
            )

        # Betrayal / deception only counts as relational betrayal when aimed at Nūr.
        if appraisal.targets_assistant and any(w in lower for w in ["betray", "lied", "deceived", "cheated"]):
            return EmotionalEvent(
                event_type=EventType.BETRAYAL,
                intensity=max(0.7, 1.0 - detected.valence),
                source="user",
                metadata=metadata,
            )

        # Direct conflict with Nūr, not general distress elsewhere.
        if appraisal.targets_assistant and any(
            w in lower
            for w in [
                "angry",
                "furious",
                "hate",
                "fight",
                "argument",
                "upset with you",
                "upset at you",
                "shut up",
                "go away",
                "screw you",
                "piss off",
                "get lost",
                "leave me alone",
            ]
        ):
            return EmotionalEvent(
                event_type=EventType.CONFLICT,
                intensity=max(0.5, detected.arousal),
                source="user",
                metadata=metadata,
            )

        # Appraisal may identify a direct attack even when the contagion lexicon
        # misses the exact phrasing (for example, "I'm upset with you").
        if appraisal.targets_assistant and appraisal.social_move == "attack":
            return EmotionalEvent(
                event_type=EventType.NEGATIVE_FEEDBACK,
                intensity=max(0.45, detected.arousal, 1.0 - detected.valence),
                source="user",
                metadata=metadata,
            )

        # Insults, hostility, profanity
        _insult_words = [
            "stupid", "idiot", "moron", "dumb", "pathetic", "useless",
            "worthless", "incompetent", "fool", "clueless",
        ]
        _hostile_phrases = [
            "shut up", "go away", "screw you", "piss off", "get lost",
            "hate you", "leave me alone",
        ]
        _profanity = ["fuck", "shit", "bullshit", "damn", "asshole", "bastard", "bitch", "crap", "suck", "sucks"]

        if appraisal.targets_assistant and any(w in lower for w in _insult_words):
            return EmotionalEvent(
                event_type=EventType.NEGATIVE_FEEDBACK,
                intensity=max(0.6, detected.arousal),
                source="user",
                metadata=metadata,
            )
        if appraisal.targets_assistant and any(p in lower for p in _hostile_phrases):
            return EmotionalEvent(
                event_type=EventType.CONFLICT,
                intensity=max(0.7, detected.arousal),
                source="user",
                metadata=metadata,
            )
        if appraisal.targets_assistant and any(w in lower for w in _profanity):
            return EmotionalEvent(
                event_type=EventType.NEGATIVE_FEEDBACK,
                intensity=max(0.5, detected.arousal),
                source="user",
                metadata=metadata,
            )

        # Positive feedback remains relational only when directed at Nūr.
        _positive_words = [
            "thank", "grateful", "appreciate", "love", "great job",
            "wonderful", "amazing", "awesome", "fantastic", "excellent",
            "brilliant", "outstanding", "incredible", "superb", "perfect",
            "beautiful", "impressive", "magnificent",
        ]
        if appraisal.targets_assistant and any(w in lower for w in _positive_words):
            return EmotionalEvent(
                event_type=EventType.POSITIVE_FEEDBACK,
                intensity=max(0.3, detected.valence),
                source="user",
                metadata=metadata,
            )

        # General negativity should only damage the relationship when aimed at Nūr.
        if appraisal.targets_assistant and any(w in lower for w in ["wrong", "bad", "terrible", "awful", "disappointed"]):
            return EmotionalEvent(
                event_type=EventType.NEGATIVE_FEEDBACK,
                intensity=max(0.4, 1.0 - detected.valence),
                source="user",
                metadata=metadata,
            )

        if any(w in lower for w in ["surprise", "unexpected", "wow", "shock"]):
            return EmotionalEvent(
                event_type=EventType.SURPRISE,
                intensity=max(0.4, detected.arousal),
                source="user",
                metadata=metadata,
            )

        if appraisal.targets_assistant and any(w in lower for w in ["warm", "kind", "sweet", "care", "hug"]):
            return EmotionalEvent(
                event_type=EventType.WARMTH,
                intensity=max(0.3, detected.valence),
                source="user",
                metadata=metadata,
            )

        # Strong negative emotion can still matter without being relational harm.
        if appraisal.targets_assistant and detected.valence < 0.25 and detected.arousal > 0.6:
            return EmotionalEvent(
                event_type=EventType.NEGATIVE_FEEDBACK,
                intensity=detected.arousal,
                source="user",
                metadata=metadata,
            )

        if appraisal.targets_assistant and detected.valence > 0.75:
            return EmotionalEvent(
                event_type=EventType.POSITIVE_FEEDBACK,
                intensity=detected.valence,
                source="user",
                metadata=metadata,
            )

        # Non-directed strongly positive emotion — shared joy still lifts mood.
        # Half weight vs assistant-targeted positive feedback.
        if not appraisal.targets_assistant and detected.valence > 0.65 and detected.arousal > 0.5:
            return EmotionalEvent(
                event_type=EventType.POSITIVE_FEEDBACK,
                intensity=detected.valence * 0.85,
                source="user",
                metadata=metadata,
            )

        # Default: regular user message. This preserves emotional contagion for
        # external distress without treating it as relational damage.
        # Strongly charged messages carry more weight — someone arriving
        # extremely agitated or excited should still move the needle.
        intensity = max(
            abs(detected.arousal - 0.5),
            abs(detected.valence - 0.5),
        )
        if detected.arousal > 0.7 or abs(detected.valence - 0.5) > 0.25:
            intensity = max(intensity, 0.6)
        return EmotionalEvent(
            event_type=EventType.USER_MESSAGE,
            intensity=max(0.1, min(1.0, intensity)),
            source="user",
            metadata=metadata,
        )

    @staticmethod
    def _appraisal_metadata(appraisal: AppraisalFrame) -> dict[str, Any]:
        """Attach compact appraisal state to events for downstream use."""
        return {
            "primary_target": appraisal.primary_target,
            "social_move": appraisal.social_move,
            "inferred_intent": appraisal.inferred_intent,
            "targets_assistant": appraisal.targets_assistant,
            "blame": appraisal.blame,
            "controllability": appraisal.controllability,
            "expectation_violation": appraisal.expectation_violation,
            "vulnerability": appraisal.vulnerability,
            "affiliation_bid": appraisal.affiliation_bid,
            "mixed_affect": appraisal.mixed_affect,
        }

    def _detect_topics(self, text: str) -> list[TopicProfile]:
        """Detect active topics via substring matching (0 LLM calls).

        LLM detection available via _detect_topics_via_llm() if needed.
        """
        all_topic_profiles = self.topic_profiles.all_profiles()
        if not all_topic_profiles:
            return []

        topics = []
        lower = text.lower()
        for tp in all_topic_profiles:
            if tp.topic.lower() in lower:
                topics.append(tp)
        return topics

    def _detect_topics_via_llm(
        self, text: str, all_profiles: list[TopicProfile]
    ) -> list[TopicProfile] | None:
        """LLM-based topic detection. Returns None on failure."""
        prompt_template = get_config().detect_topics_prompt
        if not prompt_template:
            return None

        known_list = ", ".join(tp.topic for tp in all_profiles)
        prompt = prompt_template.replace("{known_topics}", known_list)

        try:
            raw = self._llm_backend.generate(prompt, text)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)

            matched_names = set(t.lower() for t in data.get("topics", []))
            return [tp for tp in all_profiles if tp.topic.lower() in matched_names]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _event_valence(event: EmotionalEvent) -> float:
        """Map event to signed valence for trust math."""
        positive = {EventType.POSITIVE_FEEDBACK, EventType.RESOLUTION, EventType.WARMTH}
        negative = {EventType.NEGATIVE_FEEDBACK, EventType.CONFLICT, EventType.BETRAYAL}
        targets_assistant = bool(event.metadata.get("targets_assistant"))
        if event.event_type in positive:
            if not targets_assistant:
                return 0.0
            return event.intensity
        elif event.event_type in negative:
            if not targets_assistant:
                return 0.0
            return -event.intensity
        return 0.0
