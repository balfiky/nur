# Changelog

All notable changes to Project Nur are documented here.

---

## v0.2.2 — 2026-04-01 (Second-pass fixes 6-11)

Second round of fixes from review. Primacy, dedup, labeling, contagion signals.

### Fix 6: Self-check retry preserves v2 steering
- Retry path now carries `candidate_response` and `defense_instruction` into correction context
- Ensures regeneration after self-check failure still uses inner dialogue output

### Fix 7: Primacy weighting corrected
- Early (primacy) observations get weight 1.0, later ones dampened by `primacy_weight` (e.g. 0.8)
- Was inverted: primacy observations were being dampened instead
- `extract_traits()` default changed from `PRIMACY_DEFAULT` (0.8) to 1.0 (no dampening unless explicit)
- `PRIMACY_DECAY_PER_INTERACTION` and `PRIMACY_FLOOR` now config-driven in person profiles

### Fix 8: Contradiction deduplication
- `_check_contradiction_resolution()` tracks `active_descs` set to prevent duplicate unresolved items
- Same contradiction text no longer creates multiple entries

### Fix 9: Round-1 dominant_path label
- Round-1 approval now always returns `dominant_path="fast"` (the unmodified fast-path output)

### Fix 10: Rule-based contagion signals
- `_detect_via_rules()` now computes meaningful `certainty` and `intensity`
- Certainty: 0.8 if all keywords agree on direction, 0.4 if mixed, 0.6 single keyword, 0.3 no keywords
- Intensity: `avg_extremity * min(match_count, 3) / 3.0`

### Fix 11: Generator output contract
- generator.md: added "Return ONLY the final response text" rule
- v2 prompt templates: added output-shape constraints

### Testing
- 493 tests total (8 new regression tests in test_regressions.py)
- Zero regressions

---

## v0.2.1 — 2026-04-01 (Second-pass fixes 1-5)

Fixes from SECOND_PASS_REVIEW.md phases 0-5. Makes v2 behaviorally real.

### Fix 1: v2 controls the response
- Added `candidate_response` and `defense_instruction` to PipelineContext
- Generator now receives inner dialogue candidate as draft to refine
- Defense instruction injected directly (not via contradiction_flags hack)
- generator.md updated with `{candidate_response}` and `{defense_instruction}` placeholders

### Fix 2: Time and context semantics
- Pipeline tracks `_last_turn_time`, calls `decay(elapsed)` at start of each `process()`
- Context shift is now non-additive: `set_context_shift()` stores resting target offset
- Decay uses `effective_baseline()` (baseline + context shift) instead of raw baseline
- Repeated turns with same person no longer ratchet modulators upward

### Fix 3: Self-model persistence
- Pipeline records 1-3 self-observations per turn (blunt, empathetic, defensive, avoidant)
- Defense events persisted to SQLite `defense_events` table via ProfileStore
- `maturity_score` derived from observation count + flaw diversity + defense event count
- `get_profile()` now returns defense_log from persistent storage

### Fix 4: Trust and resolution accounting
- Removed sign-only session-end trust update (per-turn trust is authoritative)
- Resolution uses item intensity only — removed age-based `_time_weight` double-decay
- Contradictions now create unresolved items (source="contradiction")
- Charged/avoidance topics now create unresolved items (source="topic")
- Resolution events match items by text overlap before falling back to oldest

### Fix 5: Prompt contracts and parsing
- fast_path.md, fast_path_revision.md, arbiter.md: added output-shape constraints
- Unparseable slow-path output triggers retry once, then treated as objection (not auto-approve)
- `parse_slow_path_response()` returns 3-tuple with `parsed_successfully` flag
- v2 prompts (fast_path, slow_path, revision, arbiter) loaded through config.loader
- Fixed `SelfModelConfig.negative_traits` AttributeError on partial config

### Testing
- 485 tests total (9 new regression tests + updated existing tests)
- Zero regressions from v0.2.0

---

## v0.2.0 — 2026-04-01 (v2: The Inner Life)

v2 adds deliberation, dread, and self-protection. Four interconnected features that give the system an inner life.

### Resolution Modulator (Phase 1)
- 6th modulator tracking unresolved cognitive/emotional tension
- Per-source decay rates: contradictions (0.02/hr), topics (0.05/hr), commitments (0/hr), spikes (0.03/hr), deadlocks (0.10/hr)
- Item-level tracking: add, resolve, recalculate weighted sum
- Integrated into EmotionalEngine snapshot and decay cycles

### Anticipation Engine (Phase 2)
- Forward emotional modeling — predicts before processing
- 4 pure heuristics (zero LLM calls): topic trajectory, person patterns, unresolved aging, temporal patterns
- 30% intensity cap on pre-shifts, 0.3 confidence gate
- Sensitive topic detection from 2+ mentions in last 3 messages

### Inner Dialogue (Phase 3)
- Iterative fast/slow path deliberation (2-3 rounds)
- Fast path: gut reaction based on emotional state
- Slow path: reflective evaluation against values, self-profile, unresolved items
- Arbiter on round 3 deadlock (logged as unresolved item)
- Control dynamics: arousal bypass (>0.8), energy bypass (<0.2), resolution insistence (>0.6)
- 4 prompt templates: fast_path.md, slow_path.md, fast_path_revision.md, arbiter.md

### Defense Mechanisms (Phase 4)
- 4 types: rationalization, deflection, minimization, projection
- Pure logic selection + prompt instruction injection (zero LLM calls)
- Comfort threshold: 0.5 + trust×0.2 + maturity×0.2 + bonding×0.1
- Suppression factor degrades with maturity (never fully gone)
- Defense events logged in self-profile for pattern detection

### Pipeline v2 Flow (Phase 5)
- Full v2 processing: anticipation → contagion → event → resolution → inner dialogue → defense → master LLM
- Debug payload includes all v2 layers (anticipation, dialogue_trace, defense_activation, unresolved_count)
- v1 behavior fully preserved
- LLM call budget: 5-9 per message (typical: 6)

### New Types
- UnresolvedItem, Anticipation, DialogueRound, InnerDialogueTrace, DefenseActivation, DefenseEvent
- ModulatorName.RESOLUTION, resolution field in ModulatorState
- maturity_score and defense_log in SelfProfile

### Debug Dashboard (Phase 6)
- Resolution gauge (6th modulator, orange)
- Anticipation section: predicted topics, tone, confidence, pre-shifts, basis
- Inner Dialogue section: rounds with fast/slow candidates, approval/objection status, tension meter, dominant path, LLM call count
- Defense section: type, raw vs expressed intensity bars, suppression delta, reason
- Unresolved Items section: source tags, descriptions, intensity, decay rate
- `/debug` endpoint extended with resolution, unresolved_count, unresolved_items
- `/chat` debug payload serializes all v2 fields (anticipation, dialogue_trace, defense_activation, unresolved_items)

### Calibration Scenarios (Phase 7)
- test_v2_scenarios.py with 30 scripted calibration tests across 5 scenarios
- Deflection under low trust: arousal + trust thresholds, instruction injection, pipeline integration
- Inner dialogue disagreement: multi-round objection/approval, slow path unresolved references, deadlock → unresolved item, bypass overrides
- Anticipation pre-shift: topic trajectory detection, 30% cap verified, confidence gating, pipeline wiring
- Defense degradation: monotonic suppression decrease across all 4 types at maturity 0.0/0.25/0.5/0.75/1.0, expressed intensity grows, suppression delta shrinks, defense logging
- Full pipeline end-to-end: all debug fields, LLM budget, session arc, defense under pressure, v1 preserved

### Testing
- 476 tests total (188 new v2 tests + 288 v1 preserved)
- test_resolution.py (19), test_anticipation.py (27), test_inner_dialogue.py (38), test_defense_mechanisms.py (36), test_pipeline_v2.py (29), test_interface.py v2 (9), test_v2_scenarios.py (30)
- Zero v1 regressions

---

## v0.1.0 — 2026-04-01 (v1 Complete)

First complete version. All v1 systems built, tested, and wired together.

### Core Engine
- 5-modulator PSI state machine (arousal, valence, certainty, bonding, energy)
- Exponential decay with configurable half-lives per modulator
- Energy drain per message + spike events, recovery with rest
- Attachment style support (locked to secure in v1)
- Context switching via per-person baseline_shift

### Memory
- Short-term: in-memory event buffer with emotional arc tracking
- Long-term: SQLite-backed with ACT-R activation retrieval (recency x frequency x emotional bias)
- Asymmetric trust curves: +0.02 positive, -0.15 negative (7.5x negativity bias)
- Spike bypass: intensity >= 0.8 writes directly to long-term
- Confidence threshold: only write if signal consistency >= 0.6
- Session digestion: LLM summarization with heuristic fallback

### Profiles
- Person profiles: trust, reliability, emotional_volatility, stress_response, baseline_shift, primacy_weight
- Self profile: observed_traits, strengths, flaws, triggers, dissonance (same mechanism as person profiles, entity ID `__self__`)
- Topic profiles: emotional_charge, avoidance flags, conflict_count (asymmetric charge updates)
- Contradiction detection: unified for self and others, divergence threshold from config

### Contagion
- Emotional detection from user text (LLM + 50-pattern rule-based fallback)
- Returns arousal, valence, certainty, intensity
- Bounded mirroring: max +/-0.15 shift, weighted by bonding score

### Dual Process
- Response generation: single LLM call with full emotional context (Jarvis personality)
- Self-check: rule-based (tone fit, overconfidence, bluntness, energy, contradictions) + optional LLM
- Regeneration with correction note on self-check failure

### Pipeline
- 15-step cognitive processing flow
- Event classification: LLM with keyword fallback (10 event types)
- Topic detection: LLM with substring fallback
- Per-message trust updates based on event valence
- Full debug state transparency (DebugState dataclass)

### Interface
- FastAPI REST API: POST /chat, GET /debug, POST /session/end, POST /rest
- WebSocket /ws for streaming chat
- Web UI: chat panel + debug dashboard (5 modulator gauges, energy meter, profiles, memories, contradictions)

### Configuration
- YAML-driven: modulators.yaml, attachment.yaml, profiles_schema.yaml, values_seed.yaml
- 6 prompt templates: generator.md, self_check.md, digestion.md, contagion.md, classify_event.md, detect_topics.md
- Singleton config loader with typed dataclasses
- All constants configurable, no hardcoded thresholds in module code

### LLM Integration
- MiniMax M2.7-highspeed client (OpenAI-compatible API)
- `<think>...</think>` tag stripping for M2.7 reasoning output
- LLMBackend protocol for swappable backends
- Graceful degradation: all LLM functions fall back to rule-based on failure

### Testing
- 288 tests total
- Unit tests for every module
- Calibration scenarios: trust building, betrayal, topic avoidance, contagion, conflict recovery, energy depletion
- End-to-end emotional journey: 10 scenarios testing full pipeline behavior
- All tests work without API key (MockLLMBackend triggers rule-based fallbacks)

### Infrastructure
- GitHub repo: github.com/balfiky/nur (private)
- pyproject.toml with dev dependencies (pytest, ruff)
- .gitignore for Python, SQLite, caches
