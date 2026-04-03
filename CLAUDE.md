# Project Nur

Read PROJECT_NUR_ARCHITECTURE.md for the full vision.
Read PROJECT_NUR_BUILD_PLAN.md for the v1/v2 roadmap.
Read PROJECT_NUR_V2_DESIGN.md for the v2 inner-life specification.
Read README.md for setup, usage, API reference, and module documentation.
Read CHANGELOG.md for version history and what changed when.

## Status: v2 COMPLETE + RUNTIME PHASE 4 + PRE-MERGE FIXES + AGENTIC TOOLS PHASE 8 + EVAL PHASE 9 + CALIBRATION PHASE 10 + PHASE 11.3

The full test suite currently collects 1210 tests.

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
- runtime/ — Jarvis Runtime: session manager, console + Telegram channels, debug API, state persistence, LLM backend factory
- main.py — Runtime entry point (`python main.py`)
- core/action_variables.py — Action-variable derivation from modulators (pure math, 0 LLM calls)
- core/tool_appraisal.py — Tool outcome appraisal (ToolResult → ToolObservation with emotional deltas)
- core/dual_process/tool_loop.py — Cognitive tool bridge: intent detection, arbiter, execute+appraise loop, multi-step plan support
- core/tool_memory.py — Tool episode memory coupling: short-term records, salient long-term writes, self-observations, unresolved items, trust deltas, task memory
- core/task_planning.py — Bounded multi-step task planner: heuristic detection, sequential execution, persistence-driven failure handling (0 LLM calls)
- core/proactive.py — Proactive behavior evaluation: trigger collection, scoring, bound enforcement, action selection (0 LLM calls)
- tools/ — Agentic tools package: registry, executor, builtin tools (filesystem, shell, web, browser, calendar)
- tools/mcp/ — MCP bridge: client protocol, adapter, category inference, register_mcp_tools()
- core/appraisal.py — Deterministic social appraisal (turn-level target/move/intent/vulnerability inference, 0 LLM calls)
- core/memory/relationship.py — Relationship-arc memory (events, open loops, context builder)
- core/strategy.py — Response strategy selector (8 strategies, deterministic, 0 LLM calls)
- evals/ — Evaluation and benchmark harness: structured scenarios, deterministic runner, assertion-based behavioral checks, text/JSON reporting, CLI entrypoint
- tests/ — 1210 collected tests including calibration, journey, v2, regression, Phase 0, runtime, Telegram, debug API, runtime-config, agentic tools Phase 0+1+2+3+4+5+6+7+8, eval Phase 9, calibration Phase 10, and Phase 11 coverage

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
- Proactive execution must participate in the same session active-work accounting as normal message processing so timeout eviction cannot fire mid-run
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
- `runtime/sessions/user_session.py` — UserSession: serialized turns via async lock + bounded backlog
- `runtime/sessions/persistence.py` — save/load session-state JSON (atomic writes)
- `runtime/channels/console.py` — ConsoleChannel: async stdin, routes through session manager
- `runtime/llm/backend.py` — MiniMax + OpenAI-compatible `create_llm_backend(config)` factory
- `runtime/tools.py` — per-pipeline builtin tool executor factory
- `runtime/config.py` — RuntimeConfig: data_dir, limits, timeout, channels, LLM backend, debug
- `runtime/app.py` — JarvisApp: orchestrator with signal-based graceful shutdown
- `main.py` — Entry point: `python main.py` (loads runtime_config.yaml)
- Identity: relationship key = `platform:user_id`, session key = `platform:user_id:chat_id`
- Sessions keyed by session_key — DM and group-chat contexts get separate active sessions
- Storage keyed by rel_key: `data/{platform}_{user_id}/nur.db`, `data/shared/self_model.db`
- Hot engine state keyed by session_key: `data/{platform}_{user_id}/sessions/{chat_id}.json`
- Per-user `asyncio.Lock` serializes pipeline access across chat contexts for the same user
- Per-user processing serialized; different users can overlap on the runtime-owned worker pool
- Nūr remains synchronous — runtime wraps pipeline calls in worker-thread helpers
- `RuntimeConfig.from_yaml(path)` loads config from YAML; defaults on missing file
- Runtime backend config supports `mock`, `minimax`, and `openai_compatible`
- `console_enabled` config flag — headless Telegram-only runtime supported

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
- Idle timer resets on message acceptance (enqueue) AND completion; fires `evict_session` automatically
- `_timeout_evict` guards against evicting sessions with in-flight or queued work — reschedules instead
- Graceful shutdown: `_accepting` flag stops intake → cancel all timers → drain + close all sessions
- Backpressure: max_queue_per_user rejects with RuntimeError; max_active_sessions rejects new users
- Shared self-model DB: WAL mode + busy_timeout=5000 via `ProfileStore(wal_mode=True)`
- Per-user DBs do NOT use WAL (single writer, no contention)
- Unresolved items are persisted in session engine snapshots and restored on restart/eviction

## Phase 4 (Runtime debug API — completed)
- `runtime/debug/api.py` — session-aware FastAPI debug endpoints
- `GET /sessions` — list active sessions (session_key, rel_key, user_id, idle_seconds, queue_size, has_debug)
- `GET /sessions/{session_key}/debug` — live modulators, memory counts, unresolved items, full last_turn debug state
- `POST /sessions/{session_key}/reset` — evict session (digest + persist)
- Reads from SessionManager — no separate pipeline
- `last_debug: DebugState` stored on UserSession after each process() call
- `_debug_to_dict()` preserves all v1/v2 debug fields (anticipation, dialogue_trace, defense, timings)
- `RuntimeConfig` gains `debug_host`, `debug_port` (default 127.0.0.1:8077)
- Debug server runs as background task in JarvisApp via uvicorn

## Agentic Tools Phase 0 (Types, traces, registry — completed)
- Read AGENTIC_TOOLS_DESIGN.md for the full agentic tools spec
- `core/types.py` extended with: ToolCategory, ToolCapability, ToolIntent, ToolDecision, ToolResult, ToolObservation, ToolTrace, ActionVariables
- `core/action_variables.py` — `derive_action_variables(state, trust, defense_active)` pure derivation
- `tools/registry.py` — `ToolRegistry` with register(), get(), list_tools(category=), names()
- `tools/types.py` — thin re-export layer (source of truth is `core/types.py`)
- `DebugState.tool_trace: ToolTrace | None = None` — safe default, no behavioral change

## Agentic Tools Phase 1 (Builtin execution layer — completed)
- `tools/executor.py` — `ToolExecutor`: lookup → call → normalize, all failures become structured `ToolResult`
- `tools/builtin/filesystem.py` — 6 ops: read_file, list_dir, search_text, glob_paths, write_file, delete_path
- `tools/builtin/shell.py` — `run_command(cmd, cwd, timeout_seconds)` via subprocess, captures stdout/stderr/exit
- `tools/builtin/web_search.py` — `search(query, limit)` + `fetch(url)` behind pluggable `WebProvider` protocol
- `tools/__init__.py` — `register_builtins(registry, executor, web_provider)` wires all 9 builtin tools
- 9 registered tools: fs.read_file, fs.list_dir, fs.search_text, fs.glob_paths, fs.write_file, fs.delete_path, shell.run_command, web.search, web.fetch
- No pipeline tool loop, no MCP, no cognitive integration yet

## Agentic Tools Phase 2 (Cognitive tool loop — completed)
- `core/dual_process/tool_loop.py` — cognitive bridge: `detect_tool_intent()` heuristic, `make_tool_decision()` arbiter, `run_tool_loop()` orchestrator
- `core/tool_appraisal.py` — `appraise_tool_result()` maps ToolResult → ToolObservation with emotional deltas
- Pipeline integration: optional `tool_executor` param, tool loop after contradiction check, before defense
- Action arbiter: 4 outcomes (execute, clarify, defer, refuse) driven by ActionVariables + category + trust
- Bounded loop: default max 2 executions, hard cap 3
- `DebugState.action_variables` + `tool_trace` populated on tool turns
- `PipelineContext.tool_context_summary` — summarized tool results for generator (never raw output)
- Debug API serializes tool_trace + action_variables
- No MCP, no multi-step planner, no autonomous background tasks yet

## Agentic Tools Phase 3 (Memory and self-model coupling — completed)
- `core/tool_memory.py` — tool episode memory coupling (all deterministic, 0 LLM calls)
  - `create_tool_event()` → EmotionalEvent for short-term memory
  - `is_salient_episode()` → salience check (destructive, failures, strong shifts)
  - `create_long_term_entry()` → LongTermEntry with compact summary, spike bypass for failures
  - `derive_tool_self_observations()` → behavioral traits (methodical, decisive, reckless, frustrated, persistent, hesitant, avoidant)
  - `create_tool_unresolved_item()` → UnresolvedItem (tool_failure, blocked_action, incomplete_task)
  - `compute_tool_trust_delta()` → conservative trust (+0.01 helpful read, -0.03 destructive/reckless failure)
- Pipeline step 11c: tool memory coupling after tool loop execution
- Short-term memory records for every tool execution
- Salient episodes written to long-term SQLite memory
- Self-observations fed to SelfProfileManager
- Unresolved items from failures feed resolution modulator
- Trust deltas applied to person profile
- `DebugState.tool_memory_effects` + debug API serialization
- No MCP, no multi-step planner, no autonomous background tasks yet

## Agentic Tools Phase 4 (Runtime debug integration — completed)
- Full observability for tool-aware turns in `runtime/debug/api.py`
- Intent serialization: all 10 fields (added `expected_outcome`, `clarification_threshold`, `persistence_drive`)
- Observation serialization: all 6 fields (added `continue_tool_loop`)
- `tool_summary` compact block: `tool_used`, `tools_executed`, `last_tool_name`, `last_tool_success`, `decision`, `loop_count`
- `_build_tool_summary()` helper for testability
- Debug API version bumped to 0.9.0
- All tool debug fields null on non-tool turns; JSON-serializable
- No MCP, no multi-step planner, no autonomous background tasks yet

## Agentic Tools Phase 5 (MCP bridge — completed)
- `tools/mcp/client.py` — `MCPClient` protocol (discover + call), `MCPToolInfo`, `MCPCallResult`, `NullMCPClient`
- `tools/mcp/adapter.py` — `MCPAdapter` (discover → ToolCapability + handlers), `infer_category()`, `register_mcp_tools()`
- MCP tools namespaced as `mcp.{server_name}.{tool_name}`
- Category inference: deterministic keyword matching on name/description, defaults to EXTERNAL_ACTION
- `mcp_backed=True` + `requires_network=True` flags set automatically
- Multiple MCP servers can coexist in one registry (different namespaces)
- All exceptions normalized to ToolResult — no raw MCP errors leak
- Same cognitive path as builtins: arbiter, appraisal, memory, debug
- `MCPClient` and `register_mcp_tools` re-exported from `tools` package

## Agentic Tools Phase 6 (Richer tools — completed)
- `tools/builtin/browser.py` — `BrowserProvider` protocol, 5 tools: open_url, get_page_text, click, fill, screenshot
- `tools/builtin/calendar.py` — `CalendarProvider` protocol, `CalendarEvent`, 3 tools: list_events, create_event, delete_event
- `tools/builtin/web_search.py` — added `web.extract_text` (bounded at 5,000 chars), `extract_text` on WebProvider protocol
- `core/dual_process/tool_loop.py` — heuristic patterns for browser, calendar, web.extract_text
- `tools/__init__.py` — `register_builtins()` accepts optional `browser_provider`, `calendar_provider`
- 18 total registered builtins: 6 fs + 1 shell + 3 web + 5 browser + 3 calendar
- All provider-based, all pluggable, all mockable, all disabled-safe via NullProvider defaults

## Agentic Tools Phase 7 (Multi-step task planning and task memory — completed)
- `core/types.py` extended with: TaskStatus, TaskStep, TaskPlan, TaskTrace, ToolTrace.task_trace
- `core/task_planning.py` — bounded multi-step planner (all deterministic, 0 LLM calls)
  - `detect_multi_step_intent()` — heuristic detection of compound requests ("X and then Y", "first X, then Y")
  - `execute_plan()` — sequential execution through ToolExecutor, bounded by max_steps_per_turn (default 3)
  - Persistence-driven failure handling: persistence_drive < 0.5 → block; >= 0.5 → continue
  - `is_continue_request()` / `is_status_request()` — follow-up detection for active plans
  - `summarize_plan_status()` — human-readable plan progress
- `core/dual_process/tool_loop.py` — `run_tool_loop()` gains `active_plan` param
  - Multi-step detection before single-step; arbiter checks first step category
  - Continue/status requests route to active plan
  - Plan results collected into ToolTrace for unified memory coupling
- `core/tool_memory.py` — task memory coupling functions
  - `create_task_unresolved_item()` → UnresolvedItem (task_blocked, task_incomplete)
  - `derive_task_self_observations()` → behavioral traits (methodical, persistent, frustrated, hesitant, reckless)
  - `create_task_long_term_entry()` → LongTermEntry for salient plans (failures, blocks, destructive)
- Pipeline: `_active_task_plan` session-scoped state, step 11d task memory coupling, cleared on end_session
- `DebugState.task_trace` + debug API serialization of plan/steps/outcome
- No autonomous background tasks, no cross-session plan persistence yet

## Agentic Tools Phase 8 (Proactive and autonomous behavior — completed)
- `core/types.py` extended with: ProactiveTriggerSource, ProactiveTrigger, ProactiveAction, ProactiveTrace
- `core/proactive.py` — proactive behavior evaluation (all deterministic, 0 LLM calls)
  - `evaluate_proactive()` — evaluate triggers, enforce bounds, select action
  - Trigger sources: unresolved items, pending tasks, commitments, temporal patterns, emotional salience
  - Trigger scoring: modulated by resolution, energy, trust/bonding, arousal
  - Bound enforcement: max per session, idle threshold, cooldown, energy floor
  - Action types: follow_up, continue_task, suggest, autonomous_step, none
- Pipeline: `process_proactive(user_id)` generates proactive responses through Nūr (defense + generator)
  - `_proactive_count` + `_last_proactive_at` session-scoped tracking, cleared on end_session
  - Task continuation via tool loop when action is continue_task
  - Self-observation: "proactive" trait recorded after each proactive action
- Runtime: `RuntimeConfig` gains proactive fields (enabled, idle_threshold, max_per_session, cooldown, check_interval)
- Runtime: `SessionManager.run_proactive_loop()` periodic sweep of idle sessions
  - Proactive callback for channel delivery: `proactive_callback(session_key, user_id, message)`
  - `JarvisApp` starts proactive loop as background task when enabled
- `DebugState.proactive_trace` + debug API serialization (triggers, action, suppressed reasons, limits)
- No open-ended loops, no multi-agent behavior, no cross-session plan persistence
- Phase 8 correctness fixes (v0.17.1):
  - `JarvisApp` wires `_deliver_proactive` callback — routes to console (print) or Telegram (send_message) by platform
  - `_run_proactive()` acquires per-user lock — same serialization as normal message processing
  - `process_proactive()` applies elapsed decay before evaluation — same as `process()` Step 0

## Phase 9 (Evaluation, calibration, and benchmark harness — completed)
- `evals/types.py` — EvalScenario, EvalTurn, EvalAssertion, EvalResult, EvalReport, EvalMetrics, ModulatorRange
  - 14 assertion kinds: tool_used, tool_not_used, tool_category, decision, modulator_range, unresolved_created/resolved, task_plan_created/continued/completed, proactive_triggered/suppressed, debug_field, response_contains, response_not_empty, custom
  - Range-based assertions — no brittle exact-value checks
- `evals/runner.py` — deterministic runner: `run_scenario()`, `run_scenarios()`, `run_by_tag()`
  - Executes through real CognitivePipeline with mock backend
  - Proactive evaluation with configurable idle simulation
  - Performance metrics: LLM calls, tool calls, latency, stage timings, defense activations
- `evals/reporting.py` — text and JSON report generators
- `evals/scenarios.py` — 34 golden behavior scenarios across 8 suites
  - Emotional core (5), tool loop (6), task planning (2), proactive (2), defense/resolution (2), relationship (3), calibration (8), Phase 11 human-likeness (6)
  - 97 assertions total
- `evals/__main__.py` — CLI: `python -m evals [--tag TAG] [--json] [--list]`
- No new user-facing features, no external dependencies

## Phase 10 (Calibration and policy shaping — completed)
- `core/action_variables.py` — persistence_drive base 0.50→0.45, action_urgency base 0.30→0.25
- `core/proactive.py` — valence boost +0.05 when valence < 0.3
- `core/tool_memory.py` — `_TRUST_POSITIVE_TOOL` 0.01→0.015
- `core/dual_process/tool_loop.py` — arbiter thresholds extracted as named constants
- `evals/scenarios.py` — 8 calibration boundary regression scenarios (Suite 7)
- `CALIBRATION_NOTES.md` — detailed tuning rationale and tradeoffs
- No structural changes, no new features — pure threshold tuning + regression locks

## Phase 11 (Lean humanization — in progress)
Three narrow, additive sub-phases that improve how Nūr interprets, remembers, and responds to social meaning.

### Phase 11.1 (Social appraisal — completed)
- `core/appraisal.py` — deterministic `appraise_message()` infers social intent before event classification
- `core/types.py` — `AppraisalFrame` dataclass: target, social_move, intent, blame, vulnerability, affiliation_bid, mixed_affect
- Pipeline: contagion → appraisal → event classification; external distress no longer damages trust
- Debug API serializes `appraisal_frame`

### Phase 11.2 (Relationship-arc memory — completed)
- `core/memory/relationship.py` — `RelationshipMemory`: durable events (rupture, repair, commitment, recurring_tension) + open loops
- `core/types.py` — `RelationshipEvent`, `OpenLoop`, `RelationshipContext`
- Digestion extracts relational arcs from session history; generator prompt includes relationship context
- Schema migration v1→v2 adds `relationship_events` and `open_loops` tables

### Phase 11.3 (Response strategy selector — completed)
- `core/strategy.py` — deterministic `select_strategy()` picks one of 8 approaches (0 LLM calls)
- Strategies: validate, reassure, repair, ground, give_space, practical_help, challenge_gently, set_boundary
- Selection inputs: `AppraisalFrame` + modulators + `PersonProfile` + `RelationshipContext`
- Priority: boundary → repair → give_space → ground → validate → reassure → practical_help → challenge_gently
- `STRATEGY_INSTRUCTIONS` dict provides compact prompt directives per strategy
- Pipeline Step 13b: strategy selected after defense, before generation
- `PipelineContext.response_strategy` carries the instruction into the generator prompt
- `DebugState.response_strategy` + debug API serialization
- `core/types.py` — `ResponseStrategy` enum

## Testing
- `pytest` collects 1210 tests
- `python -m evals` for the full evaluation benchmark (34 scenarios, 97 assertions)
- `python -m evals --tag emotional` for suite-specific runs
- `python -m evals --tag phase11` for appraisal/relationship/strategy regression
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

Read PROJECT_NUR_V2_DESIGN.md for the complete v2 specification.
