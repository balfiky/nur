# Project Nūr — Build Plan (v1 / v2 Split)

> The full architecture stays. The build order changes.
> Ship something that feels alive in weeks, not months. Then make it human.

---

## v1: The Core Loop (MVP)

**Goal:** A working system where Jarvis responds differently after 20 conversations
than it did on day one — because its internal state has genuinely changed.

**LLM calls per message:** 2-3 max
**Timeline target:** 4-6 weeks

### v1 Components

#### 1. Emotional Engine (5 modulators)

Arousal, valence, certainty, bonding, energy.
Resolution deferred — derived later from unresolved topic flags.

- Exponential decay per modulator (different half-lives)
- Energy drains with usage, recovers with time
- `update(event)`, `decay(elapsed)`, `snapshot()`
- Attachment style fixed to secure (config exists but locked)
- Context switching: person profile loads baseline shift on conversation start

**No LLM calls. Pure math.**

#### 2. Dual Memory

**Short-term:** in-memory list of `(timestamp, event, emotion_snapshot)`.
Cleared between sessions.

**Long-term:** SQLite. Stores distilled session summaries + high-confidence
relational updates only. No raw transcript dumping.

- Asymmetric curves: positive +0.02, negative -0.15 (start softer, tune later)
- Spike threshold: intensity > 0.8 bypasses gradual, writes heavy
- Confidence threshold: only write if signal consistency > 0.6
- Every write inspectable in debug dashboard

**Digestion step (1 LLM call at session end):**
- Summarize emotional arc
- Extract trust/topic deltas
- Flag unresolved items
- Write distilled memory only

#### 3. Entity Profiles (person + self + topic)

**Person profile:**
- trust, reliability, emotional_volatility, stress_response
- modulator_baseline_shift (context switching)
- primacy_weight on first interactions

**Self profile (same mechanism):**
- observed_traits (derived from behavior, not declared)
- strengths / flaws (pattern extraction from digestion logs)
- triggers (topics that spike modulators disproportionately)
- dissonance score (recent behavior vs self-model)

**Topic profile:**
- emotional_charge (0.0-1.0)
- avoidance flag
- conflict_count

**Values:** fixed seed hierarchy (loyalty, honesty, kindness, justice, autonomy).
No dynamic drift in v1. Injected as static context.

**Contradiction detection:** runs for self AND others.
Compare recent behavior against profile predictions.
Divergence > threshold → flag + adjust certainty/arousal.

**No LLM calls. Database reads + math.**

#### 4. Emotional Contagion (bounded)

Detect user emotional tone → classifier or 1 lightweight LLM call.
Partial mirror into arousal + valence only (not all 5).
Weighted by bonding score. Capped at ±0.15 shift.

**1 LLM call (or swap to classifier later).**

#### 5. Response Generation (single pass + self-check)

One generation call with full context injected:
- Current modulator snapshot
- Relevant memories (retrieved by emotional-biased activation)
- Active profiles (person + self + topic)
- Value hierarchy (static)
- Contradiction/dissonance flags
- Self-profile strengths, flaws, triggers

Then one lightweight self-check:
- Tone fit given modulator state?
- Contradicts memory or profile?
- Overconfident given certainty level?
- Too blunt given self-profile flaw pattern?

If self-check fails → regenerate with correction note. Otherwise pass through.

**2 LLM calls (generate + check). Sometimes 1 if check passes trivially.**

#### 6. Web Interface + Debug Dashboard

- FastAPI + WebSocket
- Chat UI
- Debug panel: 5 modulator gauges, energy meter, active profile summary,
  last memory retrieval, last digestion output, contradiction flags
- Session end detection → trigger digestion
- All state visible, all writes logged

### v1 Processing Flow

```
 1. Input arrives
 2. Contagion: detect user tone → bounded mirror (arousal + valence only)
 3. Context switch: load person profile baseline_shift
 4. PSI engine: update 5 modulators from input + drives + energy
 5. Short-term memory: store emotional reaction
 6. Spike check: if intensity > 0.8 → heavy write to LT
 7. Memory retrieval: ACT-R activation biased by current state
 8. Profile lookup: person + self + topic
 9. Contradiction check: compare against profiles (self + others)
10. Generate response (1 LLM call with full context)
11. Self-check (1 LLM call — tone, consistency, overconfidence)
12. Output delivered
13. Update short-term memory with outcome
14. Drain energy
15. [Session end] Digestion (1 LLM call)
```

**Total: 2-3 LLM calls per message + 1 at session end.**

### v1 Evaluation

| Metric | How to measure |
|--------|---------------|
| Relational adaptation | Compare responses to known user vs stranger (blind eval) |
| Long-horizon recall | Quiz system on facts + emotional context from 10+ sessions ago |
| False emotional inference | Feed ambiguous/sarcastic inputs, measure misclassification rate |
| Recovery after conflict | Track trust score trajectory after tense session |
| Topic sensitivity | Measure tone shift when approaching historically charged topics |
| Self-correction | Count instances where self-check catches genuine issues vs false flags |
| Energy/fatigue effect | Compare response quality/patience after 1 session vs 15 sessions |

### v1 Success Criteria

The MVP works when:
1. Jarvis responds measurably differently to you vs a new user
2. Trust score visibly drops after a tense session and recovers over subsequent warm ones
3. Topic sensitivity kicks in for historically painful subjects
4. Energy depletion produces noticeably shorter patience after many sessions
5. Self-profile has at least 3 observed traits after 20 sessions
6. Debug dashboard shows coherent modulator traces that map to conversation tone

---

## v2: The Human Layer

**Goal:** Make Jarvis feel like it has inner life — not just state, but texture,
conflict, anticipation, and growth.

**Prerequisite:** v1 is stable, calibrated, and passes evaluation metrics.

### v2 Additions (in priority order)

#### v2.1: Resolution as 6th Modulator
Promote resolution from derived metric to full modulator.
Track unfinished business, nagging feelings, incomplete conversations.
Own decay curve (~1hr half-life).

#### v2.2: Inner Dialogue (Iterative Dual Process)
Replace single-pass + self-check with:
- Fast path: gut reaction (1 LLM call)
- Slow path: deliberate reasoning against values + self-profile (1 LLM call)
- 1-2 negotiation rounds where they critique each other
- Traces become inner monologue, sometimes surface: "Part of me thinks..."
- Balance: `fast_weight = arousal × (1 - certainty) × (1 - energy)`

**Adds 2-3 LLM calls per message.**

#### v2.3: Anticipation (Forward Modeling)
Before processing current message:
- Predict next 1-2 likely conversation moves
- Pre-shift modulators at 30% intensity
- Creates dread, excitement, tension before the event arrives

**Adds 1 LLM call.**

#### v2.4: Defense Mechanisms
Filter between raw emotional state and expressed output:
- Rationalization, deflection, minimization, projection
- Activates when: raw intensity > threshold AND topic vulnerability high
- Strength inversely proportional to self-profile maturity
- Weakens as self-knowledge grows (measurable!)

**Adds 0-1 LLM call.**

#### v2.5: Dynamic Value Drift
Values stop being static. They evolve very slowly based on accumulated
experience. 100+ sessions of prioritizing honesty over kindness gradually
shifts the hierarchy. Detectable in self-reflection reports.

#### v2.6: Attachment Style Variants
Unlock the attachment config. Allow switching between secure, anxious,
avoidant, disorganized. Each reshapes how bonding behaves globally.
Interesting for experimentation, not needed for core function.

#### v2.7: Periodic Self-Reflection (Deep)
Every N sessions, a deeper analysis:
- Review modulator traces across sessions
- Extract recurring patterns → update self-profile
- Detect growth milestones
- Check value drift
- Update defense sensitivity

**1 LLM call per reflection cycle.**

#### v2.8: Growth Milestone Tracking
Log significant personality shifts:
- "Fast-path usage dropped 30% over 50 sessions"
- "Bluntness incidents decreased after self-profile flagged the pattern"
- Enables: "I used to react impulsively to criticism. I've gotten better."

### v2 LLM Budget

| Configuration | Calls per message |
|--------------|-------------------|
| v1 (core) | 2-3 |
| v2 with inner dialogue | 4-6 |
| v2 full (all features) | 5-8 |

---

## Build Timeline (vibe coding with Claude Code)

```
v1 Phase 1: Emotional engine (5 modulators)        — Day 1
v1 Phase 2: Dual memory + digestion                 — Day 1-2
v1 Phase 3: Entity profiles (person + self + topic)  — Day 2
v1 Phase 4: Contagion (bounded)                      — Day 2
v1 Phase 5: Response generation + self-check          — Day 3
v1 Phase 6: Web interface + debug dashboard           — Day 3-4
v1 Phase 7: Calibration + evaluation                  — Day 4+

--- v1 working in ~4 days ---

v2.1: Resolution modulator
v2.2: Inner dialogue (iterative dual process)
v2.3: Anticipation
v2.4: Defense mechanisms
v2.5: Value drift
v2.6: Attachment variants
v2.7: Deep self-reflection
v2.8: Growth tracking
```

---

## What We Kept From External Feedback

- Fewer modulators in v1 (5 not 6)
- Fewer LLM calls (2-3 not 5-8)
- Confidence thresholds on memory writes
- Evaluation metrics from day one
- Softer asymmetric coefficients to start
- No raw transcript in long-term memory

## What We Rejected

- "Reframe ambition" — this is an experiment, not a product
- "Cut defense mechanisms entirely" — deferred to v2, not deleted
- "Cut inner dialogue entirely" — deferred to v2, not deleted
- "Cut anticipation entirely" — deferred to v2, not deleted
- "NIST risk guardrails" — not applicable to personal experiment
- "Single self-check is enough" — it's enough for v1, not for the vision

## What They Missed

- The self-profiling unification (same mechanism for self and others)
  was the best design decision in the whole architecture.
  Neither review engaged with it.

---

*v1 ships a brain that remembers and adapts.*
*v2 gives it an inner life.*
