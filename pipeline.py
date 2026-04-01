"""Cognitive pipeline orchestrator.

v2 processing flow:
 1. Anticipation: predict emotional trajectory (0 LLM calls)
 2. Contagion: detect user tone → bounded mirror (0 LLM calls — rule-based)
 3. Context switch: load person profile baseline_shift
 4. Event classification: categorize input (0 LLM calls — rule-based)
 5. PSI engine: update 6 modulators from input + drives + energy
 6. Resolution update: check for new/resolved tension items (0 LLM calls)
 7. Short-term memory: store emotional reaction
 8. Spike check: if intensity > 0.8 → heavy write to LT
 9. Memory retrieval: ACT-R activation biased by current state
10. Profile lookup: person + self + topic
11. Contradiction check: compare against profiles (self + others)
12. Inner dialogue: 2-3 round fast/slow deliberation (2-5 LLM calls)
13. Defense mechanisms: filter output if needed (0 LLM calls)
14. Master LLM: generate final response (1 LLM call)
15. Self-check: rule-based default; LLM only when intensity > 0.7 (0-1 LLM calls)
16. Post-processing: update memory, drain energy
17. [Session end] Digestion (0-1 LLM call)

LLM call budget: 1-6 per message (typical: 1). Inner dialogue only fires when
non-spike unresolved items exist (contradictions, deadlocks). Spike residue alone
does not trigger extra calls on follow-up turns.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from config.loader import get_config
from core.types import (
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
    SelfProfile,
    TopicProfile,
    UnresolvedItem,
    ValueHierarchy,
)
from core.emotional_engine import EmotionalEngine, SPIKE_INTENSITY_THRESHOLD
from core.memory.short_term import ShortTermMemory
from core.memory.long_term import LongTermMemory
from core.memory.digestion import digest_session, DigestedSession
from core.contagion import detect_emotion
from core.profiles.base import ProfileStore
from core.profiles.person import PersonProfileManager
from core.profiles.self_model import SelfProfileManager, SELF_ENTITY_ID
from core.profiles.topic import TopicProfileManager
from core.profiles.contradiction import ContradictionDetector
from core.dual_process.generator import (
    GenerationResult,
    LLMBackend,
    MockLLMBackend,
    ResponseGenerator,
)
from core.dual_process.self_check import SelfChecker
from core.dual_process.inner_dialogue import InnerDialogue
from core.anticipation import AnticipationEngine
from core.defense_mechanisms import DEFENSE_INSTRUCTIONS, DefenseMechanism


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

    # Step 3: Context switch
    baseline_shift_applied: dict[str, float] = field(default_factory=dict)

    # Step 4: Modulator update
    event_classified: str = ""
    event_intensity: float = 0.0
    is_spike: bool = False
    modulator_snapshot: dict[str, float] = field(default_factory=dict)

    # Step 7: Memory retrieval
    retrieved_memories: list[LongTermEntry] = field(default_factory=list)

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

    # Timing instrumentation (ms)
    stage_timings_ms: dict[str, float] = field(default_factory=dict)


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
    ) -> None:
        # Primary backend (used if no fast backend provided)
        self._llm_backend = llm_backend or MockLLMBackend()
        # Fast backend (thinking mode off) — used for ALL calls
        # Generator prompt already has full context; thinking overhead not needed
        self._llm_backend_fast = llm_backend_fast or self._llm_backend

        # Core engine
        self.engine = EmotionalEngine()

        # Memory
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory(db_path=db_path)

        # Profiles (shared store)
        self.profile_store = ProfileStore(db_path=db_path)
        self.person_profiles = PersonProfileManager(self.profile_store, db_path=db_path)
        self.self_profile = SelfProfileManager(self.profile_store)
        self.topic_profiles = TopicProfileManager(db_path=db_path)
        self.contradiction_detector = ContradictionDetector(self.profile_store)

        # Master generator uses fast backend (thinking off — prompt has full context)
        self.generator = ResponseGenerator(backend=self._llm_backend_fast)
        # Self-checker: rule-based by default; LLM only for high-intensity turns
        self.self_checker = SelfChecker(llm_client=None)
        self._self_check_llm = SelfChecker(llm_client=self._llm_backend_fast)

        # Inner dialogue uses fast backend (no thinking needed for gut reaction + evaluation)
        self.inner_dialogue = InnerDialogue(backend=self._llm_backend_fast)
        self.anticipation_engine = AnticipationEngine()
        self.defense_mechanism = DefenseMechanism()

        # Values (static in v1)
        self.values = ValueHierarchy()

        # Conversation history for context
        self._conversation_history: list[dict[str, str]] = []

        # Time tracking for auto-decay between turns
        self._last_turn_time: float | None = None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(self, user_message: str, user_id: str = "default") -> PipelineResponse:
        """Process a user message through the full v2 cognitive pipeline.

        Flow: anticipation → contagion → context → event → resolution →
              memory → profiles → contradiction → inner dialogue →
              defense → master LLM → post-processing.

        Returns the response text and full debug state.
        """
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

        # ---- Step 3: Context switch (non-additive — sets resting target) ----
        shift = self.person_profiles.get_baseline_shift(user_id)
        self.engine.set_context_shift(shift)
        debug.baseline_shift_applied = shift.to_dict()

        # ---- Step 4: Event classification + PSI engine update ----
        _ts = time.perf_counter()
        event = self._classify_event(user_message, detected)
        is_spike = self.engine.update(event)
        debug.event_classified = event.event_type.value
        debug.event_intensity = event.intensity
        debug.is_spike = is_spike
        timings["event_classification"] = (time.perf_counter() - _ts) * 1000

        # ---- Step 5: Resolution update (0 LLM calls) ----
        # Check if this event creates new unresolved items
        self._check_resolution_sources(event, user_message, user_id)
        active_unresolved = self.engine.active_unresolved()
        debug.unresolved_count = len(active_unresolved)
        debug.unresolved_items = list(active_unresolved)

        debug.modulator_snapshot = self.engine.snapshot()

        # ---- Step 6: Short-term memory ----
        self.short_term.record(event, self.engine.state)

        # ---- Step 7: Spike check ----
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

        # ---- Step 8: Memory retrieval ----
        _ts = time.perf_counter()
        retrieved = self.long_term.retrieve(
            self.engine.state,
            source_person=user_id,
            limit=5,
        )
        debug.retrieved_memories = retrieved
        timings["memory_retrieval"] = (time.perf_counter() - _ts) * 1000

        # ---- Step 9: Profile lookup ----
        person = self.person_profiles.get_or_create(user_id)
        self_prof = self.self_profile.get_profile()
        active_topics = self._detect_topics(user_message)
        debug.person_profile = person
        debug.self_profile = self_prof
        debug.topic_profiles = active_topics

        # ---- Step 10: Contradiction check ----
        contradiction_flags: list[str] = []

        person_expected = self.person_profiles.get_expected_traits(user_id)
        if person_expected:
            person_result = self.contradiction_detector.detect(user_id, person_expected)
            for c in person_result.contradictions:
                contradiction_flags.append(c.description)

        self_expected = self.self_profile.get_expected_traits()
        if self_expected:
            self_result = self.contradiction_detector.detect(SELF_ENTITY_ID, self_expected)
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

        # ---- Step 11: INNER DIALOGUE (0-5 LLM calls; 0 for calm) ----
        _ts = time.perf_counter()
        contagion_summary = (
            f"arousal={detected.arousal:.2f}, valence={detected.valence:.2f}, "
            f"intensity={detected.intensity:.2f}"
        )
        short_term_summary = f"{len(self.short_term)} entries in short-term memory"

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
        debug.dialogue_trace = dialogue_trace
        timings["inner_dialogue"] = (time.perf_counter() - _ts) * 1000

        # If deadlock, feed it to resolution modulator
        deadlock_item = self.inner_dialogue.create_deadlock_item(dialogue_trace)
        if deadlock_item:
            self.engine.add_unresolved(deadlock_item)
            active_unresolved = self.engine.active_unresolved()
            debug.unresolved_count = len(active_unresolved)
            debug.unresolved_items = list(active_unresolved)

        # ---- Step 12: DEFENSE MECHANISMS (0 LLM calls) ----
        filtered_output, defense = self.defense_mechanism.evaluate(
            inner_dialogue_output=dialogue_trace.final_candidate,
            modulator_state=self.engine.state,
            self_profile=self_prof,
            person_profile=person,
            topic_profiles=active_topics,
        )
        debug.defense_activation = defense

        # ---- Step 13: MASTER LLM (1 LLM call) ----
        _ts = time.perf_counter()
        defense_instruction = ""
        if defense:
            defense_instruction = DEFENSE_INSTRUCTIONS.get(defense.defense_type, "")

        ctx = PipelineContext(
            modulator_snapshot=self.engine.snapshot(),
            person_profile=person,
            self_profile=self_prof,
            topic_profiles=active_topics,
            values=self.values,
            retrieved_memories=retrieved,
            short_term_history=self.short_term.recent(5),
            contradiction_flags=contradiction_flags,
            contagion=detected,
            candidate_response=filtered_output,
            defense_instruction=defense_instruction,
        )

        gen_result = self.generator.generate(
            ctx, user_message, self._conversation_history
        )
        debug.generation_attempts = 1
        timings["generator"] = (time.perf_counter() - _ts) * 1000

        # ---- Self-check (rule-based default; LLM only when warranted) ----
        _ts = time.perf_counter()
        checker = (
            self._self_check_llm
            if self._should_use_llm_self_check(
                event, contradiction_flags, dialogue_trace, defense,
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
                person_profile=ctx.person_profile,
                self_profile=ctx.self_profile,
                topic_profiles=ctx.topic_profiles,
                values=ctx.values,
                retrieved_memories=ctx.retrieved_memories,
                short_term_history=ctx.short_term_history,
                contradiction_flags=ctx.contradiction_flags,
                contagion=ctx.contagion,
                candidate_response=correction_candidate,
                defense_instruction=ctx.defense_instruction,
            )
            gen_result = self.generator.generate(
                correction_ctx,
                user_message,
                self._conversation_history,
            )
            gen_result.correction_note = check_result.correction_note
            debug.correction_note = check_result.correction_note
            debug.generation_attempts = 2

        timings["self_check"] = (time.perf_counter() - _ts) * 1000
        debug.response = gen_result.response

        # ---- Step 14: Post-processing ----
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
            event, detected, defense, self.engine.state, gen_result.response,
        )

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
    # Self-observation recording (v2 — Phase 3)
    # ------------------------------------------------------------------

    def _record_self_observations(
        self,
        event: EmotionalEvent,
        detected: DetectedEmotion,
        defense: DefenseActivation | None,
        state: ModulatorState,
        response: str,
    ) -> None:
        """Record 1-3 behavioral self-observations after each turn."""
        observations: list[tuple[str, float, str]] = []  # (trait, value, context)

        # Blunt: high certainty + directive response
        if state.certainty > 0.7:
            observations.append(("blunt", state.certainty, "high_certainty"))

        # Empathetic: negative user emotion acknowledged
        if detected.valence < 0.3:
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
        defense: DefenseActivation | None,
    ) -> bool:
        """Use the LLM self-check only when the turn truly warrants it."""
        if event.intensity > 0.85:
            return True
        if contradiction_flags:
            return True
        if dialogue_trace is not None and dialogue_trace.reached_deadlock:
            return True
        if defense is not None:
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

        # Spike not processed → unresolved
        if event.intensity >= SPIKE_INTENSITY_THRESHOLD:
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
    # Session management
    # ------------------------------------------------------------------

    def end_session(self, user_id: str = "default") -> DigestedSession:
        """End a session: run digestion, clear short-term, apply energy drain."""
        result = digest_session(
            self.short_term,
            self.long_term,
            source_person=user_id,
            llm_client=self._llm_backend,
            conversation_history=self._conversation_history,
        )

        # Trust is updated per-turn (in process()), not again at session end.
        # The digested trust_delta is informational only.

        # Apply energy drain
        self.engine.state.energy = max(
            0.0, self.engine.state.energy - result.energy_drain
        )

        # Clear conversation history
        self._conversation_history.clear()

        return result

    def apply_rest(self, hours: float) -> None:
        """Simulate time passing between sessions. Recovers energy, decays modulators."""
        seconds = hours * 3600.0
        self.engine.decay(seconds)

    # ------------------------------------------------------------------
    # Event classification (LLM with rule-based fallback)
    # ------------------------------------------------------------------

    def _classify_event(
        self, text: str, detected: DetectedEmotion
    ) -> EmotionalEvent:
        """Classify user message into an EmotionalEvent.

        Always uses rule-based heuristics (0 LLM calls).
        LLM classification available via _classify_event_via_llm() if needed.
        """
        return self._classify_event_via_rules(text, detected)

    def _classify_event_via_llm(
        self, text: str, detected: DetectedEmotion
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
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def _classify_event_via_rules(
        self, text: str, detected: DetectedEmotion
    ) -> EmotionalEvent:
        """Rule-based fallback for event classification."""
        lower = text.lower()

        # Betrayal / deception keywords
        if any(w in lower for w in ["betray", "lied", "deceived", "cheated"]):
            return EmotionalEvent(
                event_type=EventType.BETRAYAL,
                intensity=max(0.7, 1.0 - detected.valence),
                source="user",
            )

        # Conflict / anger keywords
        if any(w in lower for w in ["angry", "furious", "hate", "fight", "argument"]):
            return EmotionalEvent(
                event_type=EventType.CONFLICT,
                intensity=max(0.5, detected.arousal),
                source="user",
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

        if any(w in lower for w in _insult_words):
            return EmotionalEvent(
                event_type=EventType.NEGATIVE_FEEDBACK,
                intensity=max(0.6, detected.arousal),
                source="user",
            )
        if any(p in lower for p in _hostile_phrases):
            return EmotionalEvent(
                event_type=EventType.CONFLICT,
                intensity=max(0.7, detected.arousal),
                source="user",
            )
        if any(w in lower for w in _profanity):
            return EmotionalEvent(
                event_type=EventType.NEGATIVE_FEEDBACK,
                intensity=max(0.5, detected.arousal),
                source="user",
            )

        # Resolution / apology keywords
        if any(w in lower for w in ["sorry", "apologize", "resolved", "forgive", "peace"]):
            return EmotionalEvent(
                event_type=EventType.RESOLUTION,
                intensity=0.6,
                source="user",
            )

        # Positive feedback keywords
        _positive_words = [
            "thank", "grateful", "appreciate", "love", "great job",
            "wonderful", "amazing", "awesome", "fantastic", "excellent",
            "brilliant", "outstanding", "incredible", "superb", "perfect",
            "beautiful", "impressive", "magnificent",
        ]
        if any(w in lower for w in _positive_words):
            return EmotionalEvent(
                event_type=EventType.POSITIVE_FEEDBACK,
                intensity=max(0.3, detected.valence),
                source="user",
            )

        # General negativity keywords
        if any(w in lower for w in ["wrong", "bad", "terrible", "awful", "disappointed"]):
            return EmotionalEvent(
                event_type=EventType.NEGATIVE_FEEDBACK,
                intensity=max(0.4, 1.0 - detected.valence),
                source="user",
            )

        if any(w in lower for w in ["surprise", "unexpected", "wow", "shock"]):
            return EmotionalEvent(
                event_type=EventType.SURPRISE,
                intensity=max(0.4, detected.arousal),
                source="user",
            )

        if any(w in lower for w in ["warm", "kind", "sweet", "care", "hug"]):
            return EmotionalEvent(
                event_type=EventType.WARMTH,
                intensity=max(0.3, detected.valence),
                source="user",
            )

        # Contagion-driven fallback
        if detected.valence < 0.25 and detected.arousal > 0.6:
            return EmotionalEvent(
                event_type=EventType.NEGATIVE_FEEDBACK,
                intensity=detected.arousal,
                source="user",
            )

        if detected.valence > 0.75:
            return EmotionalEvent(
                event_type=EventType.POSITIVE_FEEDBACK,
                intensity=detected.valence,
                source="user",
            )

        # Default: regular user message
        intensity = max(abs(detected.arousal - 0.5), abs(detected.valence - 0.5))
        return EmotionalEvent(
            event_type=EventType.USER_MESSAGE,
            intensity=max(0.1, min(1.0, intensity)),
            source="user",
        )

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
        if event.event_type in positive:
            return event.intensity
        elif event.event_type in negative:
            return -event.intensity
        return 0.0
