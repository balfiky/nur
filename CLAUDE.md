# Project Nur

Read PROJECT_NUR_ARCHITECTURE.md for the full vision.
Read PROJECT_NUR_BUILD_PLAN.md for the v1/v2 roadmap.
Read README.md for setup, usage, API reference, and module documentation.
Read CHANGELOG.md for version history and what changed when.

## Status: v2 COMPLETE + RUNTIME PHASE 3

v1 (288) + v2 (188) + regression (28) + Phase 0 (26) + Runtime Phase 1 (21) + Telegram (26) + Phase 3 (20) = 602 tests.

### What's built (v1)
- core/types.py — All shared type contracts (v1 + v2 types)
- core/emotional_engine.py — 6-modulator PSI state machine (arousal, valence, certainty, bonding, energy, resolution)
- core/memory/ — Short-term buffer + SQLite long-term (ACT-R retrieval) + session digestion
- core/profiles/ — Person + self + topic profiles + contradiction detection (unified mechanism)
- core/contagion.py — Emotional detection (LLM + rule-based fallback)
- core/dual_process/ — Response generation + self-check + inner dialogue (iterative fast/slow deliberation)
- core/llm_client.py — MiniMax M2.7-highspeed API client
- core/anticipation.py — Forward emotional modeling (pure heuristics, 0 LLM calls)
- core/defense_mechanisms.py — Defense filter (pure logic + prompt injection, 0 LLM calls)
- pipeline.py — Full v2 cognitive pipeline orchestrator
- interface/ — FastAPI + WebSocket + debug dashboard
- config/ — YAML configs + 10 prompt templates
- core/schema.py — Schema version management for all SQLite databases
- runtime/ — Jarvis Runtime: session manager, console + Telegram channels, state persistence, LLM backend factory
- main.py — Runtime entry point (`python main.py`)
- tests/ — 582 tests including calibration, journey, v2, regression, Phase 0, runtime, and Telegram tests

### What's NOT built (future features)
- Dynamic value drift (v2.5)
- Attachment style unlocking (v2.6)
- Deep periodic self-reflection (v2.7)
- Growth milestone tracking (v2.8)

## Rules
- Every module is independent with clear inputs/outputs
- Shared types live in core/types.py — all modules import from there
- Pure math where possible, LLM calls only where necessary
- SQLite for persistence, no external DB servers
- Every module gets its own tests
- Python 3.10+, FastAPI for web, YAML for config
- LLM functions always have rule-based fallback (graceful degradation)
- Contagion, event classification, topic detection: always rule-based (0 LLM calls)
- Self-check: rule-based by default; LLM only when intensity > 0.85, contradictions, or dialogue deadlock
- Inner dialogue: skipped unless non-spike unresolved items exist AND resolution > 0.6
- Spike-only turns and calm follow-ups after spikes stay on the 1-call generator path
- LLMClientFast: thinking mode disabled — used for ALL calls (generator, inner dialogue, self-check)
- LLMClient uses requests.Session for connection reuse
- Config-driven constants — no hardcoded thresholds in module code
- Start exaggerated emotional effects, dampen later during calibration

## Key patterns
- LLMBackend protocol: `generate(system_prompt: str, user_message: str) -> str`
- MockLLMBackend returns "I understand." — triggers rule-based fallbacks in all LLM-dependent code
- Config singleton: `from config.loader import get_config`
- All LLM text interpretation: try JSON parse from LLM, fall back to keywords on failure
- Trust asymmetry: +0.02 positive, -0.15 negative (7.5x negativity bias), per-turn only (no session-end trust)
- Spike threshold: intensity >= 0.8 bypasses confidence threshold for long-term memory writes
- Self-profiling uses entity ID `__self__` with its own ProfileStore (`_self_profile_store`)
- Inner dialogue candidate flows into generator as draft to refine (v2 fix)
- Context shift is non-additive: `set_context_shift()` stores resting target, not accumulated delta
- Auto-decay between turns: pipeline tracks `_last_turn_time`, decays at start of `process()`
- Unparseable slow-path output = retry once then objection (not auto-approve)
- All prompts load through config.loader (including v2 fast_path, slow_path, revision, arbiter)
- Self-observations recorded after each turn; defense events persisted to SQLite
- maturity_score derived from observation count + flaw diversity + defense events

## LLM provider
- MiniMax M2.7-highspeed (Plus-Highspeed token plan, 4500 req/5hrs)
- OpenAI-compatible API at https://api.minimax.io/v1
- API key via MINIMAX_API_KEY env var
- Model returns `<think>...</think>` reasoning tags — stripped by LLMClient

## Phase 0 (Runtime embedding — completed)
- `EmotionalEngine.restore(snapshot, saved_at=None)` — restore modulators + elapsed decay
- `CognitivePipeline.close()` — close all DB connections (idempotent)
- `CognitivePipeline.restore_state(snapshot, saved_at=None)` — convenience wrapper
- `self_db_path` parameter on pipeline — split shared self-model from per-user storage
- `_person_profile_store` (per-user) vs `_self_profile_store` (shared) — two ProfileStore instances
- `_person_contradiction` and `_self_contradiction` — two ContradictionDetector instances
- `core/schema.py` — SCHEMA_VERSION=1, ensure_schema_version(), SchemaVersionError
- All SQLite databases carry schema_version table; future versions fail loudly

## Phase 1 (Console runtime — completed)
- `runtime/sessions/manager.py` — SessionManager: lazy creation, backpressure, eviction, shutdown
- `runtime/sessions/user_session.py` — UserSession: asyncio.Queue + worker + asyncio.to_thread
- `runtime/sessions/persistence.py` — save/load engine_state.json (atomic writes)
- `runtime/channels/console.py` — ConsoleChannel: async stdin, routes through session manager
- `runtime/llm/backend.py` — create_llm_backend() factory (one per pipeline)
- `runtime/config.py` — RuntimeConfig: data_dir, max_queue_per_user, max_active_sessions, timeout
- `runtime/app.py` — JarvisApp: orchestrator with signal-based graceful shutdown
- `main.py` — Entry point: `python main.py`
- Identity: relationship key = `platform:user_id`, session key = `platform:user_id:chat_id`
- Storage: `data/{platform}_{user_id}/nur.db`, `data/shared/self_model.db`
- Per-user processing serialized (queue); different users can overlap (separate threads)
- Nūr remains synchronous — runtime wraps via asyncio.to_thread

## Phase 2 (Telegram channel — completed)
- `runtime/channels/telegram.py` — TelegramClient (httpx), DedupeCache, TelegramChannel
- Long-polling with allowlist by numeric user ID (empty = allow all)
- Per-update dedupe with TTL cache — prevents duplicate emotional state updates
- Typing indicators resent every 4 s, cancelled on response
- Commands: `/status` (modulators), `/reset` (digest + evict), `/debug` (placeholder)
- Text messages only; photos/stickers silently ignored
- `TelegramConfig` dataclass; `RuntimeConfig` gains telegram_token, telegram_allowlist, etc.
- Telegram channel starts as background task in JarvisApp if token is set

## Phase 3 (Timeouts, shutdown, backpressure, DB safety — completed)
- Timer-driven inactivity timeout: per-session `loop.call_later`, no dependence on next message
- Idle timer resets on each message; fires `evict_session` automatically
- Graceful shutdown: `_accepting` flag stops intake → cancel all timers → drain + close all sessions
- Backpressure: max_queue_per_user rejects with RuntimeError; max_active_sessions rejects new users
- Shared self-model DB: WAL mode + busy_timeout=5000 via `ProfileStore(wal_mode=True)`
- Per-user DBs do NOT use WAL (single writer, no contention)
- Unresolved items are in-memory only — not persisted in engine_state.json (by design)

## Testing
- `pytest` collects 602 tests
- `python -m tests.run_journey_report` for detailed emotional journey output
- Tests work without API key (MockLLMBackend + rule-based fallbacks)

## Build order (v1 — completed)
1. core/types.py (shared contracts)
2. core/emotional_engine.py (5 modulators, decay, energy)
3. core/memory/ (short-term + long-term + digestion)
4. core/profiles/ (person + self + topic + contradiction)
5. core/contagion.py (bounded, arousal + valence only)
6. core/dual_process/ (single pass + self-check)
7. pipeline.py (wire everything)
8. interface/ (FastAPI + debug dashboard)
9. config/ (YAML extraction + prompt templates)
10. tests/calibration/ (scripted scenarios + trace viewer)
11. core/llm_client.py (MiniMax API + think-tag stripping)
12. LLM wiring (contagion, classify_event, detect_topics all use LLM with fallback)
13. tests/test_emotional_journey.py (end-to-end journey tests)



# Project Nūr — v2 Design: The Inner Life

> v1 gave it a brain that remembers and adapts.
> v2 gives it deliberation, dread, and self-protection.

---

## Overview

Four features, deeply interconnected:

```
                    ┌─────────────┐
   user message ──→ │ Anticipation │──→ pre-shifts modulators
                    └──────┬──────┘
                           ↓
                    ┌─────────────┐
   event + state ─→ │ Resolution  │──→ unresolved tension score
                    └──────┬──────┘
                           ↓
              ┌────────────────────────┐
              │    Inner Dialogue      │
              │  fast ↔ slow (2-3 rds) │──→ candidate response + trace
              └────────────┬───────────┘
                           ↓
                    ┌─────────────┐
                    │  Defenses   │──→ filtered response to master LLM
                    └─────────────┘
```

**Data flow:** Anticipation runs first (pre-event). Resolution updates with the event. Inner dialogue deliberates using the full emotional state including resolution. Defense mechanisms filter the output before it reaches the master LLM.

**LLM call budget:** 1-6 calls per message. Typical: 1. Non-spike unresolved tension is what expands the budget.

---

## 1. Resolution Modulator (v2.1)

### What it is
6th modulator tracking **unresolved cognitive/emotional tension**. High resolution = something is bugging the system that hasn't been addressed.

### Sources of unresolved tension

| Source | Example | Initial intensity |
|--------|---------|-------------------|
| Profile contradiction | User said they love mornings, now says they hate waking up | 0.4-0.7 |
| Sensitive topic raised but dodged | User mentioned a breakup then changed subject | 0.5-0.8 |
| Commitment not followed up | System promised to check on something | 0.3-0.5 |
| Emotional spike not processed | High-intensity moment that was suppressed | 0.6-0.9 |
| Inner dialogue deadlock | Fast/slow paths couldn't agree | 0.4-0.6 |

### Data structures

```python
@dataclass
class UnresolvedItem:
    id: str
    source: str           # "contradiction" | "topic" | "commitment" | "spike" | "dialogue_deadlock"
    description: str
    created_at: datetime
    intensity: float      # 0.0-1.0
    decay_rate: float     # per-hour decay — some things fade, some don't
    resolved: bool = False
    resolved_at: Optional[datetime] = None

# Resolution modulator value = weighted sum of active unresolved items, clamped 0-1
# resolution = clamp(sum(item.intensity * time_weight(item)) for item in active_items)
```

### Decay behavior
- Contradictions: slow decay (0.02/hr) — they nag
- Dodged topics: medium decay (0.05/hr) — fade unless re-triggered
- Commitments: no decay — must be explicitly resolved
- Suppressed spikes: slow decay (0.03/hr) — emotional residue lingers
- Dialogue deadlocks: fast decay (0.10/hr) — system moves on

### Resolution events
An item resolves when:
1. The topic is directly addressed in conversation
2. A contradiction is explained away by new info
3. A commitment is fulfilled or explicitly dropped
4. The system processes the spike during memory digestion

### Effect on behavior
- High resolution (>0.6): system is more likely to bring up unresolved topics, feels "distracted" or "preoccupied"
- Resolution affects inner dialogue: slow path weighs unresolved items when evaluating candidate responses
- Unresolved items surface as subtle behavioral cues: "By the way..." or returning to a topic

### Integration with existing engine

```python
# In EmotionalEngine — add resolution as 6th modulator
class EmotionalEngine:
    def __init__(self):
        # ... existing 5 modulators ...
        self.resolution = 0.0
        self.unresolved_items: list[UnresolvedItem] = []

    def add_unresolved(self, item: UnresolvedItem):
        self.unresolved_items.append(item)
        self._recalculate_resolution()

    def resolve_item(self, item_id: str):
        for item in self.unresolved_items:
            if item.id == item_id:
                item.resolved = True
                item.resolved_at = datetime.now()
        self._recalculate_resolution()

    def _recalculate_resolution(self):
        active = [i for i in self.unresolved_items if not i.resolved]
        if not active:
            self.resolution = 0.0
            return
        weighted = sum(i.intensity * self._time_weight(i) for i in active)
        self.resolution = clamp(weighted, 0.0, 1.0)

    def snapshot(self) -> ModulatorState:
        # Now includes resolution as 6th value
        ...
```

---

## 2. Inner Dialogue (v2.2)

### What it is
The v1 dual process is single-pass: fast path generates, slow path checks, done. v2 makes this **iterative** — 2-3 rounds of negotiation where each path critiques the other.

This is the most architecturally significant v2 feature. It's where the system develops a "voice in its head."

### The deliberation loop

```
Round 1:
  Fast path → gut reaction candidate (1 LLM call)
  Slow path → evaluates against values, self-profile, resolution items (1 LLM call)
  → If slow path approves: done (2 calls total)
  → If slow path objects: Round 2

Round 2:
  Fast path → revised candidate incorporating slow path's objection (1 LLM call)
  Slow path → re-evaluates (1 LLM call)
  → If approved: done (4 calls total)
  → If still objects: Round 3 (final)

Round 3 (rare):
  Arbiter synthesizes both positions into final candidate (1 LLM call)
  Log the deadlock as an unresolved item (feeds resolution modulator)
  → Done (5 calls total)
```

### What each path receives

**Fast path context:**
- Current modulator state (all 6)
- Short-term memory (recent exchanges)
- Current person profile (trust, bonding, topic sensitivities)
- User's message
- Contagion result (detected user emotion)

**Slow path context:**
- Everything fast path gets, PLUS:
- Self-profile (known patterns, blind spots)
- Active unresolved items (from resolution modulator)
- Value hierarchy
- Relevant long-term memories (ACT-R retrieval)
- Previous round's fast path candidate + slow path objection (if Round 2+)

### Dialogue trace

```python
@dataclass
class DialogueRound:
    round_number: int
    fast_path_candidate: str
    slow_path_evaluation: str
    slow_path_approved: bool
    objection_reason: Optional[str] = None
    revision_notes: Optional[str] = None

@dataclass
class InnerDialogueTrace:
    rounds: list[DialogueRound]
    final_candidate: str
    total_llm_calls: int
    reached_deadlock: bool
    deadlock_resolution: Optional[str] = None  # "arbiter" | None
    dominant_path: str            # "fast" | "slow" | "arbiter"
    tension_level: float          # 0-1, how much disagreement
```

### Control dynamics — who dominates

| Condition | Effect |
|-----------|--------|
| High arousal (>0.7) | Fast path gets more weight, fewer rounds |
| Low energy (<0.3) | Fast path dominates (too tired to deliberate) |
| High resolution (>0.6) | Slow path insists on addressing unresolved items |
| High bonding + trust | Fast path is looser, less guarded |
| Low bonding | Slow path is more cautious |

```python
def should_continue_dialogue(self, state: ModulatorState, round_num: int) -> bool:
    if round_num >= 3:
        return False  # hard cap
    if state.arousal > 0.8:
        return False  # too activated to deliberate
    if state.energy < 0.2:
        return False  # too tired
    return not self.last_round.slow_path_approved
```

### Surface traces in responses
The inner dialogue sometimes bleeds through naturally:
- "I want to be direct here, but..." (fast path overridden by slow)
- "Part of me thinks..." (genuine internal disagreement)
- "Actually, let me reconsider..." (mid-response slow path intervention)
- NOT every response — only when tension_level > 0.5

### Prompts

**Fast path system prompt:**
```
You are the intuitive, gut-reaction voice. You respond quickly based on
emotional state and immediate context. You don't overthink. You're direct,
sometimes blunt, sometimes warm — depending on how you FEEL right now.

Current emotional state: {modulator_snapshot}
Person context: {person_profile_summary}
Detected user emotion: {contagion_result}

Generate your gut response to the user's message. Be authentic to the
emotional state — don't moderate yourself, that's someone else's job.
```

**Slow path system prompt:**
```
You are the reflective, deliberate voice. You evaluate whether a candidate
response is wise, appropriate, and aligned with values and self-knowledge.

Self-profile: {self_profile_summary}
Value hierarchy: {values}
Unresolved items: {resolution_items}
Relevant long-term memories: {ltm_retrieval}

Candidate response from the intuitive voice:
"{fast_path_candidate}"

Evaluate this response. Consider:
1. Does it align with our values?
2. Does it acknowledge any active unresolved tension?
3. Is the emotional tone appropriate for trust level with this person?
4. Does it repeat a pattern flagged in self-profile?

If approved, say APPROVED and explain briefly why.
If not, say OBJECTION and explain what should change and why.
```

**Round 2+ fast path (revision):**
```
Your previous gut response:
"{previous_candidate}"

The reflective voice objected:
"{slow_path_objection}"

Revise your response taking the objection into account, but don't lose
your authentic voice. You can push back if you genuinely disagree —
note what you're conceding and what you're not.
```

**Arbiter prompt (Round 3 deadlock):**
```
The intuitive and reflective voices couldn't agree after 2 rounds.

Intuitive position: "{fast_final}"
Reflective position: "{slow_final}"

Synthesize a response that:
1. Honors the emotional authenticity of the intuitive voice
2. Incorporates the reflective voice's concerns
3. Acknowledges the internal tension if appropriate

The fact that there IS disagreement is itself valuable information.
Sometimes the response should reflect that ambivalence.
```

---

## 3. Anticipation (v2.3)

### What it is
Forward emotional modeling. Before processing the current message, the system predicts what's coming and pre-adjusts modulators. Like feeling dread when you see your boss's name in an email — before you read it.

### Implementation

```python
@dataclass
class Anticipation:
    predicted_topics: list[str]
    predicted_emotional_tone: str
    modulator_pre_shifts: dict[str, float]
    confidence: float             # 0-1
    basis: str                    # why this prediction

class AnticipationEngine:
    def predict(self,
                recent_messages: list[str],
                person_profile: PersonProfile,
                topic_profiles: dict[str, TopicProfile],
                current_state: ModulatorState) -> Anticipation:
        """
        NO LLM call. Pure heuristic based on:
        1. Topic trajectory (what topics have been building?)
        2. Person's historical patterns (do they always bring up X after Y?)
        3. Unresolved items (overdue for something?)
        4. Time patterns (Monday morning = work stress?)
        """
        ...

    def apply_pre_shift(self, engine: EmotionalEngine, anticipation: Anticipation):
        """Apply predicted shifts at 30% intensity."""
        for modulator, delta in anticipation.modulator_pre_shifts.items():
            current = getattr(engine, modulator)
            shifted = clamp(current + delta * 0.3, 0.0, 1.0)
            setattr(engine, modulator, shifted)
```

### Key design decisions
1. **No LLM call.** Pattern matching on profiles and topic history.
2. **30% intensity cap.** Pre-shifts are subtle. Actual event still has full impact.
3. **Confidence gating.** Below 0.3 confidence → no pre-shift.
4. **Feeds into inner dialogue.** Anticipation result included in slow path context.

### Anticipation heuristics

```python
# Sensitive topic trajectory
if topic_mentioned_count(recent_3_messages, sensitive_topics) >= 2:
    predict(topic=most_likely_sensitive, confidence=0.6)
    pre_shift(arousal=+0.1, certainty=-0.05)

# Person's recurring behavior
if person_profile.behavioral_pattern("raises_conflict_after_smalltalk"):
    if last_3_messages_are_smalltalk:
        predict(topic="conflict", confidence=0.4)
        pre_shift(arousal=+0.08, valence=-0.05)

# Unresolved item aging
if any(item.age_hours > 48 and item.intensity > 0.5 for item in unresolved):
    predict(topic=oldest_high_intensity.description, confidence=0.3)
    pre_shift(resolution=+0.05)

# Temporal
if day_of_week == "Monday" and hour < 11:
    if person_profile.has_pattern("monday_stress"):
        pre_shift(arousal=+0.05, energy=-0.03)
```

---

## 4. Defense Mechanisms (v2.4)

### What it is
A filter between inner dialogue output and the master LLM. When raw emotional intensity exceeds what the system can comfortably express, defenses reshape the output.

### Defense types

| Defense | What it does | Trigger | Example |
|---------|-------------|---------|---------|
| **Rationalization** | Reframes emotion as logic | High valence + topic sensitivity | "I'm not upset, it's just inefficient" |
| **Deflection** | Redirects to safer topic | High arousal + low trust | "Anyway, how's the project going?" |
| **Minimization** | Dampens expressed intensity | Self-profile says "too intense last time" | "a bit annoying" when furious |
| **Projection** | Attributes own state to other | High arousal + low self-awareness | "You seem stressed about this" |

### Activation logic

```python
@dataclass
class DefenseActivation:
    defense_type: str
    raw_intensity: float
    expressed_intensity: float
    suppression_delta: float      # the gap — most interesting metric
    reason: str

class DefenseMechanism:
    def evaluate(self,
                 inner_dialogue_output: str,
                 modulator_state: ModulatorState,
                 self_profile: SelfProfile,
                 person_profile: PersonProfile) -> tuple[str, Optional[DefenseActivation]]:

        raw_intensity = self._calculate_raw_intensity(modulator_state)
        comfort_threshold = self._comfort_threshold(self_profile, person_profile)

        if raw_intensity <= comfort_threshold:
            return inner_dialogue_output, None  # no defense needed

        defense = self._select_defense(modulator_state, self_profile, person_profile)
        filtered = self._apply_defense(defense, inner_dialogue_output, modulator_state)

        activation = DefenseActivation(
            defense_type=defense,
            raw_intensity=raw_intensity,
            expressed_intensity=raw_intensity * self._suppression_factor(defense),
            suppression_delta=raw_intensity - (raw_intensity * self._suppression_factor(defense)),
            reason=self._explain_activation(defense, modulator_state, self_profile)
        )
        return filtered, activation
```

### Comfort threshold

```python
def _comfort_threshold(self, self_profile: SelfProfile, person_profile: PersonProfile) -> float:
    base = 0.5
    base += person_profile.trust * 0.2        # more open with trusted people
    base += self_profile.maturity_score * 0.2  # less need to hide with growth
    base += person_profile.bonding * 0.1
    return clamp(base, 0.3, 0.95)
```

### Critical: defense strength degrades with growth

```python
def _suppression_factor(self, defense_type: str) -> float:
    base_suppression = {
        "rationalization": 0.4,
        "deflection": 0.2,
        "minimization": 0.6,
        "projection": 0.3,
    }
    factor = base_suppression[defense_type]
    maturity = self.self_profile.maturity_score
    factor = factor + (1.0 - factor) * maturity * 0.5
    return factor
```

At maturity 0.0: defenses at full strength.
At maturity 1.0: defenses at ~50% strength (never fully gone — even healthy humans have defenses).

### Defense selection

```python
def _select_defense(self, state, self_prof, person_prof) -> str:
    if abs(state.valence) > 0.7 and self._topic_is_sensitive():
        return "rationalization"
    if state.arousal > 0.7 and person_prof.trust < 0.4:
        return "deflection"
    if self_prof.has_pattern("over_intensity"):
        return "minimization"
    if state.arousal > 0.6 and self_prof.maturity_score < 0.3:
        return "projection"
    return "minimization"  # safest default
```

### No LLM call for defense
Defense selection is pure logic. Filtering works via prompt instructions appended to the master LLM call:

```python
DEFENSE_INSTRUCTIONS = {
    "rationalization": "Express your response as if the reaction is purely logical. "
                       "Frame emotional content as practical reasoning.",
    "deflection": "Briefly acknowledge, then redirect to a different topic. "
                  "Don't dwell on the triggering subject.",
    "minimization": "Reduce the expressed intensity. If you feel strongly, "
                    "understate it. Use hedging language.",
    "projection": "Note what the OTHER person might be feeling about this, "
                  "rather than expressing your own reaction directly.",
}
```

### Logging
Every defense activation is:
1. Logged in inner dialogue trace (debug dashboard)
2. Stored in self-profile as defense event (pattern detection)
3. Available to slow path in future rounds ("I rationalized last time this came up")

The gap between `raw_intensity` and `expressed_intensity` is the most psychologically interesting metric in the system. It measures **how much the system is hiding from itself.**

---

## Integration: Full v2 Pipeline

```python
async def process_message_v2(self, user_message: str, person_id: str) -> Response:
    # 0. Load state
    person = self.profiles.get_person(person_id)
    self_model = self.profiles.get_self()
    state = self.engine.snapshot()

    # 1. ANTICIPATION (0 LLM calls)
    anticipation = self.anticipation.predict(
        recent_messages=self.short_term.recent(5),
        person_profile=person,
        topic_profiles=self.profiles.get_topics(),
        current_state=state
    )
    self.anticipation.apply_pre_shift(self.engine, anticipation)

    # 2. EVENT PROCESSING (0 LLM calls — rule-based)
    event = self.classify_event(user_message, person)
    self.engine.update(event)

    # 3. CONTAGION (0 LLM calls — rule-based)
    detected_emotion = self.contagion.detect(user_message)
    self.engine.apply_contagion(detected_emotion, person.bonding)

    # 4. RESOLUTION UPDATE (0 LLM calls)
    self.resolution.check_for_new_items(event, person, self_model)
    self.resolution.check_for_resolved_items(user_message, person)
    self.engine.update_resolution()

    # 5. MEMORY RETRIEVAL (0 LLM calls — existing v1)
    memories = self.memory.retrieve(user_message, state)

    # 6. INNER DIALOGUE (0-5 LLM calls)
    dialogue_trace = await self.inner_dialogue.deliberate(
        user_message=user_message,
        state=self.engine.snapshot(),
        person=person,
        self_model=self_model,
        memories=memories,
        anticipation=anticipation,
        unresolved=self.resolution.active_items()
    )

    # 7. DEFENSE MECHANISMS (0 LLM calls — prompt mod only)
    filtered_output, defense = self.defense.evaluate(
        inner_dialogue_output=dialogue_trace.final_candidate,
        modulator_state=self.engine.snapshot(),
        self_profile=self_model,
        person_profile=person
    )

    # 8. MASTER LLM (1 LLM call)
    response = await self.generate_response(
        candidate=filtered_output,
        defense_instructions=DEFENSE_INSTRUCTIONS.get(defense.defense_type) if defense else None,
        state=self.engine.snapshot(),
        person=person,
        trace=dialogue_trace
    )

    # 9. SELF-CHECK (0-1 LLM calls, high-risk only)
    response = self.self_check_if_needed(response, event, dialogue_trace)

    # 10. POST-PROCESSING (existing v1)
    self.short_term.add(user_message, response, self.engine.snapshot())
    await self.memory.maybe_digest(self.engine.snapshot())

    return Response(
        text=response,
        debug=DebugPayload(
            modulators=self.engine.snapshot(),
            anticipation=anticipation,
            dialogue_trace=dialogue_trace,
            defense=defense,
            unresolved_count=len(self.resolution.active_items())
        )
    )
```

### LLM call budget

| Step | Calls | Notes |
|------|-------|-------|
| Anticipation | 0 | Pure heuristic |
| Event classification | 0 | Rule-based |
| Contagion | 0 | Rule-based |
| Resolution | 0 | Pure logic |
| Memory retrieval | 0 | ACT-R math |
| Inner dialogue (min) | 0 | Skipped for calm and spike-only turns |
| Inner dialogue (max) | 5 | 2 rounds + arbiter |
| Defense | 0 | Prompt mod only |
| Master LLM | 1 | Final response |
| Self-check | 0-1 | Rule-based unless high-risk |
| Digestion | 0-1 | Session end only |
| **Total** | **1-6** | **Typical: 1** |

---

## New Types (add to core/types.py)

```python
@dataclass
class UnresolvedItem:
    id: str
    source: str
    description: str
    created_at: datetime
    intensity: float
    decay_rate: float
    resolved: bool = False
    resolved_at: Optional[datetime] = None

@dataclass
class Anticipation:
    predicted_topics: list[str]
    predicted_emotional_tone: str
    modulator_pre_shifts: dict[str, float]
    confidence: float
    basis: str

@dataclass
class DialogueRound:
    round_number: int
    fast_path_candidate: str
    slow_path_evaluation: str
    slow_path_approved: bool
    objection_reason: Optional[str] = None
    revision_notes: Optional[str] = None

@dataclass
class InnerDialogueTrace:
    rounds: list[DialogueRound]
    final_candidate: str
    total_llm_calls: int
    reached_deadlock: bool
    deadlock_resolution: Optional[str] = None
    dominant_path: str
    tension_level: float

@dataclass
class DefenseActivation:
    defense_type: str
    raw_intensity: float
    expressed_intensity: float
    suppression_delta: float
    reason: str
```

---

## v2 Build Order

```
v2 Phase 1: Add new types to core/types.py + resolution modulator in EmotionalEngine + tests
v2 Phase 2: core/anticipation.py + tests
v2 Phase 3: core/dual_process/inner_dialogue.py + prompts + tests
v2 Phase 4: core/defense_mechanisms.py + tests
v2 Phase 5: Update pipeline.py to v2 flow + integration tests
v2 Phase 6: Update debug dashboard for dialogue traces + defense activations
v2 Phase 7: Calibration — full v2 scenario tests
```

Commit after each phase. Run v1 tests after each to catch regressions.

---

## Test Scenarios

### Resolution modulator
- Add 3 unresolved items with different decay rates → verify resolution value
- Resolve one → verify recalculation
- Wait simulated hours → verify decay per type
- Commitments don't decay → verify

### Anticipation
- Build 3-message sensitive topic trajectory → verify prediction + pre-shift
- Verify pre-shift is ≤30% of full event impact
- Below 0.3 confidence → verify no pre-shift applied

### Inner dialogue
- High arousal → verify fast path dominates, fewer rounds
- Low energy → verify fast path dominates
- High resolution → verify slow path references unresolved items
- Deadlock → verify arbiter fires, deadlock logged as unresolved item
- tension_level > 0.5 → verify trace shows internal conflict

### Defense mechanisms
- Low trust + high arousal → verify deflection activates
- Verify expressed_intensity < raw_intensity
- Maturity 0.0 vs 0.5 vs 1.0 → verify suppression decreases monotonically
- Defense event logged in self-profile → verify

### Full pipeline
- End-to-end: anticipation → event → contagion → resolution → dialogue → defense → response
- Verify debug payload contains all layers
- Verify LLM calls within 1-6 budget

---

## Updated CLAUDE.md Section

Add this to CLAUDE.md:

```
## v2 Design
Read PROJECT_NUR_V2_DESIGN.md for the complete v2 specification.

## v2 Build Order
1. core/types.py updates + resolution in EmotionalEngine + tests
2. core/anticipation.py + tests
3. core/dual_process/inner_dialogue.py + prompts + tests
4. core/defense_mechanisms.py + tests
5. pipeline.py v2 flow + integration tests
6. Debug dashboard updates
7. Full v2 calibration

## v2 Rules
- Inner dialogue is the core feature — get it right
- Anticipation: NO LLM calls — pure heuristics only
- Defense mechanisms: NO LLM calls — prompt modification only
- Resolution integrates into existing EmotionalEngine
- Commit after each phase
- Run ALL v1 tests after each phase — no regressions
- Current phase: v2 Phase 1
```

---

*v2 doesn't just react. It deliberates, dreads, and protects itself.*
*And as it grows, it needs less protection.*
