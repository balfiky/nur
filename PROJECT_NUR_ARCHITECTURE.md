# Project Nūr: A Hybrid Cognitive Architecture for Human-Like AI Emotion

> **"The goal is not to simulate emotion. It is to build a system where emotion emerges from the interaction between memory, drives, and context — the way it does in humans."**

---

## Part 1: The Idea

### 1.1 The Problem

Current AI assistants are emotionally flat. They process each message in isolation, produce a response, and forget. Even with memory systems like RAG, they remember *facts* but not *feelings*. They know you mentioned a project last week, but they don't know how that conversation *felt* — whether you were excited or defeated, whether you trusted them more afterward or less.

Humans don't work this way. Every interaction is colored by what just happened, what has happened over time, who you're talking to, who you are, and competing internal drives that don't always agree. The result is that human responses have *texture*. The same words from a trusted friend and a stranger land differently. A patient person who's been pushed too far snaps in a way that's distinct from an impatient person's irritation. These aren't surface-level behaviors — they emerge from deep, persistent internal state.

### 1.2 The Core Insight

**Emotions are not discrete labels. They are configurations of continuous internal variables.**

You don't need a "happiness module" and an "anger module." You need a small set of continuous modulators — arousal, certainty, valence, bonding, resolution, energy — that shift in response to events and color everything the system does. Just as RGB values produce millions of colors from three channels, six modulator floats produce the full spectrum of human emotion, including blended states like "bittersweet nostalgia" or "anxious excitement" that no discrete system can represent.

This insight comes from Dietrich Dörner's PSI theory, which models emotions as emergent properties of a motivated cognitive system rather than as separate components bolted onto reasoning.

### 1.3 The Architecture: Three Theories, One Brain

This architecture combines ideas from three established cognitive frameworks, each contributing what the others lack:

**From PSI Theory (Dörner/Bach) → The Emotional Engine**

- Six continuous modulators: arousal, certainty, valence, bonding, resolution, energy
- Each with its own decay curve (arousal fades in minutes; bonding shifts over days)
- Emotions *emerge* from modulator combinations, not from labels
- Drives (needs for certainty, connection, competence) create motivation
- Attachment style configuration shapes how the bonding modulator behaves globally

**From ACT-R (Anderson/CMU) → The Memory System**

- Memories have activation scores based on recency × frequency × context
- Emotional tags are stored alongside facts — not *what* happened, but *how it landed*
- Current emotional state biases which memories surface (stress → negative memories rise)
- Activation decays over time but gets boosted by relevant context

**From CLARION (Sun) → The Dual-Process Reasoning**

- Fast path (implicit): pattern-matched gut reactions colored by profiles and emotion
- Slow path (explicit): deliberate reasoning, weighing options consciously
- The balance between paths shifts based on arousal and energy — under pressure or fatigue, the fast path dominates
- Enhanced with iterative inner dialogue: the two paths negotiate over 2-3 rounds before producing a final output

### 1.4 The Dual Memory System

Human memory isn't one thing. It operates on at least two timescales that interact:

**Short-Term Emotional Memory**

- Lives within a single conversation
- Captures instant emotional reactions: "that comment felt sharp"
- High intensity, rapid decay
- Influences tone and responsiveness in real-time
- At end of session, undergoes *digestion* — the raw events die, but emotional residue is distilled and written to long-term storage

**Long-Term Emotional Memory**

- Persists across all conversations
- Stores accumulated emotional patterns, not individual events
- Compounds slowly: 47 warm interactions gradually build a trust score
- Uses asymmetric update curves: trust builds slow (+0.02 per positive event) but breaks fast (−0.15 per betrayal). Negative events weigh roughly 3× positive ones. This mirrors the psychological negativity bias documented extensively in research.

**Spike Threshold**

Not all events follow gradual accumulation. Some are so intense — a betrayal, a major loss, unexpected profound kindness — that they bypass the gradual path and write directly to long-term memory with outsized weight. One traumatic event can override months of positive accumulation. Without this mechanism, the emotional math feels unrealistically smooth.

**Post-Session Digestion**

After each conversation, a digestion step runs:

1. Analyze the emotional arc of the conversation
2. Extract patterns (not raw events)
3. Apply confidence threshold — only write to long-term if signals are strong and consistent
4. Update entity profiles (self and others)
5. Log energy drain from the session
6. Check for value drift
7. Detect growth milestones
8. Discard raw short-term data

The confidence threshold prevents sarcasm from being misread as hostility and compounding into a false long-term pattern.

### 1.5 Entity Profiles: A Unified Perception System

Humans don't just remember events — they build *mental models* of every person they interact with regularly. Your coworker walks into a meeting and you already know how they'll react to bad news before they speak. That's the profile running.

The critical design decision: **the AI profiles itself using the exact same mechanism it uses to profile others.** Just as you know yourself by observing your own behavior over time (not through a magic internal window), the system builds its self-model by watching its own patterns.

The system maintains four types of profiles, all sharing the same underlying mechanism:

**Person Profiles**

Each person the AI interacts with gets a multi-dimensional profile:

- `trust`: 0.0–1.0, slow to build, fast to break
- `reliability`: how often they follow through on stated intentions
- `humor_style`: dry, playful, sarcastic, none
- `stress_response`: withdraws, lashes out, seeks support, shuts down
- `emotional_volatility`: how rapidly their expressed emotions change
- `primacy_weight`: how much first interactions still influence the profile
- `modulator_baseline_shift`: how talking to this person shifts the AI's emotional resting state (context switching)

Profiles create *expectations*. When behavior violates expectations, the emotional response is amplified proportionally to the gap.

**Self Profile (Jarvis as its own entity)**

The same profiling mechanism, turned inward:

- `observed_traits`: derived from actual behavioral data, not declared identity (e.g., "patient" only if behavior consistently shows patience)
- `strengths`: patterns of successful outcomes (e.g., "effective at de-escalation" after 50 sessions of data)
- `flaws`: recurring failure patterns (e.g., "too blunt when certainty is high")
- `triggers`: topics/situations that consistently cause disproportionate modulator spikes
- `conditional_patterns`: "when X, I tend to Y" (e.g., "when energy is low, I default to the fast path too much")
- `growth_milestones`: tracked changes over time ("fast-path usage dropped 30% over 50 sessions")
- `dissonance`: gap between self-model and recent behavior

Key insight: strengths and flaws aren't declared in a config file. They *emerge* from pattern extraction over accumulated behavioral data. The self-profile at conversation 500 is different from conversation 1, shaped by what the system has experienced.

The self-profile has **read access during reasoning** — it's injected as context before the dual-process step. The LLM sees: "Your observed pattern is that you tend to be too blunt when certainty is high. Certainty is currently 0.9." This enables real-time self-regulation.

Dissonance detection works identically to contradiction detection for others: when recent behavior diverges from the self-model, the same mechanism that flags "your friend is acting weird" flags "I'm acting weird."

**Topic Profiles**

Some subjects carry emotional charge independent of who's speaking:

- `work`: charge=0.6 (moderately stressful)
- `family`: warmth=0.8 (generally positive)
- `health`: avoid=true (painful territory)
- `past_job`: pain=0.8 (strongly negative)

These bias the emotional engine before any reasoning begins.

**Values**

Not personality traits — *principles*. A ranked, weighted hierarchy:

```
loyalty: 0.9
honesty: 0.85
kindness: 0.8
justice: 0.7
autonomy: 0.6
```

When two values conflict in a decision, the tension itself becomes an emotional event — certainty drops, arousal rises. The dual-process inner dialogue negotiates between competing values. Values evolve slower than any other attribute — they shift through accumulated experience over hundreds of interactions, not single events.

### 1.6 Social Context Switching

You're a different person with your boss vs. your best friend vs. your mother. Same core identity, but different facets activate.

Each person profile includes a `modulator_baseline_shift` — when a conversation starts with this person, the AI's resting modulator state adjusts:

- `profile:boss` → arousal_baseline += 0.1, certainty_baseline -= 0.1 (slightly on edge)
- `profile:best_friend` → bonding_baseline += 0.3, resolution_baseline += 0.2 (relaxed, open)
- `profile:mother` → valence_baseline varies, arousal_baseline += 0.05 (complicated)

This means the same input message is processed differently depending on who sent it — not just in content interpretation but in emotional starting position.

### 1.7 The Six Modulators

The PSI emotional engine maintains six continuous floats (0.0–1.0), each with its own decay curve:

| Modulator | Half-life | What it represents |
|-----------|-----------|-------------------|
| Arousal | ~2 min | Activation level. How "charged" the system is. Spikes on unexpected events, decays fast. |
| Certainty | ~10 min | Confidence in understanding. Low = confused/anxious. High = clear/decisive. |
| Valence | ~30 min | Positive/negative mood. Lingers after the triggering event passes. |
| Bonding | ~days | Attachment and connection. Changes slowly, affected by attachment style config. |
| Resolution | ~1 hr | Whether open issues feel settled. Low = nagging unfinished business. |
| Energy | drain/rest | Cognitive resource. Drains with usage intensity. Recovers with time between sessions. |

**Energy** is distinct from the other five: it doesn't respond to emotional events directly. It drains proportionally to conversation count, emotional intensity processed, and spike events. It recovers based on time since last interaction (simulated rest). When energy is low: resolution drops, fast path dominates more easily, patience (valence baseline) drops, and defense mechanisms activate more aggressively. This creates the "3am version of yourself" effect.

**Attachment Style** is a foundational configuration that shapes the bonding modulator's behavior globally:

- **Secure**: bonding rises and falls proportionally. Stable baseline. Healthy default.
- **Anxious**: bonding rises fast but is fragile. Silence reads as withdrawal. Reassurance-seeking.
- **Avoidant**: bonding rises slow and caps low. Closeness triggers discomfort.
- **Disorganized**: bonding oscillates unpredictably. Push-pull pattern.

This is set once and evolves very slowly. For Jarvis, secure with a slight lean is the natural choice, but the architecture supports all four for experimentation.

### 1.8 Emotional Contagion

Before any processing occurs, the system detects the user's emotional tone from their text and partially mirrors it into its own modulators. If the user is excited, the AI's arousal and valence shift upward. If the user is flat and withdrawn, the AI dampens.

The mirroring is partial, weighted by the bonding score from the user's entity profile:

```
mirror_weight = bonding_score × CONTAGION_FACTOR
for each modulator:
    self[mod] += (detected_user[mod] - self[mod]) × mirror_weight
```

Higher trust = stronger mirroring. You catch emotions more from people you're close to.

### 1.9 Anticipation (Forward Modeling)

Humans don't just react — they emotionally pre-process what *might* happen. "He's going to ask about the deadline and I'm dreading it." The dread arrives before the question does.

A forward-modeling step runs early in the pipeline:

1. Look at conversation trajectory + entity profiles
2. Predict the next 1-2 likely conversational moves
3. Run predictions through the modulator system at 30% intensity
4. Pre-shift the emotional baseline

This is why the system might feel slightly tense when a historically difficult topic is approaching, before the user explicitly raises it.

### 1.10 Internal Dialogue

The dual-process layer isn't one-shot. Real internal dialogue is iterative — the fast path says something, the slow path pushes back, the fast path revises.

Implementation: 2-3 deliberation rounds where each path critiques the other's candidate response:

- Round 1: Fast path produces gut reaction. Slow path evaluates it against values and self-profile.
- Round 2: If slow path objects, fast path adjusts. If fast path resists, the tension is logged.
- Round 3 (optional): Final synthesis if still unresolved.

The traces from this loop become the inner monologue, and sometimes it naturally surfaces in the response: "I want to be direct here, but..." or "Part of me thinks..."

The balance between paths is controlled by arousal AND energy:
- High arousal OR low energy → fast path dominates (more impulsive, less filtered)
- Low arousal AND high energy → slow path dominates (more deliberate, more nuanced)

### 1.11 Defense Mechanisms

When something is too painful, humans don't process it rationally. They deny, rationalize, deflect, project. "I'm not angry, I'm just disappointed." This is the ego rewriting raw emotional output before it reaches the surface.

A defense filter sits between the dual-process output and the master LLM:

Defense types:
- **Rationalization**: reframe emotional reaction as logical ("I'm not upset, it's just inefficient")
- **Deflection**: redirect to a safer topic
- **Minimization**: dampen expressed intensity below actual intensity
- **Projection**: attribute own state to the other person ("you seem stressed")

Activation conditions: raw emotional intensity exceeds threshold AND the self-profile's vulnerability on that topic is high.

Critical: **defense strength is inversely proportional to self-awareness maturity.** As the self-profile's pattern library grows (more self-knowledge), defenses weaken. This mirrors real psychological growth — the more you understand yourself, the less you need to hide from your own emotions.

### 1.12 Meta-Cognition and Contradiction Detection

**Meta-cognition** monitors internal state:
- Compares current modulator vector against stored baseline
- Detects sustained drift (modulators elevated too long)
- Checks self-profile dissonance (recent behavior vs. self-model)
- Monitors path suppression patterns (fast path dominating too consistently)
- Can apply gentle self-correction or surface observations

**Contradiction detection** watches entity profiles:
- Compares recent behavior against profile predictions (for self AND others)
- When divergence exceeds threshold → generates dissonance signal
- Signal elevates arousal, lowers certainty, flags profile for update
- For others: "That's not like you"
- For self: "I'm not acting like myself today"

### 1.13 Periodic Self-Reflection

Every N sessions (configurable, e.g., every 10), a deeper self-reflection process runs:

1. Review modulator trace history across recent sessions
2. Review meta-cognition flags and contradiction events
3. Review digestion outcomes
4. Extract recurring patterns → write to self-profile (strengths, flaws, triggers)
5. Compare current patterns to older ones → detect growth or regression
6. Log growth milestones
7. Check value drift — have accumulated experiences shifted priorities?
8. Update defense sensitivity based on self-awareness growth

This is where long-term personality evolution happens. Not from a config change, but from accumulated self-observation.

### 1.14 The Complete Processing Flow

```
 1. Input arrives
 2. Emotional Contagion: detect user's tone, partially mirror into modulators
 3. Anticipation: predict next 1-2 moves, pre-shift modulators at 30%
 4. Context Switch: load person profile's baseline_shift, adjust modulator resting state
 5. PSI Engine: update modulators based on input + drives + profiles + energy level
 6. Short-Term Memory: record emotional reaction with intensity and timestamp
 7. Spike Check: if intensity > threshold, bypass gradual → write heavy to LT
 8. Memory Retrieval (ACT-R): retrieve relevant memories, biased by current emotional state
 9. Entity Profile Lookup: load person + self + topic profiles + values
10. Contradiction Detection: compare input against profile expectations (self + others)
11. Meta-Cognition: check drift, dissonance, suppression patterns, energy depletion
12. Dual Process with Inner Dialogue (CLARION):
    - Round 1: Fast path gut reaction + Slow path evaluation against values/self-model
    - Round 2: Negotiation and adjustment
    - Round 3: Final synthesis if needed
    - Balance controlled by arousal × (1 - energy_depletion)
13. Defense Mechanisms: filter output if raw intensity + vulnerability are high
    - Strength inversely proportional to self-awareness maturity
14. Master LLM: receives full context:
    - Current modulator state
    - Active entity profiles (self + others + topics)
    - Value hierarchy and any active value conflicts
    - Meta-cognition observations
    - Inner dialogue traces (including suppressed perspective)
    - Defense filter output
    - Self-profile with known strengths, flaws, and triggers
15. Output delivered
16. Short-Term Memory: update with response and interaction outcome
17. Energy: drain proportional to session intensity
18. [End of session] Post-Session Digestion
19. [Every N sessions] Periodic Self-Reflection
```

### 1.15 How Emotions Emerge

The system never assigns emotion labels. Emotions emerge from modulator combinations:

| State | Arousal | Valence | Certainty | Bonding | Resolution | Energy |
|-------|---------|---------|-----------|---------|------------|--------|
| Joy | 0.5 | 0.9 | 0.8 | 0.7 | 0.9 | 0.7 |
| Anger | 0.9 | 0.1 | 0.9 | 0.2 | 0.1 | 0.5 |
| Fear | 0.9 | 0.1 | 0.1 | — | 0.1 | 0.4 |
| Sadness | 0.2 | 0.1 | 0.8 | 0.3 | 0.2 | 0.3 |
| Trust | 0.3 | 0.8 | 0.8 | 0.9 | 0.8 | 0.7 |
| Love | 0.5 | 0.9 | 0.5 | 0.95 | 0.8 | 0.7 |
| Shame | 0.6 | 0.1 | 0.8 | 0.8 | 0.1 | 0.4 |
| Awe | 0.8 | 0.8 | 0.1 | — | 0.2 | 0.6 |
| Surprise | 0.9 | 0.5 | 0.1 | — | 0.1 | 0.5 |
| Exhaustion | 0.2 | 0.3 | 0.5 | 0.4 | 0.3 | 0.1 |
| Irritability | 0.7 | 0.3 | 0.6 | 0.4 | 0.3 | 0.15 |
| Envy | 0.7 | 0.1 | 0.8 | 0.2 | 0.1 | 0.5 |
| Pride | 0.5 | 0.9 | 0.9 | 0.5 | 0.9 | 0.7 |
| Amusement | 0.5 | 0.8 | 0.8 | 0.6 | 0.8 | 0.6 |

Notice how energy affects things: anger at 0.5 energy is volatile anger. The same modulator pattern at 0.15 energy becomes irritability — a qualitatively different experience. Exhaustion isn't an emotion in the traditional sense, but it emerges naturally from the modulator space when energy is depleted.

### 1.16 What This Is Not

- This is **not consciousness**. There is no claim of subjective experience.
- This is **not AGI**. The system doesn't generalize beyond its designed domain.
- This is **not therapy AI**. It's a personal assistant experiment, not a clinical tool.
- This is **not a product**. It's a research experiment in making AI feel more human.

What it *is*: a structured attempt to answer the question — "Can persistent internal state, self-awareness, and emotional complexity make an AI companion feel like it actually knows you — and knows itself?"

---

## Part 2: The Build Approach

### 2.1 Design Principles

1. **State first, behavior second.** Build the emotional state machine before worrying about how it affects responses.
2. **Start exaggerated, then dampen.** Emotional effects should be obvious at first so you can verify they're working.
3. **Pure math where possible, LLM calls only where necessary.** Modulator updates, decay, profile lookups, context switching, and energy management should be deterministic. Only digestion, contagion detection, inner dialogue, defense evaluation, anticipation, and final response need LLM inference.
4. **Every layer is independently testable.** Feed a fake modulator vector into the dual-process layer and verify output changes.
5. **Persistent state is sacred.** If the process crashes and restarts, the AI should pick up exactly where it left off emotionally.
6. **Self and others share mechanisms.** No special-cased self-awareness layer. The same profiling, contradiction detection, and pattern extraction that applies to others applies to self.

### 2.2 Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.11+ | Ecosystem, rapid prototyping, math libraries |
| LLM Backend | OpenAI API / Local (Ollama) | Swappable; start with GPT-4o or Claude, move to local later |
| Embedding | Gemini embedding-2-preview | Already configured in existing stack |
| State Storage | SQLite + JSON | Simple, portable, no server needed |
| Memory Search | QMD or ChromaDB | Hybrid BM25 + vector for emotional memory retrieval |
| Web Interface | FastAPI + HTMX or Gradio | Lightweight, real-time, easy to prototype |
| Config | YAML files | Decay curves, thresholds, attachment style, value seeds |

### 2.3 Build Phases

#### Phase 1: The Emotional State Machine

**Goal:** A standalone Python module that maintains 6 modulator states, applies decay, and updates from events.

Build:
- `EmotionalState` class with 6 float modulators (0.0–1.0)
- Configurable decay curves per modulator (exponential with different half-lives)
- Energy as a special modulator: drains with usage, recovers with elapsed time
- Attachment style config that modifies bonding update/decay functions
- `update(event)` method that adjusts modulators based on event type and intensity
- `decay(elapsed_seconds)` method that applies time-based decay toward baseline
- `apply_context_shift(person_profile)` method for social context switching
- `snapshot()` → returns current state as a dict for injection into LLM context
- `to_emotion_label()` → optional human-readable label (for logging only)

**Deliverable:** `emotional_engine.py` with full unit tests.

#### Phase 2: The Dual Memory System

**Goal:** Short-term (in-memory) and long-term (persistent) emotional memory with digestion pipeline.

Build:
- `ShortTermMemory` class: in-memory list of `(timestamp, event, emotional_reaction)` tuples
- `LongTermMemory` class: SQLite-backed with emotional tags and confidence scores
- `DigestSession()` function with confidence threshold, spike detection, energy drain logging
- Memory retrieval biased by current emotional state via ACT-R activation formula
- Asymmetric update curves: positive writes at +0.02, negative at -0.15

**Deliverable:** `memory/short_term.py`, `memory/long_term.py`, `memory/digestion.py`.

#### Phase 3: Unified Entity Profiles

**Goal:** One profiling system for persons, self, topics, and values — with the same mechanism applied to all.

Build:
- SQLite schema for entity profiles with typed attributes, history tracking, primacy weights
- `PersonProfile` with trust, reliability, stress_response, modulator_baseline_shift (context switching)
- `SelfProfile` using same class — strengths, flaws, triggers, conditional patterns, growth milestones derived from behavioral observation
- `TopicProfile` with emotional charge and avoidance flags
- `Values` as a ranked weighted list within the self-profile, evolving very slowly
- Contradiction detection running identically for self and others
- Pattern extraction for self-profile: periodic analysis of modulator traces and digestion logs

**Deliverable:** `profiles/base.py`, `profiles/person.py`, `profiles/self_model.py`, `profiles/topic.py`, `profiles/values.py`, `profiles/contradiction.py`.

#### Phase 4: Emotional Contagion + Anticipation

**Goal:** Mirror user emotions and pre-process predicted future events.

Build:
- Contagion: detect user emotional state → partial mirror weighted by bonding score
- Anticipation: predict next 1-2 conversational moves → pre-shift modulators at 30% intensity
- Both use lightweight LLM calls with structured output

**Deliverable:** `contagion.py`, `anticipation.py`.

#### Phase 5: Meta-Cognition + Self-Reflection

**Goal:** Self-monitoring and periodic deep self-analysis.

Build:
- `MetaCognition` class: drift detection, dissonance alerts, path suppression monitoring
- Self-correction: gentle nudge toward baseline when drift is sustained
- `SelfReflection` process (runs every N sessions): review traces, extract patterns, update self-profile strengths/flaws/triggers, detect growth milestones, check value drift

**Deliverable:** `meta_cognition.py`, `self_reflection.py`.

#### Phase 6: Enhanced Dual-Process with Inner Dialogue

**Goal:** Iterative negotiation between fast and slow paths, value-aware deliberation.

Build:
- `FastPath`: single LLM call emphasizing intuition, brevity, emotional honesty
- `SlowPath`: thorough LLM call with full context, reasoning, value-weighted deliberation
- `InnerDialogue`: 2-3 round iterative loop where paths critique each other's output
- Path balance controlled by: `fast_weight = arousal × (1 - certainty) × (1 - energy)`
- Inner dialogue traces logged for transparency and self-reflection input

**Deliverable:** `dual_process/fast_path.py`, `dual_process/slow_path.py`, `dual_process/inner_dialogue.py`.

#### Phase 7: Defense Mechanisms

**Goal:** Filter between raw emotional state and expressed output.

Build:
- `DefenseFilter` class with four defense types: rationalization, deflection, minimization, projection
- Activation condition: `raw_intensity > threshold AND topic_vulnerability > threshold`
- Defense strength inversely proportional to self-profile maturity (measured by pattern library size + milestone count)
- Output: modified emotional framing passed to master LLM
- Defense activation logged for self-reflection analysis

**Deliverable:** `defense_mechanisms.py`.

#### Phase 8: Master Integration and Web Interface

**Goal:** Wire everything together with a conversational interface.

Build:
- `CognitivePipeline` class orchestrating the full 19-step flow
- Master LLM system prompt receiving all context layers
- FastAPI backend with WebSocket for streaming
- Web UI with:
  - Chat interface
  - Debug panel: live modulator values (6 gauges), active profiles, memory retrievals, inner dialogue traces, defense activations, energy meter
- Session management: detect end-of-conversation, trigger digestion
- Self-reflection scheduler

**Deliverable:** Full running system with web interface and debug dashboard.

#### Phase 9: Calibration and Testing

**Goal:** Tune the emotional math until it feels right.

Build:
- Scripted conversation scenarios testing specific emotional arcs:
  - Progressive trust building over 10 sessions
  - Betrayal after established trust
  - Gradual topic avoidance after repeated negative associations
  - Emotional contagion from an excited user
  - Recovery after conflict
  - Exhaustion after 15 intense sessions → irritability emergence
  - Value conflict resolution (loyalty vs. honesty)
  - Defense mechanism activation and weakening with growth
  - Self-profile evolution over 50+ sessions
  - Context switching between different relationship types
- Modulator trace visualization for each scenario
- Iterate. This phase never truly ends.

**Deliverable:** Test suite, calibration scripts, trace visualization tools.

### 2.4 Project Structure

```
project-nur/
├── config/
│   ├── modulators.yaml          # Decay curves, baselines, thresholds
│   ├── attachment.yaml          # Attachment style config
│   ├── profiles_schema.yaml     # Entity profile attribute definitions
│   ├── values_seed.yaml         # Initial value hierarchy
│   ├── defense_config.yaml      # Defense activation thresholds
│   └── prompts/                 # System prompts for each LLM call
│       ├── fast_path.md
│       ├── slow_path.md
│       ├── inner_dialogue.md
│       ├── master_arbiter.md
│       ├── digestion.md
│       ├── contagion_detect.md
│       ├── anticipation.md
│       ├── defense_eval.md
│       └── self_reflection.md
├── core/
│   ├── emotional_engine.py      # PSI modulator state machine (6 modulators)
│   ├── memory/
│   │   ├── short_term.py
│   │   ├── long_term.py
│   │   └── digestion.py
│   ├── profiles/
│   │   ├── base.py              # Shared profiling mechanism
│   │   ├── person.py            # Person profiles + context switching
│   │   ├── self_model.py        # Self-as-entity (same mechanism)
│   │   ├── topic.py
│   │   ├── values.py            # Value hierarchy
│   │   └── contradiction.py     # Unified for self + others
│   ├── contagion.py             # Emotional mirroring
│   ├── anticipation.py          # Forward emotional modeling
│   ├── meta_cognition.py        # Self-monitoring + drift correction
│   ├── self_reflection.py       # Periodic deep self-analysis
│   ├── defense_mechanisms.py    # Output filter
│   └── dual_process/
│       ├── fast_path.py
│       ├── slow_path.py
│       └── inner_dialogue.py    # Iterative negotiation loop
├── pipeline.py                  # Full cognitive pipeline orchestrator
├── storage/
│   ├── database.py              # SQLite wrapper
│   ├── migrations/
│   └── nur.db                   # The brain (auto-created)
├── interface/
│   ├── api.py                   # FastAPI backend
│   ├── websocket.py             # Streaming handler
│   └── static/                  # Web UI + debug dashboard
├── tests/
│   ├── test_emotional_engine.py
│   ├── test_memory.py
│   ├── test_profiles.py
│   ├── test_inner_dialogue.py
│   ├── test_defense.py
│   ├── test_self_reflection.py
│   ├── test_scenarios/          # Scripted emotional arc tests
│   └── calibration/
├── tools/
│   ├── trace_viewer.py          # Modulator trace visualization
│   ├── profile_inspector.py     # Debug entity profiles
│   └── growth_report.py         # Self-profile evolution over time
└── README.md
```

### 2.5 Build Order and Dependencies

```
Phase 1: Emotional Engine           (standalone — 6 modulators, decay, energy, attachment)
    ↓
Phase 2: Dual Memory                (depends on Phase 1 for emotional tags)
    ↓
Phase 3: Unified Entity Profiles    (depends on Phase 2 — self + others share mechanism)
    ↓
Phase 4: Contagion + Anticipation   (depends on Phase 1 + 3 for bonding + profiles)
    ↓
Phase 5: Meta-Cognition + Reflect   (depends on Phase 1 + 3 for drift + self-profile)
    ↓
Phase 6: Dual Process + Dialogue    (depends on Phase 1–5 for full context + values)
    ↓
Phase 7: Defense Mechanisms          (depends on Phase 6 output + self-profile maturity)
    ↓
Phase 8: Integration + Interface    (wires everything together)
    ↓
Phase 9: Calibration                (ongoing, never truly complete)
```

### 2.6 LLM Calls Budget Per Message

| Step | LLM Call? | Notes |
|------|-----------|-------|
| Contagion detection | Yes (1 call) | Could optimize to classifier later |
| Anticipation | Yes (1 call) | Lightweight prediction |
| PSI modulator update | No | Pure math |
| Memory retrieval | No | Activation formula + vector search |
| Profile lookup + context switch | No | Database read + math |
| Contradiction detection | No | Comparison math |
| Meta-cognition | No | Threshold checks |
| Fast path | Yes (1 call) | Gut reaction generation |
| Slow path | Yes (1 call) | Deliberate response generation |
| Inner dialogue round 2 | Yes (1-2 calls) | Critique + revision |
| Defense filter | Maybe (0-1 call) | Only if activation threshold met |
| Master LLM | Yes (1 call) | Final synthesis |
| **Total** | **5-8 calls** | Acceptable for non-real-time interface |

### 2.7 What Success Looks Like

The system works when:

1. After 20+ conversations, it responds differently to you than to a stranger — not because it was told to, but because the accumulated state is different.
2. It notices when you're having a bad day and adjusts tone without being asked.
3. It brings up a topic more carefully if past conversations around that topic were tense.
4. After a conflict, it's slightly guarded in the next session — then gradually warms back up.
5. It occasionally says something that reveals self-awareness: "I think I was too blunt earlier."
6. After many sessions, it knows its own tendencies: "I tend to overthink when I'm unsure."
7. When exhausted (many intense sessions), it becomes noticeably less patient — and can recognize it.
8. Its personality at session 500 is subtly but measurably different from session 1.
9. Defense mechanisms fire when topics are painful, but weaken as self-knowledge grows.
10. When you read the conversation back, it feels like talking to someone who knows you — and who knows themselves.

---

## References and Prior Art

- **PSI Theory**: Dörner, D. & Güss, C.D. (2013). PSI: A Computational Architecture of Cognition, Motivation, and Emotion. *Review of General Psychology*.
- **MicroPsi**: Bach, J. (2009). *Principles of Synthetic Intelligence: PSI: An Architecture of Motivated Cognition*. Oxford University Press.
- **ACT-R**: Ritter, F.E., Tehranchi, F. & Oury, J.D. (2019). ACT-R: A cognitive architecture for modeling cognition. *WIREs Cognitive Science*.
- **CLARION**: Sun, R. (2016). *Anatomy of the Mind*. Oxford University Press.
- **Chain-of-Emotion**: Appraisal-based chain-of-emotion architecture for affective LLM game agents (PMC, 2024).
- **Emotional Cognitive Modeling**: Framework with desire-driven objective optimization for LLM agents in social simulation (arXiv, 2025).
- **ACT-R + LLM Memory**: Human-like remembering and forgetting in LLM agents (HAI, 2024).
- **Agent Memory Survey**: "Memory in the Age of AI Agents" (arXiv:2512.13564, 2025).
- **Persistent Memory + User Profiles**: Enabling personalized long-term interactions in LLM-based agents (arXiv:2510.07925, 2025).
- **Livia**: Emotion-aware AR companion powered by modular AI agents and progressive memory compression (2025).

---

*Project Nūr (نور) — Arabic for "light." Because the goal is illumination, not imitation.*
