# Project Nur

A hybrid cognitive architecture for human-like AI emotion, based on PSI Theory, ACT-R, and CLARION.

Jarvis is an AI assistant with persistent emotional state. It doesn't simulate emotions — emotions *emerge* from the interaction between continuous modulators, memory, drives, and context. After 20 conversations, it responds differently than it did on day one, because its internal state has genuinely changed.

## Status

**v1 is complete.** All core systems are built, tested (288 tests), and wired together.

See [PROJECT_NUR_ARCHITECTURE.md](PROJECT_NUR_ARCHITECTURE.md) for the full vision.
See [PROJECT_NUR_BUILD_PLAN.md](PROJECT_NUR_BUILD_PLAN.md) for v1/v2 roadmap.

## Quick Start

### Requirements

- Python 3.10+ (3.11+ recommended)
- MiniMax API key (for LLM features) or runs with mock backend for testing

### Install

```bash
git clone https://github.com/balfiky/nur.git
cd nur
pip install -e ".[dev]"
```

### Run the Server

```bash
# With real LLM (MiniMax M2.7-highspeed)
export MINIMAX_API_KEY="your-key-here"
uvicorn interface.api:app --reload --port 8000

# Without LLM (mock backend — useful for development)
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

# End session (triggers memory digestion)
digested = pipe.end_session(user_id="paco")
print(digested.summary)

# Simulate rest between sessions
pipe.apply_rest(hours=8.0)
```

---

## Architecture Overview

### The Core Idea

Emotions are not discrete labels. They are configurations of **five continuous modulators**:

| Modulator | Half-life | What it represents |
|-----------|-----------|-------------------|
| Arousal | ~2 min | Activation level — how "charged" the system is |
| Valence | ~30 min | Positive/negative mood — lingers after triggering event |
| Certainty | ~10 min | Confidence in understanding — low = anxious |
| Bonding | ~days | Connection to the person being talked to |
| Energy | drain/rest | Cognitive resource — drains with usage, recovers with time |

These combine to produce emergent emotions: arousal=0.9 + valence=0.1 + certainty=0.9 = anger. Same modulators at low energy = irritability. No labels are assigned — they emerge from the math.

### Three Theories, One Brain

| Theory | Contribution | Where in code |
|--------|-------------|---------------|
| **PSI Theory** (Dorner) | 5 continuous modulators, exponential decay, energy as resource | `core/emotional_engine.py` |
| **ACT-R** (Anderson) | Memory activation = recency x frequency x emotional bias | `core/memory/long_term.py` |
| **CLARION** (Sun) | Dual-process: generate response + self-check | `core/dual_process/` |

### v1 Processing Flow (15 steps)

```
 1. Input arrives
 2. Contagion: detect user tone -> bounded mirror (arousal + valence)
 3. Context switch: load person profile baseline_shift
 4. PSI engine: update 5 modulators from event + drives + energy
 5. Short-term memory: store emotional reaction
 6. Spike check: intensity > 0.8 -> immediate write to long-term
 7. Memory retrieval: ACT-R activation biased by current state
 8. Profile lookup: person + self + topic
 9. Contradiction check: compare behavior against profiles
10. Generate response (1 LLM call with full context)
11. Self-check (rule-based + optional LLM)
12. Output delivered
13. Update short-term memory with outcome
14. Drain energy
15. [Session end] Digestion (1 LLM call)
```

**LLM calls per message:** 2-4 (contagion + classify + generate + optional self-check)
**LLM calls at session end:** 1 (digestion)

---

## Project Structure

```
nur/
|-- config/                          # Configuration (YAML + prompt templates)
|   |-- modulators.yaml              # Decay curves, baselines, spike threshold, energy, event impacts
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
|       +-- detect_topics.md         # Topic identification
|
|-- core/                            # Core modules
|   |-- types.py                     # ALL shared type contracts (dataclasses, enums)
|   |-- emotional_engine.py          # PSI modulator state machine (5 modulators, decay, energy)
|   |-- contagion.py                 # Emotional contagion (LLM + rule-based fallback)
|   |-- llm_client.py                # MiniMax API client (OpenAI-compatible)
|   |-- memory/
|   |   |-- short_term.py            # In-memory emotional event buffer
|   |   |-- long_term.py             # SQLite-backed persistent memory (ACT-R retrieval)
|   |   +-- digestion.py             # Post-session memory consolidation
|   |-- profiles/
|   |   |-- base.py                  # ProfileStore — shared observation/trait mechanism
|   |   |-- person.py                # Person profiles (trust, baseline_shift, primacy)
|   |   |-- self_model.py            # Self-as-entity (same mechanism as person profiles)
|   |   |-- topic.py                 # Topic emotional charge + avoidance
|   |   +-- contradiction.py         # Contradiction detection (self + others)
|   +-- dual_process/
|       |-- generator.py             # Response generation (LLMBackend protocol, prompt builder)
|       +-- self_check.py            # Rule-based + optional LLM self-check
|
|-- pipeline.py                      # CognitivePipeline — orchestrates the full 15-step flow
|-- interface/
|   |-- api.py                       # FastAPI backend (REST + WebSocket)
|   +-- static/
|       +-- index.html               # Chat UI + debug dashboard
|
|-- tests/
|   |-- test_emotional_engine.py     # 22 tests — modulators, decay, energy, contagion, attachment
|   |-- test_memory.py               # 31 tests — short-term, long-term, digestion, ACT-R
|   |-- test_profiles.py             # 51 tests — person, self, topic, contradiction
|   |-- test_contagion.py            # 29 tests — keyword detection + LLM path
|   |-- test_dual_process.py         # 15 tests — prompt building, generation, self-check
|   |-- test_pipeline.py             # 29 tests — full pipeline, event classification, LLM paths
|   |-- test_config.py               # 36 tests — YAML loading, defaults, singleton
|   |-- test_interface.py            # 11 tests — API endpoints
|   |-- test_llm_client.py           # 11 tests — MiniMax client, think-tag stripping
|   |-- test_calibration.py          # 27 tests — multi-session calibration scenarios
|   |-- test_emotional_journey.py    # 12 tests — end-to-end emotional journey scenarios
|   |-- run_journey_report.py        # Detailed journey report with numeric output
|   +-- calibration/
|       |-- scenarios.py             # Scripted multi-session scenarios
|       +-- trace_viewer.py          # Matplotlib modulator trace visualization
|
|-- PROJECT_NUR_ARCHITECTURE.md      # Full architectural vision (PSI + ACT-R + CLARION)
|-- PROJECT_NUR_BUILD_PLAN.md        # v1/v2 roadmap with build phases
|-- BUILD_ALL.md                     # Phase-by-phase build instructions
|-- CLAUDE.md                        # AI assistant instructions
|-- pyproject.toml                   # Python project config
+-- README.md                        # This file
```

---

## Module Reference

### core/types.py — Shared Contracts

All data structures that flow between modules. Every module imports from here.

| Type | Description |
|------|-------------|
| `ModulatorName` | Enum: arousal, valence, certainty, bonding, energy |
| `ModulatorState` | Snapshot of all 5 modulators (auto-clamped 0.0-1.0) |
| `EventType` | Enum: user_message, positive_feedback, negative_feedback, conflict, resolution, surprise, betrayal, warmth, silence, topic_shift |
| `EmotionalEvent` | An event with type, intensity (0.0-1.0), source, metadata, timestamp |
| `ShortTermEntry` | timestamp + event + emotion_snapshot |
| `LongTermEntry` | Distilled memory: summary, emotional_valence, trust_delta, topic, source_person, confidence, spike flag, ACT-R activation |
| `PersonProfile` | trust, reliability, emotional_volatility, stress_response, baseline_shift, primacy_weight, interaction_count |
| `SelfProfile` | observed_traits, strengths, flaws, triggers, dissonance score |
| `TopicProfile` | topic name, emotional_charge, avoidance flag, conflict_count |
| `BaselineShift` | Per-person modulator resting state adjustment |
| `ValueHierarchy` | Ranked weighted values (static in v1) |
| `DetectedEmotion` | arousal, valence, certainty, intensity (from contagion) |
| `AttachmentStyle` | Enum: secure, anxious, avoidant, disorganized |
| `PipelineContext` | Full context assembled for LLM: modulators + profiles + memories + values + contradictions |

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

# Read state
engine.snapshot()                      # -> {"arousal": 0.7, "valence": 0.3, ...}
engine.to_emotion_label()              # -> "angry" (for logging only)
```

**Key constants** (from config/modulators.yaml):
- Half-lives: arousal=120s, valence=1800s, certainty=600s, bonding=86400s
- Spike threshold: 0.8
- Energy drain: 0.02/message + 0.08/spike
- Energy recovery: 0.1/hour
- Contagion cap: +/-0.15, factor: 0.3

### core/memory/ — Dual Memory System

**ShortTermMemory** — in-memory, cleared each session:
```python
stm = ShortTermMemory(max_entries=200)
stm.record(event, modulator_state)
stm.recent(5)                          # Last 5 entries
stm.emotional_arc()                    # List of (timestamp, valence) pairs
stm.average_intensity()
stm.peak_intensity()
stm.valence_drift()                    # Last valence - first valence
stm.clear()
```

**LongTermMemory** — SQLite-backed, persists across sessions:
```python
ltm = LongTermMemory(db_path="jarvis.db")
ltm.store(entry)                       # Only if confidence >= 0.6 or spike
ltm.store_spike(entry)                 # Force-write, bypasses confidence threshold
ltm.retrieve(current_state, source_person="paco", limit=5)  # ACT-R biased retrieval
ltm.count()
ltm.all()
ltm.by_person("paco")
ltm.by_topic("work")
LongTermMemory.compute_trust_delta(valence)  # +0.02 positive, -0.15 negative
```

**Digestion** — runs at session end:
```python
result = digest_session(short_term, long_term, source_person="paco",
                        llm_client=client, conversation_history=history)
# Returns DigestedSession: summary, arc_label, trust_delta, spike_events,
#   memories_written, energy_drain, topics, unresolved_flags
```

### core/profiles/ — Unified Profiling

All profiles use the same underlying `ProfileStore` observation/trait mechanism. The AI profiles itself using the exact same mechanism it uses to profile others.

**PersonProfileManager:**
```python
pm = PersonProfileManager(profile_store, db_path="jarvis.db")
profile = pm.get_or_create("paco")     # Load or create
pm.update_trust("paco", valence=0.8)   # Asymmetric: +0.02 pos, -0.15 neg
pm.record_interaction("paco", {"engagement": 0.7}, context="warmth")
pm.get_baseline_shift("paco")          # -> BaselineShift for context switching
pm.get_expected_traits("paco")         # For contradiction detection
```

**SelfProfileManager:**
```python
sm = SelfProfileManager(profile_store)
profile = sm.get_profile()             # -> SelfProfile with strengths, flaws, triggers
sm.record_behavior("patient", 0.8)     # Observe own behavior
sm.get_expected_traits()               # For self-contradiction detection
```

**TopicProfileManager:**
```python
tm = TopicProfileManager(db_path="jarvis.db")
tm.get_or_create("work")
tm.record_negative("work", intensity=0.8)  # Charge increases (toward 1.0)
tm.record_positive("work", intensity=0.5)  # Charge decreases (toward 0.0)
tm.record_conflict("work")                 # Increments conflict_count
# Avoidance triggers at: charge >= 0.7 or conflict_count >= 3
```

**ContradictionDetector:**
```python
cd = ContradictionDetector(profile_store)
result = cd.detect("paco", expected_traits)
# result.contradictions: list of {trait, expected, observed, divergence, description}
```

### core/contagion.py — Emotional Detection

```python
from core.contagion import detect_emotion

# With LLM (returns arousal, valence, certainty, intensity from JSON)
result = detect_emotion("I'm so angry!", llm_client=client)

# Without LLM (rule-based keyword fallback)
result = detect_emotion("I'm so angry!")
# result.arousal = 0.8, result.valence = 0.15
```

50+ regex patterns for keyword matching. LLM returns JSON `{"arousal", "valence", "certainty", "intensity"}`. Falls back to rules if JSON parsing fails.

### core/llm_client.py — MiniMax API Client

```python
from core.llm_client import LLMClient

client = LLMClient(
    api_key="your-key",                    # or MINIMAX_API_KEY env var
    base_url="https://api.minimax.io/v1",  # default
    model="MiniMax-M2.7-highspeed",        # default (Plus-Highspeed plan)
)
response = client.generate(system_prompt, user_message)
```

Conforms to the `LLMBackend` protocol. Strips `<think>...</think>` reasoning tags from M2.7 responses.

### core/dual_process/ — Generation + Self-Check

**ResponseGenerator:**
```python
gen = ResponseGenerator(backend=client)
result = gen.generate(pipeline_context, user_message, conversation_history)
# result.response, result.system_prompt, result.correction_note
```

Builds system prompt from `config/prompts/generator.md` template, injecting modulator state, profiles, memories, values, contradictions, and behavioral guidance.

**SelfChecker:**
```python
checker = SelfChecker(llm_client=client)
result = checker.check(response_text, pipeline_context)
# result.passed, result.failed, result.issues, result.correction_note
```

Rule-based checks (always run): tone fit, overconfidence, bluntness, energy-awareness, contradiction acknowledgment. Optional LLM check when client is available.

### pipeline.py — Cognitive Pipeline

```python
pipe = CognitivePipeline(
    llm_backend=client,      # or MockLLMBackend() for testing
    db_path="jarvis.db",     # ":memory:" for transient
)

# Process a message (full 15-step flow)
result = pipe.process("Hello!", user_id="paco")
result.response               # The generated response
result.debug                  # DebugState with full transparency

# End session
digested = pipe.end_session(user_id="paco")

# Simulate rest between sessions
pipe.apply_rest(hours=8.0)
```

Event classification and topic detection use LLM when available, fall back to rule-based keyword matching.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send message, get response + debug state |
| GET | `/debug` | Current emotional state snapshot |
| POST | `/session/end` | End session, trigger digestion |
| POST | `/rest` | Simulate rest period (energy recovery) |
| WS | `/ws` | WebSocket for streaming chat |
| GET | `/` | Web UI (chat + debug dashboard) |

### POST /chat
```json
// Request
{"message": "Hello!", "user_id": "paco"}

// Response
{
  "response": "Hey.",
  "debug": {
    "modulator_snapshot": {"arousal": 0.52, "valence": 0.65, ...},
    "event_classified": "warmth",
    "event_intensity": 0.4,
    "is_spike": false,
    "emotion_label": "content",
    "person_profile": {"person_id": "paco", "trust": 0.58, "interaction_count": 12},
    "self_check_passed": true,
    ...
  }
}
```

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

### config/profiles_schema.yaml

Controls profiling behavior:

| Key | Default | Description |
|-----|---------|-------------|
| `person.trust_positive_delta` | 0.02 | Trust gain per positive interaction |
| `person.trust_negative_delta` | -0.15 | Trust loss per negative interaction (7.5x asymmetry) |
| `topic.charge_negative_delta` | 0.10 | Topic charge gain per negative mention |
| `topic.avoidance_charge_threshold` | 0.7 | Charge level triggering topic avoidance |
| `contradiction.threshold` | 0.3 | Divergence threshold for contradiction flags |
| `self_model.strength_threshold` | 0.7 | Trait score above which it's a "strength" |
| `self_model.flaw_threshold` | 0.6 | Trait score above which negative trait is a "flaw" |

### config/prompts/

Markdown templates with `{placeholder}` variables, loaded at startup:

| File | Used by | Placeholders |
|------|---------|-------------|
| `generator.md` | ResponseGenerator | `{modulator_state}`, `{self_profile}`, `{person_name}`, `{person_profile}`, `{topic_profiles}`, `{values}`, `{retrieved_memories}`, `{contradiction_flags}`, `{behavioral_guidance}` |
| `self_check.md` | SelfChecker | `{modulator_state}`, `{self_profile}`, `{person_profile}`, `{contradiction_flags}` |
| `digestion.md` | digest_session | `{emotional_arc}`, `{events}`, `{conversation_history}` |
| `contagion.md` | detect_emotion | None (user message passed directly) |
| `classify_event.md` | _classify_event | `{arousal}`, `{valence}`, `{certainty}`, `{intensity}` |
| `detect_topics.md` | _detect_topics | `{known_topics}` |

---

## Key Design Decisions

### Asymmetric Trust (7.5x negativity bias)
Trust builds slowly (+0.02 per positive event) but breaks fast (-0.15 per negative). Two insults erase more trust than five compliments build. This mirrors the psychological negativity bias.

### Spike Bypass
Events with intensity >= 0.8 write directly to long-term memory, bypassing the confidence threshold. One betrayal can override months of positive accumulation.

### Unified Self/Other Profiling
The AI profiles itself using the exact same `ProfileStore` mechanism it uses to profile others. Entity ID `__self__` is treated identically to any person ID. Same contradiction detection, same trait extraction.

### LLM Fallback Architecture
Every text-interpretation function (contagion, event classification, topic detection) tries LLM first, falls back to rule-based keyword matching. This means:
- Tests work without API keys (MockLLMBackend returns non-JSON, triggers fallback)
- Production uses LLM for nuanced understanding
- System degrades gracefully if API is down

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
| test_interface | 11 | REST API endpoints, WebSocket, HTML serving |
| test_llm_client | 11 | MiniMax client, auth headers, think-tag stripping |
| test_calibration | 27 | Multi-session scenarios (trust, betrayal, contagion, energy) |
| test_emotional_journey | 12 | End-to-end emotional journey (10 scenarios) |
| **Total** | **288** | |

### Emotional Journey Scenarios

The `test_emotional_journey.py` file tests 10 realistic scenarios:

1. **Trust building** — 5 warm messages, assert valence/bonding/trust increase
2. **Betrayal after trust** — Build trust then send hostile messages, assert crash
3. **Trust asymmetry** — Prove 2 negatives erase more than 5 positives built
4. **Energy drain** — 15 intense messages, assert energy depleted
5. **Session persistence** — Trust and memories survive across pipeline instances
6. **Emotional contagion** — Excited input raises arousal, depressed drops valence
7. **Context switching** — Different users get different emotional starting positions
8. **Topic sensitivity** — Negative topic charge persists and affects modulators
9. **Spike detection** — Extreme messages trigger spikes and direct LT writes
10. **Decay over time** — Arousal decays fast (120s), valence recovers slowly (1800s)

Run `python -m tests.run_journey_report` for detailed numeric output.

---

## v2 Roadmap

v1 is complete. v2 adds the "human layer":

| Feature | Description | LLM cost |
|---------|-------------|----------|
| v2.1 Resolution modulator | 6th modulator for unfinished business | 0 |
| v2.2 Inner dialogue | Iterative fast/slow path negotiation | +2-3 calls |
| v2.3 Anticipation | Predict next moves, pre-shift modulators | +1 call |
| v2.4 Defense mechanisms | Rationalization, deflection, minimization, projection | +0-1 call |
| v2.5 Dynamic value drift | Values evolve from accumulated experience | 0 |
| v2.6 Attachment variants | Unlock anxious/avoidant/disorganized styles | 0 |
| v2.7 Deep self-reflection | Periodic pattern extraction across sessions | +1 call/N sessions |
| v2.8 Growth tracking | Milestone detection and personality evolution | 0 |

See [PROJECT_NUR_BUILD_PLAN.md](PROJECT_NUR_BUILD_PLAN.md) for full details.

---

*Project Nur (Arabic for "light") — because the goal is illumination, not imitation.*
