# Project Nur

A hybrid cognitive architecture for human-like AI emotion, based on PSI Theory, ACT-R, and CLARION.

Jarvis is an AI assistant with persistent emotional state. It doesn't simulate emotions — emotions *emerge* from the interaction between continuous modulators, memory, drives, and context. After 20 conversations, it responds differently than it did on day one, because its internal state has genuinely changed.

## Status

**v2 + Jarvis Runtime complete.** Phase 11 is now underway with deterministic social appraisal and relationship-arc memory. The test suite has grown well past 1100 tests.

v1 gave it a brain that remembers and adapts.
v2 gives it deliberation, dread, and self-protection.
Phase 11 starts making it more socially human: better appraisal and better relationship continuity.
The runtime gives it a body — sessions, channels, persistence, and debug inspection.

See [CHANGELOG.md](CHANGELOG.md) for version history.
See [PROJECT_NUR_ARCHITECTURE.md](PROJECT_NUR_ARCHITECTURE.md) for the full vision.

## Quick Start

### Requirements

- Python 3.10+ (3.11+ recommended)
- MiniMax API key, a local OpenAI-compatible endpoint (for example vLLM), or mock backend for testing

### Install

```bash
git clone https://github.com/balfiky/nur.git
cd nur
pip install -e ".[dev]"
```

### Run via Jarvis Runtime (recommended)

```bash
# With real LLM (MiniMax M2.7-highspeed)
export MINIMAX_API_KEY="your-key-here"
python main.py

# With local vLLM / OpenAI-compatible backend
# runtime_config.yaml:
#   llm_backend: openai_compatible
#   llm_base_url: http://127.0.0.1:8000/v1
#   llm_model: Qwen/Qwen3-30B-A3B
python main.py

# Without LLM (mock backend — useful for development)
python main.py
```

Console channel starts by default. Set `telegram_token` in `runtime_config.yaml` to enable Telegram.
Debug API at http://127.0.0.1:8077/sessions.

### Run the standalone web server

```bash
uvicorn interface.api:app --reload --port 8000
```

Open http://localhost:8000 for the chat UI + debug dashboard.

### Run Tests

```bash
# All tests
pytest

# Specific test suite
pytest tests/test_emotional_journey.py -v

# Detailed emotional journey report
python -m tests.run_journey_report
```

### Use Programmatically

```python
from pipeline import CognitivePipeline
from core.llm_client import LLMClient

# With real LLM
client = LLMClient(api_key="your-key")
pipe = CognitivePipeline(llm_backend=client, db_path="jarvis.db")

# Process messages
result = pipe.process("Hey, how are you?", user_id="paco")
print(result.response)
print(result.debug.modulator_snapshot)
print(result.debug.emotion_label)

# v2 debug fields
print(result.debug.anticipation)          # Forward emotional prediction
print(result.debug.dialogue_trace)        # Inner dialogue rounds
print(result.debug.defense_activation)    # Defense mechanism (if fired)
print(result.debug.unresolved_count)      # Active unresolved tensions

# End session
digested = pipe.end_session(user_id="paco")
print(digested.summary)

# Simulate rest between sessions
pipe.apply_rest(hours=8.0)
```

---

## Architecture Overview

### The Core Idea

Emotions are not discrete labels. They are configurations of **six continuous modulators**:

| Modulator | Half-life | What it represents |
|-----------|-----------|-------------------|
| Arousal | ~2 min | Activation level — how "charged" the system is |
| Valence | ~30 min | Positive/negative mood — lingers after triggering event |
| Certainty | ~10 min | Confidence in understanding — low = anxious |
| Bonding | ~days | Connection to the person being talked to |
| Energy | drain/rest | Cognitive resource — drains with usage, recovers with time |
| Resolution | item-decay | Unresolved cognitive/emotional tension (v2) |

These combine to produce emergent emotions: arousal=0.9 + valence=0.1 + certainty=0.9 = anger. Same modulators at low energy = irritability. No labels are assigned — they emerge from the math.

### Three Theories, One Brain

| Theory | Contribution | Where in code |
|--------|-------------|---------------|
| **PSI Theory** (Dorner) | 6 continuous modulators, exponential decay, energy as resource | `core/emotional_engine.py` |
| **ACT-R** (Anderson) | Memory activation = recency x frequency x emotional bias | `core/memory/long_term.py` |
| **CLARION** (Sun) | Dual-process: iterative fast/slow deliberation + self-check | `core/dual_process/` |

### v2 Processing Flow

```
 1. ANTICIPATION: predict emotional trajectory from context (0 LLM calls)
 2. Contagion: detect user tone -> bounded mirror (0 LLM calls — rule-based)
 3. APPRAISAL: infer target, intent, vulnerability, affiliation (0 LLM calls)
 4. Context switch: load person profile baseline_shift
 5. Event classification + PSI update
 6. RESOLUTION: check for new/resolved tension items (0 LLM calls)
 7. Short-term memory: store emotional reaction
 8. Spike check: intensity > 0.8 -> immediate write to long-term
 9. Memory retrieval: ACT-R activation biased by current state
10. Profile + relationship lookup: person + self + topic + relationship context
11. Contradiction check: compare behavior against profiles
12. INNER DIALOGUE: 2-3 round fast/slow deliberation (2-5 LLM calls)
13. DEFENSE MECHANISMS: filter output if needed (0 LLM calls)
14. Master LLM: generate final response (1 LLM call)
15. Self-check (rule-based + optional LLM)
16. Post-processing: update memory, drain energy
17. [Session end] Digestion + relationship-arc consolidation (0-1 LLM call)
```

**LLM calls per message:** 1-6 (typical: 1)

---

## v2 Features

### Resolution Modulator

6th modulator tracking unresolved cognitive/emotional tension. Sources: spikes not processed, contradictions, dodged topics, commitments, inner dialogue deadlocks. Each source has a different decay rate — commitments never decay, deadlocks fade fast.

### Inner Dialogue

Iterative fast/slow path deliberation (2-3 rounds). The fast path generates a gut reaction; the slow path evaluates against values, self-profile, and unresolved items. If they disagree, the fast path revises. After 3 rounds of disagreement, an arbiter synthesizes both positions, and the deadlock is logged as an unresolved item.

Control dynamics: high arousal (>0.8) or low energy (<0.2) bypass deliberation. High resolution (>0.6) forces the slow path to insist on all 3 rounds.

### Anticipation

Forward emotional modeling. Before processing the current message, the system predicts what's coming and pre-adjusts modulators at 30% intensity. Pure heuristics — no LLM calls. Fires on sensitive topic buildup, person behavioral patterns, unresolved item aging, and temporal patterns (Monday stress, late night vulnerability).

### Social Appraisal

Before Nūr classifies an event, it now performs a deterministic social appraisal pass. This distinguishes:

- distress aimed at life vs distress aimed at the assistant
- apology / repair vs excuse / complaint
- warmth / gratitude / affiliative bids vs generic positive tone
- mixed affect and vulnerability vs flat sentiment

That appraisal feeds event classification, trust updates, self-observation, debug output, and later relationship-memory writes.

### Relationship-Arc Memory

Long-term memory is no longer just a flat list of emotionally biased summaries. Nūr now also stores a compact relationship layer:

- `relationship_events`: rupture, repair, commitment, recurring tension
- `open_loops`: unresolved relationship tension and pending follow-up commitments
- `relationship_context`: a compact summary retrieved before generation

This makes cross-session continuity feel more human without storing raw transcript dumps.

### Defense Mechanisms

A filter between inner dialogue and the master LLM. When raw emotional intensity exceeds the comfort threshold, defenses reshape the output via prompt instructions:

| Defense | Trigger | Effect |
|---------|---------|--------|
| Rationalization | Extreme valence + sensitive topic | Reframes emotion as logic |
| Deflection | High arousal + low trust | Redirects to safer topic |
| Minimization | Over-intensity pattern in self-profile | Dampens expressed intensity |
| Projection | High arousal + low maturity | Attributes feelings to other |

Defense strength degrades with maturity — at maturity 1.0, defenses are at ~50% strength (never fully gone). The gap between raw and expressed intensity measures how much the system is hiding from itself.

---

## Project Structure

```
nur/
|-- config/                          # Configuration (YAML + prompt templates)
|   |-- modulators.yaml              # Decay curves, baselines, spike threshold, energy, resolution
|   |-- attachment.yaml              # Attachment style (locked to secure in v1)
|   |-- profiles_schema.yaml         # Profile configs (person, self, topic, contradiction, contagion)
|   |-- values_seed.yaml             # Value hierarchy (static in v1)
|   |-- loader.py                    # Config loader with singleton, typed dataclasses
|   +-- prompts/                     # LLM prompt templates
|       |-- generator.md             # Jarvis personality + response generation
|       |-- self_check.md            # Self-check (tone fit, overconfidence, etc.)
|       |-- digestion.md             # Session digestion / memory consolidation
|       |-- contagion.md             # Emotional detection from user text
|       |-- classify_event.md        # Event type classification
|       |-- detect_topics.md         # Topic identification
|       |-- fast_path.md             # Inner dialogue: gut reaction (v2)
|       |-- slow_path.md             # Inner dialogue: reflective evaluation (v2)
|       |-- fast_path_revision.md    # Inner dialogue: revision after objection (v2)
|       +-- arbiter.md              # Inner dialogue: deadlock synthesis (v2)
|
|-- core/                            # Core modules
|   |-- types.py                     # ALL shared type contracts (v1 + v2 dataclasses, enums)
|   |-- emotional_engine.py          # PSI modulator state machine (6 modulators, decay, energy, resolution)
|   |-- contagion.py                 # Emotional contagion (LLM + rule-based fallback)
|   |-- appraisal.py                 # Deterministic social appraisal (Phase 11)
|   |-- llm_client.py                # MiniMax API client (OpenAI-compatible)
|   |-- anticipation.py              # Forward emotional modeling (v2, pure heuristics)
|   |-- defense_mechanisms.py        # Defense filter (v2, pure logic + prompt injection)
|   |-- memory/
|   |   |-- short_term.py            # In-memory emotional event buffer
|   |   |-- long_term.py             # SQLite-backed persistent memory (ACT-R retrieval)
|   |   |-- relationship.py          # Relationship-arc memory: rupture/repair/commitment/open loops
|   |   +-- digestion.py             # Post-session memory + relationship consolidation
|   |-- profiles/
|   |   |-- base.py                  # ProfileStore — shared observation/trait mechanism
|   |   |-- person.py                # Person profiles (trust, baseline_shift, primacy)
|   |   |-- self_model.py            # Self-as-entity (same mechanism as person profiles)
|   |   |-- topic.py                 # Topic emotional charge + avoidance
|   |   +-- contradiction.py         # Contradiction detection (self + others)
|   +-- dual_process/
|       |-- generator.py             # Response generation (LLMBackend protocol, prompt builder)
|       |-- self_check.py            # Rule-based + optional LLM self-check
|       +-- inner_dialogue.py        # Iterative fast/slow deliberation (v2)
|
|-- pipeline.py                      # CognitivePipeline — orchestrates the full v2 flow
|-- interface/
|   |-- api.py                       # FastAPI backend (REST + WebSocket)
|   +-- static/
|       +-- index.html               # Chat UI + v2 debug dashboard
|
|-- tests/
|   |-- test_emotional_engine.py     # 22 tests — modulators, decay, energy, contagion, attachment
|   |-- test_memory.py               # 31 tests — short-term, long-term, digestion, ACT-R
|   |-- test_profiles.py             # 51 tests — person, self, topic, contradiction
|   |-- test_contagion.py            # 29 tests — keyword detection + LLM path
|   |-- test_dual_process.py         # 15 tests — prompt building, generation, self-check
|   |-- test_pipeline.py             # 29 tests — full pipeline, event classification, LLM paths
|   |-- test_config.py               # 36 tests — YAML loading, defaults, singleton
|   |-- test_interface.py            # 20 tests — API endpoints + v2 debug fields
|   |-- test_llm_client.py           # 11 tests — MiniMax client, think-tag stripping
|   |-- test_calibration.py          # 27 tests — multi-session calibration scenarios
|   |-- test_emotional_journey.py    # 12 tests — end-to-end emotional journey scenarios
|   |-- test_resolution.py           # 19 tests — resolution modulator (v2)
|   |-- test_anticipation.py         # 27 tests — anticipation engine (v2)
|   |-- test_inner_dialogue.py       # 38 tests — inner dialogue deliberation (v2)
|   |-- test_defense_mechanisms.py   # 36 tests — defense types + suppression (v2)
|   |-- test_pipeline_v2.py          # 29 tests — v2 pipeline integration (v2)
|   |-- test_v2_scenarios.py         # 30 tests — v2 calibration scenarios (v2)
|   |-- run_journey_report.py        # Detailed journey report with numeric output
|   +-- calibration/
|       |-- scenarios.py             # Scripted multi-session scenarios
|       +-- trace_viewer.py          # Matplotlib modulator trace visualization
|
|-- PROJECT_NUR_ARCHITECTURE.md      # Full architectural vision (PSI + ACT-R + CLARION)
|-- PROJECT_NUR_BUILD_PLAN.md        # v1/v2 roadmap with build phases
|-- BUILD_ALL.md                     # Phase-by-phase build instructions
|-- CHANGELOG.md                     # Version history
|-- runtime/                         # Jarvis Runtime — lifecycle, channels, persistence
|   |-- app.py                       # JarvisApp orchestrator + signal handling
|   |-- config.py                    # RuntimeConfig dataclass + path helpers
|   |-- channels/
|   |   |-- base.py                  # Channel protocol (start/stop)
|   |   |-- console.py               # Async stdin console channel
|   |   +-- telegram.py              # Telegram long-polling + commands + typing
|   |-- sessions/
|   |   |-- manager.py               # SessionManager: create, evict, shutdown, backpressure
|   |   |-- user_session.py          # UserSession: queue + worker + asyncio.to_thread
|   |   +-- persistence.py           # Atomic session-state JSON save/load
|   |-- llm/
|   |   +-- backend.py               # create_llm_backend() factory
|   +-- debug/
|       +-- api.py                   # Session-aware debug endpoints (FastAPI)
|
|-- main.py                          # Runtime entry point (python main.py)
|-- runtime_config.yaml              # Runtime configuration (data_dir, limits, Telegram, debug)
|-- CLAUDE.md                        # AI assistant instructions + v2 design spec
|-- pyproject.toml                   # Python project config
+-- README.md                        # This file
```

---

## Module Reference

### core/types.py — Shared Contracts

All data structures that flow between modules. Every module imports from here.

| Type | Description |
|------|-------------|
| `ModulatorName` | Enum: arousal, valence, certainty, bonding, energy, resolution |
| `ModulatorState` | Snapshot of all 6 modulators (auto-clamped 0.0-1.0) |
| `EventType` | Enum: user_message, positive_feedback, negative_feedback, conflict, resolution, surprise, betrayal, warmth, silence, topic_shift |
| `EmotionalEvent` | An event with type, intensity (0.0-1.0), source, metadata, timestamp |
| `ShortTermEntry` | timestamp + event + emotion_snapshot |
| `LongTermEntry` | Distilled memory: summary, emotional_valence, trust_delta, topic, source_person, confidence, spike flag, ACT-R activation |
| `RelationshipEvent` | Durable relationship-arc event: rupture, repair, commitment, recurring_tension |
| `OpenLoop` | Persistent unresolved relationship tension or pending follow-up |
| `RelationshipContext` | Compact retrieved relationship summary passed to generation/debug |
| `PersonProfile` | trust, reliability, emotional_volatility, stress_response, baseline_shift, primacy_weight, interaction_count |
| `SelfProfile` | observed_traits, strengths, flaws, triggers, dissonance, maturity_score, defense_log |
| `TopicProfile` | topic name, emotional_charge, avoidance flag, conflict_count |
| `BaselineShift` | Per-person modulator resting state adjustment |
| `ValueHierarchy` | Ranked weighted values (static in v1) |
| `DetectedEmotion` | arousal, valence, certainty, intensity (from contagion) |
| `AppraisalFrame` | Turn-level target, intent, vulnerability, affiliation, and mixed-affect appraisal |
| `AttachmentStyle` | Enum: secure, anxious, avoidant, disorganized |
| `PipelineContext` | Full context assembled for LLM |
| `UnresolvedItem` | Unresolved tension: id, source, description, intensity, decay_rate (v2) |
| `Anticipation` | Forward prediction: topics, tone, pre-shifts, confidence, basis (v2) |
| `DialogueRound` | One fast/slow negotiation round (v2) |
| `InnerDialogueTrace` | Full deliberation trace: rounds, final candidate, tension level (v2) |
| `DefenseActivation` | Defense record: type, raw/expressed intensity, suppression delta (v2) |
| `DefenseEvent` | Logged defense for self-profile pattern detection (v2) |

### core/emotional_engine.py — PSI State Machine

```python
engine = EmotionalEngine()

# Update from event
is_spike = engine.update(event)        # Returns True if intensity >= 0.8

# Time-based decay toward baseline
engine.decay(elapsed_seconds=300)

# Mirror user emotion (bounded +/-0.15, weighted by bonding)
engine.apply_contagion(user_arousal, user_valence, bonding_score)

# Context switch for a person
engine.apply_context_shift(baseline_shift)

# Drain energy after processing
engine.drain_energy(intensity=0.5)

# Resolution modulator (v2)
engine.add_unresolved(item)            # Add unresolved tension item
engine.resolve_item(item_id)           # Resolve by ID
engine.active_unresolved()             # List active items

# Read state
engine.snapshot()                      # -> {"arousal": 0.7, "valence": 0.3, ..., "resolution": 0.4}
engine.to_emotion_label()              # -> "angry" (for logging only)
```

### core/anticipation.py — Forward Modeling (v2)

```python
engine = AnticipationEngine()

anticipation = engine.predict(
    recent_messages=["msg1", "msg2"],
    person_profile=person,
    topic_profiles={"breakup": topic_profile},
    current_state=modulator_state,
    unresolved_items=unresolved_list,
)
# anticipation.predicted_topics, .confidence, .modulator_pre_shifts, .basis

engine.apply_pre_shift(state, anticipation)  # Applies at 30% intensity, gated by confidence >= 0.3
```

4 heuristics (zero LLM calls): topic trajectory, person patterns, unresolved aging, temporal patterns.

### core/dual_process/inner_dialogue.py — Deliberation (v2)

```python
dialogue = InnerDialogue(backend=llm_client)

trace = dialogue.deliberate(
    user_message="Hello",
    state=modulator_state,
    person=person_profile,
    self_profile=self_profile,
    values=value_hierarchy,
    memories=retrieved_memories,
    unresolved=unresolved_items,
)
# trace.rounds, .final_candidate, .total_llm_calls, .reached_deadlock, .tension_level
```

Control dynamics: arousal > 0.8 or energy < 0.2 → bypass (1 round). Resolution > 0.6 → insist (3 rounds).

### core/defense_mechanisms.py — Defense Filter (v2)

```python
dm = DefenseMechanism()

filtered_output, activation = dm.evaluate(
    inner_dialogue_output="I feel really upset.",
    modulator_state=state,
    self_profile=self_prof,
    person_profile=person,
)
# activation.defense_type, .raw_intensity, .expressed_intensity, .suppression_delta
```

Comfort threshold: `0.5 + trust*0.2 + maturity*0.2 + bonding*0.1`, clamped [0.3, 0.95].
Suppression: `base + (1-base) * maturity * 0.5` — defenses weaken with growth but never vanish.

### core/memory/ — Dual Memory System

**ShortTermMemory** — in-memory, cleared each session:
```python
stm = ShortTermMemory(max_entries=200)
stm.record(event, modulator_state)
stm.recent(5)                          # Last 5 entries
stm.emotional_arc()                    # List of (timestamp, valence) pairs
```

**LongTermMemory** — SQLite-backed, persists across sessions:
```python
ltm = LongTermMemory(db_path="jarvis.db")
ltm.store(entry)                       # Only if confidence >= 0.6 or spike
ltm.store_spike(entry)                 # Force-write, bypasses confidence threshold
ltm.retrieve(current_state, source_person="paco", limit=5)  # ACT-R biased retrieval
```

**RelationshipMemory** — SQLite-backed relational continuity:
```python
rel = RelationshipMemory(db_path="jarvis.db")
rel.record_event(event)                # rupture / repair / commitment / recurring_tension
rel.upsert_open_loop(loop)             # unresolved tension or pending follow-up
rel.build_context("paco", topic="work")
```

### core/profiles/ — Unified Profiling

All profiles use the same underlying `ProfileStore` mechanism. The AI profiles itself using the exact same mechanism it uses to profile others (entity ID `__self__`).

### pipeline.py — Cognitive Pipeline

```python
pipe = CognitivePipeline(
    llm_backend=client,      # or MockLLMBackend() for testing
    db_path="jarvis.db",     # ":memory:" for transient
)

result = pipe.process("Hello!", user_id="paco")
result.response               # The generated response
result.debug                  # DebugState with full v1 + v2 transparency

digested = pipe.end_session(user_id="paco")
pipe.apply_rest(hours=8.0)
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send message, get response + full debug state (v1 + v2 fields) |
| GET | `/debug` | Current emotional state snapshot + resolution + unresolved items |
| POST | `/session/end` | End session, trigger digestion |
| POST | `/rest` | Simulate rest period (energy recovery) |
| WS | `/ws` | WebSocket for streaming chat |
| GET | `/` | Web UI (chat + v2 debug dashboard) |

### POST /chat
```json
// Request
{"message": "Hello!", "user_id": "paco"}

// Response
{
  "response": "Hey.",
  "debug": {
    "modulator_snapshot": {"arousal": 0.52, "valence": 0.65, "resolution": 0.0, ...},
    "event_classified": "warmth",
    "event_intensity": 0.4,
    "is_spike": false,
    "emotion_label": "content",
    "anticipation": {"predicted_topics": [], "confidence": 0.0, "basis": "no heuristic fired", ...},
    "dialogue_trace": {"rounds": [...], "tension_level": 0.0, "dominant_path": "fast", ...},
    "defense_activation": null,
    "unresolved_count": 0,
    "unresolved_items": [],
    ...
  }
}
```

### Runtime Debug API (port 8077)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/sessions` | List active sessions (session_key, rel_key, user_id, idle_seconds, queue_size) |
| GET | `/sessions/{session_key}/debug` | Live modulators, memory counts, unresolved items, relationship counts, last_turn debug |
| POST | `/sessions/{session_key}/reset` | Evict session (digest + persist + close) |

### Debug Dashboard

The web UI at `/` includes real-time visualization of:
- 6 modulator gauges (arousal, valence, certainty, bonding, energy, resolution)
- Anticipation predictions (topics, tone, confidence, pre-shifts)
- Inner dialogue rounds (fast/slow candidates, approval/objection, tension meter)
- Defense activation (type, raw vs expressed intensity bars, suppression delta)
- Unresolved items list (source, description, intensity, decay rate)
- Event classification, profiles, contradictions, memories, self-check

---

## Configuration Reference

### config/modulators.yaml

Controls the emotional engine math:

| Key | Default | Description |
|-----|---------|-------------|
| `half_lives.arousal` | 120.0 | Seconds for arousal to decay halfway to baseline |
| `half_lives.valence` | 1800.0 | Seconds for valence to decay halfway |
| `half_lives.certainty` | 600.0 | Seconds for certainty to decay halfway |
| `half_lives.bonding` | 86400.0 | Seconds for bonding to decay halfway (~1 day) |
| `spike_threshold` | 0.8 | Intensity above which events are "spikes" |
| `energy.drain_per_message` | 0.02 | Energy cost per processed message |
| `energy.drain_per_spike` | 0.08 | Additional energy cost for spike events |
| `energy.recovery_rate_per_hour` | 0.1 | Energy recovered per hour of rest |
| `contagion.factor` | 0.3 | Contagion mirroring strength |
| `contagion.cap` | 0.15 | Maximum contagion shift per update |
| `event_impacts.*` | varies | Per-event-type modulator deltas |
| `resolution.decay_rates.*` | varies | Per-source decay rates (v2) |

---

## Key Design Decisions

### Asymmetric Trust (7.5x negativity bias)
Trust builds slowly (+0.02 per positive event) but breaks fast (-0.15 per negative). Two insults erase more trust than five compliments build.

### Spike Bypass
Events with intensity >= 0.8 write directly to long-term memory, bypassing the confidence threshold. One betrayal can override months of positive accumulation.

### Unified Self/Other Profiling
The AI profiles itself using the exact same `ProfileStore` mechanism it uses to profile others. Entity ID `__self__` is treated identically to any person ID.

### LLM Fallback Architecture
Every text-interpretation function tries LLM first, falls back to rule-based keyword matching. Tests work without API keys.

### Defense Degradation
Defense mechanisms weaken with maturity but never fully disappear. At maturity 1.0, suppression is ~50% of its base strength. The suppression delta (raw - expressed intensity) measures how much the system is hiding from itself.

### Config-Driven Constants
All magic numbers live in YAML files. No hardcoded thresholds in module code. The `get_config()` singleton loads once and is shared across all modules.

---

## Testing

### Test Suites

| Suite | Tests | What it covers |
|-------|-------|---------------|
| test_emotional_engine | 22 | Modulators, decay, energy, contagion, attachment styles |
| test_memory | 31 | Short-term buffer, long-term SQLite, ACT-R retrieval, digestion |
| test_profiles | 51 | Person profiles, self-model, topic charge, contradiction detection |
| test_contagion | 29 | Keyword detection, LLM parsing, edge cases |
| test_dual_process | 15 | Prompt building, response generation, self-check rules |
| test_pipeline | 29 | Full pipeline flow, event classification, LLM integration |
| test_config | 36 | YAML loading, defaults, prompt loading, singleton behavior |
| test_interface | 20 | REST API endpoints, WebSocket, HTML serving, v2 debug fields |
| test_llm_client | 11 | MiniMax client, auth headers, think-tag stripping |
| test_calibration | 27 | Multi-session calibration scenarios |
| test_emotional_journey | 12 | End-to-end emotional journey (10 scenarios) |
| test_resolution | 19 | Resolution modulator, item decay, recalculation (v2) |
| test_anticipation | 27 | Topic trajectory, person patterns, temporal, confidence gating (v2) |
| test_inner_dialogue | 38 | Deliberation rounds, bypass, deadlock, prompt building (v2) |
| test_defense_mechanisms | 36 | All defense types, suppression, comfort threshold, logging (v2) |
| test_pipeline_v2 | 29 | v2 pipeline integration, LLM budget, v1 preservation (v2) |
| test_v2_scenarios | 30 | v2 calibration: deflection, disagreement, anticipation, degradation (v2) |
| test_regressions | 28 | Regression locks for v2 fixes (fixes 1-13, spike-only, sticky-spike) |
| test_phase0 | 26 | Restore, elapsed decay, close, shared self-profile, schema versions |
| test_runtime | 24 | Console e2e, serialization, session-state persistence, lifecycle, shared self-model |
| test_telegram | 26 | Dedupe, normalization, allowlist, commands, typing indicators |
| test_phase3 | 20 | Inactivity timeout, graceful shutdown, backpressure, WAL mode |
| test_debug_api | 15 | Session listing, per-session debug, reset, isolation |
| test_runtime_config | 21 | Config loading, local/backend selection, channel config |
| **Total** | **1150+** | |

---

## Future Roadmap

v2 phases 1-7 and runtime phases 0-4 are complete. Phase 11.1 and 11.2 are now landed. The remaining high-value path is:

| Feature | Description |
|---------|-------------|
| Phase 11.3 Response strategy selector | Choose validate / repair / ground / challenge gently based on appraisal + relationship context |
| v2.7 Deep self-reflection | Periodic pattern extraction across sessions |
| v2.8 Growth tracking | Milestone detection and personality evolution |
| v2.9 Expression-state regulation | Make felt state vs shown state explicit and inspectable |
| Deferred research | Attachment variants, dynamic value drift, and richer retrieval only if they clearly improve human-likeness |

---

*Project Nur (Arabic for "light") — because the goal is illumination, not imitation.*
