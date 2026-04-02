# Changelog

All notable changes to Project Nur are documented here.

---

## v0.8.0 — 2026-04-02 (Phase 4: Runtime debug API)

Session-aware debug API replacing the old single-pipeline debug model.

### Debug API (`runtime/debug/api.py`)
- `GET /sessions` — list active sessions with rel_key, user_id, idle_seconds, queue_size, has_debug
- `GET /sessions/{rel_key}/debug` — per-user debug: live modulators, emotion label, memory counts, unresolved items, full last_turn debug snapshot
- `POST /sessions/{rel_key}/reset` — evict session (digest + persist + close)
- Reads from SessionManager — no separate pipeline, no global state
- 404 for non-existent sessions on both debug and reset endpoints

### Debug state capture (`runtime/sessions/user_session.py`)
- `last_debug: DebugState` stored on UserSession after each `process()` call
- Full v1/v2 debug fields preserved in serialization: contagion, event classification, profiles, contradictions, anticipation, inner dialogue trace, defense activation, stage timings

### Config + wiring
- `RuntimeConfig` gains `debug_host` (default `127.0.0.1`) and `debug_port` (default `8077`)
- `runtime_config.yaml` updated with debug section
- Debug server runs as background uvicorn task in JarvisApp
- Graceful shutdown stops debug server alongside channels

### Testing
- 617 tests total (15 new debug API tests)
- `TestSessionListing` (4): empty, lists active, field validation, has_debug flag
- `TestPerUserDebug` (5): live state, last_turn, v2 fields, 404, modulator reflection
- `TestPerUserReset` (3): eviction, 404, removed from listing
- `TestSessionIsolation` (3): different states, reset isolation, correct last_turn per user
- Zero regressions

---

## v0.7.0 — 2026-04-02 (Phase 3: Timeouts, shutdown, DB safety)

Timer-driven inactivity, graceful shutdown, and shared DB hardening.

### Timer-driven inactivity timeout (`runtime/sessions/manager.py`)
- Per-session idle timer via `loop.call_later` — fires eviction automatically
- No dependence on future incoming messages (spec requirement)
- Timer resets on each message completion
- Each session has its own independent timer
- Eviction on timeout: digest session, save state, close pipeline

### Graceful shutdown
- `_accepting` flag: set to False on shutdown, rejects new messages immediately
- Shutdown sequence: stop intake → cancel all idle timers → drain active queues → persist state → close
- In-progress pipeline work completes before session close (no data loss)

### Backpressure enforcement
- `max_queue_per_user`: raises RuntimeError with "queue full" when exceeded
- `max_active_sessions`: raises RuntimeError with "limit reached" for new users at capacity
- Existing users with active sessions are never blocked by session limit
- Evicting a session frees the slot for new users

### Shared DB WAL mode + busy timeout (`core/profiles/base.py`)
- `ProfileStore` gains `wal_mode: bool = False` parameter
- When `wal_mode=True`: `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=5000`
- Pipeline enables WAL automatically when `self_db_path` is provided (shared mode)
- Per-user DBs remain on default journal mode (single writer, no contention)
- In-memory DBs ignore WAL flag

### Unresolved items — in-memory only
- Unresolved items are NOT persisted in engine_state.json (by design)
- They reset on session restart — documented explicitly per spec requirement

### Testing
- 602 tests total (20 new Phase 3 tests, 1 updated)
- `TestInactivityTimeout` (4): auto-eviction, state persistence, timer reset, independent timers
- `TestGracefulShutdown` (5): persist all, stop intake, evict all, cancel timers, drain in-progress
- `TestBackpressure` (4): queue full, session limit, eviction frees slot, existing user not blocked
- `TestSharedDBSafety` (6): WAL enabled, busy timeout set, no WAL default, memory ignores WAL, pipeline shared WAL, concurrent writes
- `TestUnresolvedItemsPersistence` (1): not in saved state
- Updated `test_evict_idle` — now verifies timer-driven eviction (not passive sweep)
- Zero regressions

---

## v0.6.0 — 2026-04-02 (Phase 2: Telegram channel)

Telegram adapter with allowlist, dedupe, typing indicators, and commands.

### Telegram channel (`runtime/channels/telegram.py`)
- Long-polling loop via httpx (`TelegramClient`)
- Allowlist by numeric Telegram user ID (empty = allow all)
- Per-update dedupe with TTL cache (`DedupeCache`) — prevents duplicate state updates
- Typing indicators resent every 4 s while Nūr processes, cancelled on response
- Text messages only — photos/stickers/etc silently ignored
- Commands: `/status` (show modulators), `/reset` (digest + evict session), `/debug` (placeholder), `/start` (greeting)
- Bot-name suffix stripped from commands (`/status@MyBot` → `/status`)

### Config updates
- `RuntimeConfig` gains `telegram_token`, `telegram_allowlist`, `telegram_poll_timeout`, `dedupe_ttl`
- `TelegramConfig` dataclass for channel-specific settings
- `runtime_config.yaml` updated with Telegram section

### App wiring (`runtime/app.py`)
- Telegram channel starts as background task if `telegram_token` is set
- Graceful shutdown stops Telegram polling + console + all sessions

### Testing
- 582 tests total (26 new Telegram tests)
- `TestDedupeCache` (5): TTL expiry, mark/check, independence
- `TestMessageNormalization` (5): text extraction, non-text ignored, missing fields, user_id as string
- `TestAllowlist` (3): allowed/rejected users, empty allowlist
- `TestDedupe` (3): duplicate dropped, different IDs processed, TTL expiry allows reprocessing
- `TestCommands` (7): /status with/without session, /reset evicts, /debug, unknown, @bot suffix, /start
- `TestTypingIndicator` (3): sent during processing, stops after response, not sent for commands
- Zero regressions

---

## v0.5.0 — 2026-04-02 (Phase 1: Console runtime)

First runtime layer around Nūr. Console-only. Nūr remains synchronous — the runtime wraps it with async lifecycle management.

### Session manager (`runtime/sessions/manager.py`)
- Lazy session creation on first message per relationship key (`platform:user_id`)
- Backpressure: `max_active_sessions=10`, `max_queue_per_user=3`
- `evict_session()` — drain queue, end session, save state, close pipeline
- `evict_idle()` — evict sessions past timeout threshold
- `shutdown()` — graceful drain of all active sessions

### Per-user serialization (`runtime/sessions/user_session.py`)
- Each user session has an `asyncio.Queue` and a background worker task
- Worker calls `asyncio.to_thread(pipeline.process, ...)` — one message at a time
- Different users CAN process concurrently (separate workers, separate threads)

### State persistence (`runtime/sessions/persistence.py`)
- `save_engine_state()` — atomic write (tmp + rename) of modulator snapshot + timestamp
- `load_engine_state()` — read back from JSON
- Saved on session eviction and shutdown
- Restored with elapsed decay on session creation (via `pipeline.restore_state()`)

### Console channel (`runtime/channels/console.py`)
- `asyncio.to_thread(input, ...)` for non-blocking stdin reads
- Routes through session manager with `platform=console`
- `/quit` and `/exit` commands

### Runtime config (`runtime/config.py`, `runtime_config.yaml`)
- `RuntimeConfig` dataclass with data_dir, queue/session limits, timeout
- Helper methods for per-user paths and shared DB path

### LLM backend factory (`runtime/llm/backend.py`)
- `create_llm_backend()` — returns MiniMax client or MockLLMBackend
- One backend per pipeline (thread safety — `requests.Session` is not thread-safe)

### App orchestrator (`runtime/app.py`, `main.py`)
- `JarvisApp` wires session manager + console channel
- Signal handler for SIGINT/SIGTERM → graceful shutdown
- Entry point: `python main.py`

### Identity keys (per spec section 6.0)
- Relationship state key: `platform:user_id` (e.g., `console:user`)
- Session state key: `platform:user_id:chat_id` (e.g., `console:user:direct`)
- Data directory: `data/{platform}_{user_id}/` (colon-safe)

### Storage layout
- `data/{platform}_{user_id}/nur.db` — per-user Nūr database
- `data/{platform}_{user_id}/engine_state.json` — modulator snapshot
- `data/shared/self_model.db` — shared self-model across all users

### Testing
- 556 tests total (21 new runtime tests)
- Console end-to-end: message → session manager → pipeline → response
- Per-user serialization: concurrent messages never overlap; different users CAN overlap
- State save/load: JSON round-trip, atomic writes, directory creation
- Restart restore: reload from disk with elapsed decay applied
- Session lifecycle: max sessions enforced, queue backpressure, idle eviction, shutdown
- Identity keys: correct format, filesystem-safe directory names
- Shared self-model: observations visible across user pipelines
- Zero regressions

---

## v0.4.0 — 2026-04-02 (Phase 0: Runtime embedding)

Mandatory Nūr-side changes to support the Jarvis Runtime. No external behavior changes.

### EmotionalEngine.restore()
- `restore(snapshot, saved_at=None)` restores modulators from a dict snapshot
- If `saved_at` (unix timestamp) is provided, elapsed wall-clock time is computed and decay is applied before use
- `CognitivePipeline.restore_state()` convenience wrapper added

### CognitivePipeline.close()
- Closes all database connections (long-term memory, profile stores, person profiles, topic profiles)
- Safe to call multiple times (idempotent)

### Shared vs per-user storage split
- New `self_db_path` parameter on `CognitivePipeline.__init__`
- Per-user storage (`db_path`): long-term memories, person profiles, person observations, topic profiles
- Shared storage (`self_db_path`): self-model observations, defense events
- When `self_db_path` is None, falls back to `db_path` (backward compatible)
- Two separate `ProfileStore` instances: `_person_profile_store` (per-user) and `_self_profile_store` (shared)
- Two `ContradictionDetector` instances: `_person_contradiction` and `_self_contradiction`

### Schema version support
- `core/schema.py`: `SCHEMA_VERSION = 1`, `ensure_schema_version(conn)`, `SchemaVersionError`
- `schema_version` table added to all SQLite databases (ProfileStore, LongTermMemory, PersonProfileManager, TopicProfileManager)
- Unknown future schema versions fail loudly with `SchemaVersionError`
- Older versions migrate forward (no migrations yet — just version bump)

### Testing
- 535 tests total (26 new Phase 0 tests)
- `test_phase0.py`: restore behavior, elapsed decay, close(), shared self-profile isolation, schema version checks
- Zero regressions

---

## v0.3.6 — 2026-04-02 (Spike-only turns back to 1 call)

### What changed
- Spike-only unresolved tension no longer triggers inner dialogue in [`core/dual_process/inner_dialogue.py`](./core/dual_process/inner_dialogue.py)
- High-arousal hostile turns like `"I hate you"` now stay on the generator path instead of paying an extra fast-path call
- LLM self-check no longer escalates just because a defense activated; it now stays reserved for extreme intensity, contradictions, or dialogue deadlock
- Added a new regression test that locks in the exact bug: spike-only hostility should stay at 1 LLM call
- Updated call-budget comments/docs to match the new runtime behavior

### Why
- The previous spike-only fix handled calm follow-up turns, but hostile spike turns still paid extra latency on the same turn
- Defense activation from residual arousal was also still forcing an unnecessary LLM self-check on later calm turns
- In practice this meant the web UI still felt slow exactly when emotionally charged state updates happened

### Testing
- 509 tests collected
- 489 non-interface tests passed in this runner
- `tests/test_interface.py` remains unchanged but still hangs under this tool's `TestClient` harness

---

## v0.3.5 — 2026-04-02 (Sticky-spike fix + self-check tightening + timing)

### Fix: sticky inner-dialogue activation after spikes
- `InnerDialogue.deliberate()` now accepts `current_event_intensity` parameter
- `_max_rounds()` checks both event intensity and unresolved item sources
- If `current_event_intensity < 0.4` and all unresolved items are spike-sourced → skip (0 rounds)
- Non-spike unresolved items (contradiction, deadlock, topic) still trigger deliberation
- Prevents calm follow-up turns from paying 3x latency after a hostile spike

### Tighter LLM self-check gating
- New `_should_use_llm_self_check()` helper in pipeline
- LLM self-check fires only when: `intensity > 0.85`, contradictions present, deadlock reached, or defense activated
- Previously: any `intensity > 0.7` triggered LLM self-check

### Timing instrumentation
- `stage_timings_ms` added to `DebugState` with per-stage millisecond timings
- Timed stages: anticipation, contagion, event_classification, memory_retrieval, inner_dialogue, generator, self_check, total
- Exposed in `/chat` debug payload via `stage_timings_ms` field

### Testing
- 508 tests total (5 new regression tests)
- `test_calm_turn_uses_1_call` — calm message = 1 LLM call
- `test_hostile_turn_may_use_extra_calls` — hostile turns may use more
- `test_calm_after_spike_skips_dialogue_and_llm_self_check` — spike residue doesn't trigger dialogue on calm follow-up
- `test_calm_with_non_spike_unresolved_may_deliberate` — contradiction/deadlock unresolved items still trigger dialogue
- `test_stage_timings_present` — all timing keys present and valid
- Zero regressions

---

## v0.3.4 — 2026-04-02 (Inner dialogue → resolution-gated only)

### Inner dialogue fires only on unresolved tension
- All messages get 1 LLM call unless `resolution > 0.6` (real unresolved tension)
- Previously: any charged message (positive or negative) triggered 3 API calls
- Now: only accumulated unresolved items (contradictions, spikes, deadlocks) trigger deliberation
- Consistent fast responses regardless of emotional content
- Inner dialogue code fully preserved — just gated behind resolution threshold
- 503 tests, zero regressions

---

## v0.3.3 — 2026-04-01 (Thinking mode off for all calls)

### All LLM calls now use thinking mode disabled
- Master generator switched from `LLMClient` (thinking on) to `LLMClientFast` (thinking off)
- Generator prompt already has full emotional context — chain-of-thought reasoning unnecessary
- Eliminates `<think>...</think>` overhead on every API call
- `api.py` now creates a single `LLMClientFast` instance for all pipeline calls
- 503 tests, zero regressions

---

## v0.3.2 — 2026-04-01 (Calm message → 1 LLM call)

### Calm message bypass: skip inner dialogue entirely
- Calm messages (arousal < 0.55, resolution < 0.3): inner dialogue returns 0 rounds, 0 LLM calls
- Master generator produces response from scratch — 1 total LLM call per calm message
- Previously: fast path (1 call) + master (1 call) = 2. Now: master only = 1
- Charged messages unchanged: fast(1) + slow(1) + master(1) = 3
- `dominant_path="skip"` in trace for calm-bypassed messages
- 503 tests, zero regressions

---

## v0.3.1 — 2026-04-01 (Connection retry fix)

### Fix: RemoteDisconnected crash
- `LLMClient.generate()` now retries once on `ConnectionError` (stale keep-alive)
- MiniMax server closes idle connections; `requests.Session` reused dead socket on 3rd+ call
- Single retry is sufficient — reconnects on the fresh attempt
- 503 tests, zero regressions

---

## v0.3.0 — 2026-04-01 (Latency optimization)

Deep latency reduction: fewer LLM calls, no-thinking mode, connection reuse, calm bypass. Typical calls per message: 2 (down from 5 in v0.2.x).

### LLM call reduction
- **Contagion**: always rule-based (50+ keyword patterns, certainty/intensity signals)
- **Event classification**: always rule-based (keyword matching with detected emotion)
- **Topic detection**: always rule-based (substring matching against known topics)
- **Self-check**: rule-based by default; LLM self-check only when turn intensity > 0.7

### Calm message bypass
- When arousal < 0.55 and resolution < 0.3, slow path is skipped entirely
- Calm messages: fast(1) + master(1) = 2 LLM calls
- Charged messages: fast(1) + slow(1) + master(1) = 3 LLM calls

### Thinking mode control
- `LLMClient(thinking=True)`: full reasoning — used for master generator
- `LLMClientFast` (thinking disabled): used for inner dialogue fast/slow paths and self-check
- Eliminates `<think>` chain-of-thought overhead on calls that don't need reasoning

### HTTP connection reuse
- `LLMClient` now uses `requests.Session()` with persistent headers
- Reuses TCP + TLS connections across calls, avoiding ~100-300ms handshake per call

### Architecture
- Pipeline accepts `llm_backend_fast` parameter for no-thinking client
- `interface/api.py` wires `LLMClient` (generator) + `LLMClientFast` (everything else)
- Inner dialogue constants: `CALM_AROUSAL_THRESHOLD=0.55`, `CALM_RESOLUTION_THRESHOLD=0.3`

### Testing
- 503 tests total (3 new LLMClientFast tests + updated call budget tests)
- Latency test: full pipeline < 50ms with mock LLM (non-LLM overhead is negligible)
- Zero regressions

---

## v0.2.4 — 2026-04-01 (Memory retrieval optimization)

### Optimization: long-term memory retrieval
- `_compute_activation()` no longer re-queries `access_count` — uses value already loaded from the row
- `_mark_accessed_batch()` replaces per-item `_mark_accessed()` — single `executemany` + one commit instead of N queries + N commits
- No behavior change: same activation math, same retrieval order, same access tracking

---

## v0.2.3 — 2026-04-01 (Final verification fixes)

### Fix 12: Self-check correction_note propagation
- `_llm_check()` now returns the LLM's `correction_note` instead of dropping it
- `check()` prefers the LLM's targeted correction over generic "Please adjust" synthesis
- Retries in pipeline.py now receive the LLM's actual guidance

### Fix 13: TestClient hang
- TestClient fixture now uses context manager (`with TestClient(app) as c:`)
- Required for Starlette 1.0.0 / httpx 0.28.1 ASGI lifespan handling

### Testing
- 496 tests total (3 new regression tests for correction_note propagation)
- Zero regressions

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
