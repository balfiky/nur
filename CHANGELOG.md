# Changelog

All notable changes to Project Nur are documented here.

---

## v0.17.1 — 2026-04-02 (Phase 8 correctness fixes: callback wiring, serialization, decay)

Fixes three correctness issues in the Phase 8 proactive behavior implementation.

### Fix 1: Proactive delivery wiring (`runtime/app.py`)
- `JarvisApp` now passes `_deliver_proactive` as `proactive_callback` to `SessionManager`
- Console delivery: prints proactive messages to stdout (same format as normal responses)
- Telegram delivery: sends proactive messages via `TelegramClient.send_message`
- Routes based on platform extracted from session_key (`platform:user_id:chat_id`)
- Graceful handling of unknown platforms and malformed session keys

### Fix 2: Proactive serialization (`runtime/sessions/manager.py`)
- `_run_proactive()` now acquires per-user lock (`session._user_lock`) before calling `pipeline.process_proactive`
- Same serialization guarantee as `UserSession._worker` — no concurrent pipeline access
- Proactive execution cannot overlap with normal message processing or another chat context for the same user

### Fix 3: Elapsed decay before proactive evaluation (`pipeline.py`)
- `process_proactive()` now applies `engine.decay(elapsed)` before trigger evaluation
- Matches Step 0 of `process()` — modulators decay based on time since last turn
- `_last_turn_time` updated after decay, before evaluation
- No crash when `_last_turn_time` is None (first proactive check with no prior turns)

### Tests
- 12 new tests covering all three fixes: callback wiring (5), serialization (3), elapsed decay (4)
- Full suite: 1091 tests passing

---

## v0.17.0 — 2026-04-02 (Agentic Tools Phase 8: proactive and autonomous behavior)

Adds bounded proactive behavior — Jarvis can now initiate actions and follow-up messages based on unresolved tension, pending tasks, commitments, and idle-time patterns. All autonomy is explicitly bounded and inspectable.

### Proactive trigger model (`core/types.py`)
- `ProactiveTriggerSource` enum: unresolved_item, pending_task, commitment, temporal, emotional_salience
- `ProactiveTrigger` dataclass: source, description, intensity, optional item reference
- `ProactiveAction` dataclass: action_type (follow_up, continue_task, suggest, autonomous_step, none), trigger, message, rationale
- `ProactiveTrace` dataclass: triggers found, action taken, suppressed reasons, limits applied, idle time, count

### Proactive evaluation layer (`core/proactive.py`)
- `evaluate_proactive()` — deterministic evaluation (zero LLM calls) of whether Jarvis should initiate behavior
- Trigger collection: gathers candidates from unresolved items (intensity ≥ 0.3), pending task plans, commitments, temporal patterns (idle + resolution), emotional salience
- Trigger scoring: intensity adjusted by resolution boost, energy penalty, bonding/trust boost, arousal modulation
- Bound checks: max proactive per session, idle threshold, cooldown between actions, energy floor
- Action selection: pending task → continue_task, commitment → follow_up, unresolved → follow_up, temporal → suggest
- All configuration values exposed as parameters with sensible defaults

### Pipeline integration (`pipeline.py`)
- `process_proactive(user_id)` — evaluates proactive triggers and generates response through Nūr (defense + generator)
- Session-scoped tracking: `_proactive_count`, `_last_proactive_at` — cleared on `end_session()`
- Proactive responses go through defense filter and generator, same cognitive path as user-initiated responses
- Task continuation support: if proactive action is "continue_task", runs tool loop with active plan
- Self-observation recorded: "proactive" trait after each proactive action
- `DebugState.proactive_trace` field for observability

### Runtime integration
- `RuntimeConfig` gains proactive fields: `proactive_enabled`, `proactive_idle_threshold`, `proactive_max_per_session`, `proactive_cooldown`, `proactive_check_interval`
- `SessionManager.run_proactive_loop()` — periodic async loop that sweeps all sessions for proactive opportunities
- `_proactive_sweep()` / `_run_proactive()` — per-session evaluation with idle guards and error isolation
- Proactive callback mechanism: `proactive_callback(session_key, user_id, message)` for channel delivery
- `JarvisApp` starts proactive loop as background task when `proactive_enabled=True`
- Proactive loop cleanly cancelled on shutdown

### Debug / observability (`runtime/debug/api.py`)
- Full proactive trace serialization: triggers (source, description, intensity), action taken, suppressed reasons, limits applied
- Integrated alongside existing tool_trace, task_trace, tool_summary
- Both action and suppression visible — easy to understand why proactive behavior did or did not occur

### Constraints
- No open-ended self-directed loops
- Max proactive actions per session (default 3)
- Cooldown between proactive actions (default 5 min)
- Idle threshold before any proactive check (default 5 min)
- Energy floor: too tired to be proactive (energy < 0.15)
- No multi-agent behavior
- All output generated through Nūr cognitive pipeline

### Tests
- 52 new tests in `tests/test_agentic_tools_phase8.py`
- Coverage: types, trigger collection, trigger scoring, action selection, full evaluation, bound enforcement, pipeline integration, proactive count tracking, self-observation recording, debug serialization, runtime config, session manager callback, regression
- Full suite: 1079 tests passing

---

## v0.16.0 — 2026-04-02 (Agentic Tools Phase 7: multi-step task planning)

Adds bounded multi-step task planning and task memory — Nūr can now decompose compound requests into ordered steps, execute them sequentially, and couple task outcomes back into emotion, memory, and self-model.

### Task data model (`core/types.py`)
- `TaskStatus` enum: pending, in_progress, completed, failed, blocked
- `TaskStep` dataclass: single step with tool_name, arguments, result, observation, timing
- `TaskPlan` dataclass: bounded plan (max 5 steps) with goal, progression, persistence_drive
- `TaskTrace` dataclass: debug trace for plan execution (steps executed/succeeded/failed, latency, outcome)
- `ToolTrace.task_trace` field: links task traces to existing tool debug infrastructure

### Bounded planner (`core/task_planning.py`)
- `detect_multi_step_intent()`: heuristic detection of compound requests ("X and then Y", "first X, then Y")
- `execute_plan()`: sequential step execution through existing ToolExecutor, bounded by max_steps_per_turn
- Persistence-driven failure handling: low persistence_drive → plan blocks on first failure; high → continues
- `is_continue_request()` / `is_status_request()`: detect follow-up queries for active plans
- `summarize_plan_status()`: human-readable plan progress summary
- All detection and planning is deterministic — zero LLM calls

### Tool loop integration (`core/dual_process/tool_loop.py`)
- `run_tool_loop()` now accepts optional `active_plan` for cross-turn plan continuation
- Multi-step detection runs before single-step: compound messages create plans
- Arbiter checks first step's category before approving the plan
- Continue/status requests route to active plan instead of new intent detection
- Plan results collected into ToolTrace for unified memory coupling

### Task memory coupling (`core/tool_memory.py`)
- `create_task_unresolved_item()`: blocked/incomplete plans create resolution tension (task_blocked, task_incomplete)
- `derive_task_self_observations()`: multi-step behavior feeds self-model (methodical, persistent, frustrated, hesitant, reckless)
- `create_task_long_term_entry()`: salient plans (failures, blocks, destructive steps) persist to long-term memory

### Pipeline integration (`pipeline.py`)
- `_active_task_plan`: session-scoped task state, passed to tool loop each turn
- Step 11d: task memory coupling after tool execution — unresolved items, self-observations, long-term writes
- Terminal plans auto-cleared from session state
- `end_session()` clears active task plan alongside conversation history
- `DebugState.task_trace` field for observability

### Debug API (`runtime/debug/api.py`)
- Task trace serialization: plan (id, goal, status, steps, progress), execution stats, outcome
- Step-level detail: tool_name, description, status, success/error
- Integrated alongside existing tool_trace and tool_summary

### Tests
- 58 new tests in `tests/test_agentic_tools_phase7.py`
- Coverage: task types, multi-step detection, follow-up detection, plan execution, persistence-driven failure handling, emotional deltas, tool loop integration, task memory coupling, resolution coupling, self-model coupling, long-term memory, pipeline integration, debug serialization, regression
- Full suite: 1027 tests passing

---

## v0.15.0 — 2026-04-02 (Agentic Tools Phase 6: richer tools)

Expands the tool layer with browser automation, calendar, and richer web retrieval. Total registered builtins: 18 (was 9).

### Browser automation (`tools/builtin/browser.py`)
- `BrowserProvider` protocol with `NullBrowserProvider` default
- 5 tools: `browser.open_url`, `browser.get_page_text`, `browser.click`, `browser.fill`, `browser.screenshot`
- Categories: open/get/screenshot = READ_ONLY, click = EXTERNAL_ACTION, fill = WRITE
- Page text truncated at 10,000 chars to prevent memory bloat
- Pluggable — any Playwright/Selenium backend can implement the protocol

### Calendar (`tools/builtin/calendar.py`)
- `CalendarProvider` protocol with `NullCalendarProvider` default
- `CalendarEvent` dataclass for normalized event representation
- 3 tools: `calendar.list_events` (READ_ONLY), `calendar.create_event` (WRITE), `calendar.delete_event` (DESTRUCTIVE)
- Pluggable — any Google Calendar/Outlook/iCal backend can implement the protocol

### Richer web retrieval (`tools/builtin/web_search.py`)
- Added `extract_text` method to `WebProvider` protocol
- New `web.extract_text` capability — fetches and returns cleaned body text
- Bounded at 5,000 chars with truncation indicator
- Total web tools: search, fetch, extract_text

### Tool loop patterns (`core/dual_process/tool_loop.py`)
- Added heuristic patterns for browser: "browse to", "open in browser", "screenshot of", "take a screenshot"
- Added heuristic patterns for calendar: "show events for", "check calendar", "what's on", "create event"
- Added patterns for web.extract_text: "extract text from", "get readable text of"
- New extractors: `date`, `title_date`

### Registration (`tools/__init__.py`)
- `register_builtins()` now accepts optional `browser_provider` and `calendar_provider`
- All 18 tools registered in one call, no special-case paths

### Tests
- 51 new tests in `tests/test_agentic_tools_phase6.py`
- Updated Phase 1 test for new tool count (9 → 18)
- Full suite: 969 tests passing

---

## v0.14.0 — 2026-04-02 (Agentic Tools Phase 5: MCP bridge)

MCP tools are now first-class participants in Nūr cognition — same ToolCapability, same appraisal, same memory coupling as builtins.

### MCP client abstraction (`tools/mcp/client.py`)
- `MCPClient` protocol: `discover_tools()` → `list[MCPToolInfo]`, `call_tool()` → `MCPCallResult`
- `MCPToolInfo` dataclass: name, description, input_schema, server_name
- `MCPCallResult` dataclass: content, is_error, error_message, metadata
- `NullMCPClient`: safe default when no MCP server is configured (returns empty/errors)
- Protocol is `runtime_checkable` — any transport implementation is pluggable

### MCP adapter (`tools/mcp/adapter.py`)
- `MCPAdapter`: discovers MCP tools and converts to ToolCapability + handler functions
- `infer_category()`: deterministic category assignment from tool name/description keywords
  - READ_ONLY: get, list, read, search, fetch, query, find, show, view...
  - WRITE: create, write, update, set, add, put, edit, modify...
  - DESTRUCTIVE: delete, remove, drop, destroy, purge, clear...
  - EXTERNAL_ACTION: send, post, publish, notify, email, trigger...
  - Unknown tools default to EXTERNAL_ACTION (treated cautiously by arbiter)
- Namespaced tool names: `mcp.{server_name}.{tool_name}` (e.g., `mcp.calendar.list_events`)
- `mcp_backed=True` and `requires_network=True` flags set automatically
- Handler functions capture MCPClient via closure; all exceptions normalized to ToolResult

### Registration (`tools/mcp/adapter.py` + `tools/__init__.py`)
- `register_mcp_tools(client, registry, executor, server_name)` — one-call registration
- Multiple MCP servers can coexist (different namespaces)
- Builtin and MCP tools share the same ToolRegistry and ToolExecutor
- `MCPClient` and `register_mcp_tools` re-exported from `tools` package

### Cognitive invariants preserved
- MCP tools go through the same arbiter, appraisal, memory coupling, and debug path
- No separate MCP planning path or agent behavior
- Inner dialogue / tool loop treats MCP tools identically to builtins

### Tests
- 45 new tests in `tests/test_agentic_tools_phase5.py`
- Full suite: 918 tests passing

---

## v0.13.0 — 2026-04-02 (Agentic Tools Phase 4: runtime debug integration)

Full observability for tool-aware turns through the runtime debug API.

### Debug serialization fixes (`runtime/debug/api.py`)
- Intent serialization now includes all fields: `expected_outcome`, `clarification_threshold`, `persistence_drive` (previously missing)
- Observation serialization now includes `continue_tool_loop` (previously missing)
- API version bumped to 0.9.0

### Compact tool summary
- `tool_summary` block added to debug output for quick inspection:
  - `tool_used`, `tools_executed`, `last_tool_name`, `last_tool_success`, `decision`, `loop_count`
- Returns `null` on non-tool turns or when no intents were proposed
- `_build_tool_summary()` helper extracted for testability

### Debug output structure (tool turns)
- `tool_trace` — proposed intents (all 10 fields), final decision, executed results, observations (all 6 fields), loop count
- `action_variables` — all 5 derived variables (risk_tolerance, action_urgency, clarification_threshold, persistence_drive, autonomy_bias)
- `tool_memory_effects` — short-term recorded, long-term written, self-observations, unresolved items, trust delta
- `tool_summary` — compact at-a-glance block
- All fields null/absent on non-tool turns; JSON-serializable

### Tests
- 33 new tests in `tests/test_agentic_tools_phase4.py`
- Full suite: 873 tests passing

---

## v0.12.0 — 2026-04-02 (Agentic Tools Phase 3: memory and self-model coupling)

Tool episodes now persist into memory, self-model, and unresolved tension — making tool behavior part of Jarvis's ongoing identity and emotional history.

### Tool memory coupling (`core/tool_memory.py`)
- `create_tool_event()` — creates EmotionalEvent for short-term memory from tool results
- `is_salient_episode()` — determines if a tool episode warrants long-term persistence
  - Always salient: destructive actions, failures, repeated failures (≥2), strong certainty/valence shifts
- `create_long_term_entry()` — builds LongTermEntry with compact summary (max 200 chars)
  - Failures stored with high confidence (0.9) and spike=True (bypass threshold)
  - Destructive success gets cautious negative valence (-0.1)
- `derive_tool_self_observations()` — extracts behavioral traits from tool outcomes
  - Success: methodical (read/cognitive), decisive (destructive), technically_competent
  - High-risk write/destructive: reckless
  - Failure: frustrated; repeated failure: persistent
  - Clarify decision: hesitant; refuse/defer: avoidant
  - Capped at 3 observations per turn
- `create_tool_unresolved_item()` — creates UnresolvedItem from tool failures
  - Sources: tool_failure (decay 0.08), blocked_action (decay 0.05), incomplete_task (decay 0.10)
  - Intensity scales with failure count
- `compute_tool_trust_delta()` — conservative trust effects
  - Successful read/cognitive: +0.01
  - Failed destructive or reckless failure: -0.03
  - All other outcomes: 0.0 (neutral)
- `ToolMemoryEffects` dataclass for debug visibility

### Pipeline integration (`pipeline.py`)
- Step 11c: Tool memory coupling runs after tool loop execution
- Short-term memory records for every tool execution
- Salient episodes written to long-term memory (SQLite)
- Self-observations fed to SelfProfileManager after each tool result
- Unresolved items from failures added to resolution modulator
- Trust deltas applied directly to person profile and persisted
- `tool_memory_effects` field added to DebugState

### Debug API (`runtime/debug/api.py`)
- `tool_memory_effects` serialized in `_debug_to_dict()` with all fields

### Tests
- 55 new tests in `tests/test_agentic_tools_phase3.py`
- Full suite: 840 tests passing

---

## v0.11.0 — 2026-04-02 (Agentic Tools Phase 2: cognitive tool loop)

Integrates tool use into cognition. The pipeline can now decide between direct response and tool use, execute a bounded tool loop, appraise the result, and continue to generation.

### Tool-aware deliberation bridge (`core/dual_process/tool_loop.py`)
- `detect_tool_intent()` — heuristic keyword matching for obvious action requests (filesystem, shell, web)
- `make_tool_decision()` — deterministic action arbiter using ActionVariables + tool category + trust
  - Four outcomes: execute, clarify, defer, refuse
  - Destructive + low risk tolerance → refuse
  - Low autonomy bias → clarify
  - Write/destructive + high clarification threshold → clarify
  - Very low urgency → defer
- `run_tool_loop()` — full cognitive bridge: derive action vars → detect intent → decide → execute → appraise → apply deltas
- Bounded loop: default max 2 executions, hard cap 3
- Returns `ToolLoopResult` with trace, action variables, and generator-ready summary

### Tool outcome appraisal (`core/tool_appraisal.py`)
- `appraise_tool_result()` — maps ToolResult → ToolObservation with emotional deltas
- Success: certainty +0.08, valence +0.02, energy -0.01, resolution -0.05
- Empty output: certainty -0.06, resolution +0.05
- Execution error: arousal +0.08, valence -0.08, certainty -0.10, resolution +0.10
- Environment block (permission denied): arousal +0.05, certainty -0.05, resolution +0.06
- Destructive success: arousal +0.04, energy -0.03, self-observation "decisive"
- Self-observations: "methodical" (read success), "decisive" (destructive), "frustrated" (error), "hesitant" (block)

### Pipeline integration (`pipeline.py`)
- Optional `tool_executor` parameter on CognitivePipeline — when None, tool loop is skipped entirely
- Tool loop inserted after contradiction check (step 11), before defense mechanisms (step 12)
- Emotional deltas from tool appraisal applied directly to engine state
- Tool context summarized for generator (never raw output) via `tool_context_summary`
- No full pipeline recursion after tool results — lightweight appraisal loop only
- `tool_loop` timing added to `stage_timings_ms`

### Debug integration
- `DebugState.action_variables: ActionVariables | None` — derived action variables per turn
- `DebugState.tool_trace` populated on tool-involved turns (proposed intents, decision, results, observations, loop_count)
- `_debug_to_dict()` serializes tool_trace and action_variables for the runtime debug API

### Generator changes
- `PipelineContext.tool_context_summary` — summarized tool execution context
- Generator builds "Tool Execution Results" section from summary
- `{tool_context}` placeholder added to generator.md prompt template
- Generator instruction: "Do not echo raw output verbatim — summarize and contextualize"

### Testing
- 785 tests total (43 new Phase 2 tests)
- `TestDetectToolIntent` (10): conversational skip, read_file, list_dir, search_text, run_command, web_search, fetch, delete, unavailable tool, cat
- `TestMakeToolDecision` (7): execute read-only, refuse destructive, clarify low autonomy, clarify write+high threshold, defer low urgency, execute write normal, rationale included
- `TestToolAppraisal` (6): success read, empty output, failure, destructive success, permission denied, failure resolution
- `TestToolLoop` (7): no intent, successful execution, failed changes state, action vars populated, non-execute decision, bounded loop, intent gets action vars
- `TestPipelineToolIntegration` (5): no executor, direct response, tool turn trace, timing, failed tool, existing behavior preserved
- `TestDebugStateSerialization` (4): action vars field, with/without tool_trace, full pipeline serializable
- `TestGeneratorToolContext` (2): no tool context, tool context in prompt
- Zero regressions

---

## v0.10.0 — 2026-04-02 (Agentic Tools Phase 1: builtin execution layer)

Builtin tool implementations and execution orchestrator. No pipeline behavioral changes yet.

### Tool executor (`tools/executor.py`)
- `ToolExecutor` class: thin lookup → call → normalize orchestrator
- Accepts a `ToolRegistry` for capability lookup and per-name handler binding
- All failures (unknown tool, missing handler, handler exception) normalized into structured `ToolResult`
- Latency tracked via `time.perf_counter` and set on every result

### Filesystem tool (`tools/builtin/filesystem.py`)
- 6 operations: `fs.read_file`, `fs.list_dir`, `fs.search_text`, `fs.glob_paths`, `fs.write_file`, `fs.delete_path`
- Read bounded to 1 MB, search bounded to 100 matches
- Regex-based text search across files with line numbers
- Write creates parent directories, delete handles files and directories
- Each operation registered as a `ToolCapability` with category (READ_ONLY / WRITE / DESTRUCTIVE)

### Shell tool (`tools/builtin/shell.py`)
- `shell.run_command(cmd, cwd=None, timeout_seconds=30)`
- Captures stdout, stderr, exit code via `subprocess.run`
- Timeout and OSError normalized into `ToolResult`
- Category: WRITE

### Web search/fetch tool (`tools/builtin/web_search.py`)
- `web.search(query, limit=5)` and `web.fetch(url)`
- Pluggable `WebProvider` protocol for testability
- `NullWebProvider` default (errors until configured)
- `create_handlers(provider)` factory for binding a provider
- Category: READ_ONLY, `requires_network=True`

### Registration helper (`tools/__init__.py`)
- `register_builtins(registry, executor, web_provider=None)` wires all 9 builtin tools
- Single call registers capabilities + handlers for filesystem, shell, and web

### Testing
- 742 tests total (45 new Phase 1 tests)
- `TestExecutor` (5): success, unknown tool, no handler, exception normalized, latency
- `TestFilesystemReadFile` (3): existing, missing, directory
- `TestFilesystemListDir` (3): entries, missing, empty
- `TestFilesystemSearchText` (5): matches, no matches, single file, invalid regex, missing path
- `TestFilesystemGlobPaths` (3): match, no match, not a dir
- `TestFilesystemWriteFile` (3): new, parent dirs, overwrite
- `TestFilesystemDeletePath` (3): file, directory, missing
- `TestShellRunCommand` (6): success, non-zero exit, stderr, timeout, cwd, default timeout
- `TestWebSearch` (7): fake provider, limit, no results, fetch, null provider, error normalized, default handlers
- `TestBuiltinRegistration` (5): names, count, categories, end-to-end, custom provider
- `TestExecutorFilesystemIntegration` (2): write-read roundtrip, write-list-delete cycle
- Zero regressions

---

## v0.9.0 — 2026-04-02 (Agentic Tools Phase 0: types, traces, registry)

Foundation for agentic tool use. Types, action-variable derivation, tool registry, and debug trace wiring. No execution, no pipeline behavioral changes.

### Tool data model (`core/types.py`)
- `ToolCategory` enum: READ_ONLY, WRITE, DESTRUCTIVE, EXTERNAL_ACTION, COGNITIVE
- `ToolCapability`: registered tool description (name, category, arg_schema, streaming, network, MCP flags)
- `ToolIntent`: cognitive action object with urgency, risk_tolerance, autonomy_bias, clarification_threshold, persistence_drive, confidence
- `ToolDecision`: arbiter output (execute / clarify / defer / refuse)
- `ToolResult`: structured execution output (success, error, metadata, latency, side effects)
- `ToolObservation`: cognitively appraised tool result (emotional deltas, self-observation, unresolved item, loop continuation)
- `ToolTrace`: full debug trace (proposed intents, final decision, results, observations, loop count)
- `ActionVariables`: turn-level derived values for action shaping

### Action-variable derivation (`core/action_variables.py`)
- `derive_action_variables(state, trust, defense_active)` — pure math, zero LLM calls
- Five derived variables: risk_tolerance, action_urgency, clarification_threshold, persistence_drive, autonomy_bias
- Shaping rules per Section 8: arousal→urgency, certainty→autonomy, energy→persistence, resolution→persistence, trust→risk, defense→caution
- All values clamped to [0, 1]

### Tool registry (`tools/registry.py`)
- `ToolRegistry` class: register(), get(), list_tools(category=), names(), len, contains
- Category-filtered listing
- Overwrite-on-duplicate semantics

### Debug integration (`pipeline.py`)
- `DebugState.tool_trace: ToolTrace | None = None` — safe default, no behavioral change
- `ToolTrace` imported in pipeline

### Package structure
- `tools/__init__.py` — package marker
- `tools/types.py` — thin re-export layer (source of truth remains `core/types.py`)

### Testing
- 697 tests total (47 new Phase 0 tests)
- `TestToolCategory` (2): enum values, string enum
- `TestToolCapability` (2): minimal and full construction
- `TestToolIntent` (2): defaults, custom values
- `TestToolDecision` (3): execute, clarify, with intent
- `TestToolResult` (2): success, failure
- `TestToolObservation` (2): defaults, with unresolved item
- `TestToolTrace` (2): empty, populated
- `TestActionVariables` (2): defaults, clamping
- `TestDeriveActionVariables` (16): all shaping rules, both extremes, clamping
- `TestToolRegistry` (9): empty, register/get, contains, list/filter, overwrite, sorted names
- `TestDebugStateToolTrace` (4): default None, v2 fields intact, set trace, pipeline compat
- `TestToolsTypesReexport` (1): re-export identity check
- Zero regressions

---

## v0.8.2 — 2026-04-02 (Runtime backend + session-state cleanup)

Closes the remaining runtime gaps after the pre-merge review.

### Fix 1: Session-specific hot-state persistence (`runtime/config.py`, `runtime/sessions/manager.py`)
- Engine state now persists per **session_key** instead of one shared `engine_state.json` per user
- Hot state files live under `data/{platform}_{user_id}/sessions/{chat_id}.json`
- Different chat contexts for the same user no longer overwrite one another on shutdown
- Legacy per-user `engine_state.json` is still restored once as a backward-compatible fallback

### Fix 2: Real OpenAI-compatible runtime backend (`runtime/llm/backend.py`, `runtime/config.py`, `runtime_config.yaml`)
- Added `OpenAICompatibleLLMBackend` with sync `generate(system_prompt, user_message)` semantics
- `RuntimeConfig` now supports `llm_backend`, `llm_base_url`, `llm_model`, and `llm_api_key`
- `create_llm_backend(config)` now supports:
  - `mock`
  - `minimax`
  - `openai_compatible`
  - `auto` → prefers local OpenAI-compatible config, otherwise MiniMax, otherwise Mock
- `runtime_config.yaml` now documents local vLLM / OpenAI-compatible settings

### Fix 3: Debug API naming sync (`runtime/debug/api.py`, tests/docs)
- Debug routes now consistently refer to `session_key`, not `rel_key`
- Reset responses now return `session_key`
- Runtime docs/tests now match the implemented route contract

### Testing
- `pytest --collect-only -q` → `650 tests collected`
- `tests/test_runtime_config.py` → `21 passed`
- Async runtime-focused pytest slices still behave inconsistently in this runner, so broader verification here is based on code inspection plus updated regression tests

---

## v0.8.1 — 2026-04-02 (Runtime pre-merge fixes)

Five pre-merge fixes closing spec gaps in the Jarvis Runtime.

### Fix 1: Session identity semantics (`runtime/sessions/manager.py`, `user_session.py`)
- Sessions now keyed by **session_key** (`platform:user_id:chat_id`), not rel_key
- DM and group-chat contexts for the same user get separate active sessions
- Relationship state (DB, engine_state.json) still keyed by **rel_key** (`platform:user_id`)
- Per-user `asyncio.Lock` serializes pipeline access across chat contexts for the same user
- `UserSession` gains `session_key`, `_user_lock`, `_processing` fields
- Debug API listing now includes both `session_key` and `rel_key` fields

### Fix 2: Config-driven LLM backend (`runtime/config.py`, `runtime/llm/backend.py`, `main.py`)
- `RuntimeConfig.from_yaml(path)` classmethod loads `runtime_config.yaml` at startup
- `main.py` now loads config from YAML instead of using defaults
- `RuntimeConfig` gains `llm_backend` ("auto"/"minimax"/"mock") and `minimax_api_key` fields
- `create_llm_backend(config)` uses config for backend selection, falls back to env var
- `runtime_config.yaml` updated with `llm_backend`, `minimax_api_key`, `console_enabled` fields

### Fix 3: Config-driven channel startup (`runtime/app.py`, `runtime/config.py`)
- `RuntimeConfig` gains `console_enabled: bool = True`
- `JarvisApp.run()` starts console only when `console_enabled=True`
- Headless mode: when console disabled, blocks on `_shutdown_event.wait()`
- Signal handler sets `_shutdown_event` for both console and headless modes
- Telegram-only runtime works without console

### Fix 4: Inactivity timeout semantics (`runtime/sessions/manager.py`, `user_session.py`)
- Idle timer resets on message **acceptance** (enqueue), not only after processing completes
- `_timeout_evict` checks `_processing` flag and queue state before evicting
- Sessions with in-flight work or queued messages are never evicted — timer reschedules
- `UserSession.send()` updates `last_activity` on enqueue
- `UserSession._processing` flag set during pipeline.process()

### Fix 5: Docs sync
- CHANGELOG.md, CLAUDE.md, README.md updated to match final implementation
- Design doc (`JARVIS_RUNTIME_DESIGN_REVISED.md`) left as-is — it is a pre-implementation spec; intentional deviations documented in CLAUDE.md

### Testing
- 643 tests total (26 new)
- `TestSessionIdentitySeparation` (5): DM/group separation, independent state, shared storage, per-user lock
- `TestConfigFromYaml` (7): full load, missing/empty/partial files, unknown keys, allowlist
- `TestBackendSelection` (5): mock forced, auto fallback, env var, no config
- `TestChannelConfig` (5): console enabled/disabled, headless, from_yaml
- `TestTimeoutSemantics` (4): in-flight guard, queued guard, true inactivity, acceptance reset
- All existing tests updated for session_key keying
- Zero regressions

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
