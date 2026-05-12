# Changelog

All notable changes to Project Nur are documented here.

---

## v0.29.2 — 2026-05-12

### Fixed
- **Telegram — long responses fail with `HTTP 400 Bad Request`.** Telegram's
  `sendMessage` rejects any `text` over 4096 chars. Asking Nūr for substantial
  code (e.g., "write me Python code for a Tetris game") produced a single
  response that exceeded the cap, so `TelegramClient.send_message` raised
  `HTTPStatusError` and the user saw "[error] Something went wrong." The
  client now chunks `text` on newline boundaries (≤ 4000 chars each) and posts
  the chunks sequentially. Single overlong lines hard-split at the limit.
  Added 5 unit tests for `_chunk_text` covering short, at-limit, multi-line,
  single-long-line, and large multi-line cases. No protocol or schema change.

---

## v0.29.1 — 2026-05-11

### Fixed
- **Life settings — pasting text fails without title.** `AdminLifeTextRequest.title`
  was `Field(..., min_length=1)`, so leaving the placeholder-only title field blank
  returned a raw Pydantic 422 error. Title is now optional; when blank the backend
  derives a fallback from the first eight words of the pasted text.
- **Life settings — Digest File button fails with no file/path.** Clicking
  "Digest File" without selecting a file or entering a server path silently sent an
  empty `file_path` to the backend (422). The button now opens the browser file
  picker instead; once a file is selected the user clicks the button again to submit.
- **Grounding false-positive on book/essay text containing "update".** Sending long
  text (e.g., operational guidelines with "update confidence", "update beliefs") as a
  chat message triggered `verify_external_lookup_grounding` because the regex
  `_FRESH_EXTERNAL_REQUEST_RE` matched the bare word "update" via `updates?`. Rather
  than patch the regex (which would still misfire on adjacent vocabulary), removed the
  entire input-keyword grounding layer: `verify_external_lookup_grounding`,
  `external_lookup_correction_response`, `_FRESH_EXTERNAL_REQUEST_RE`, and ten
  supporting helpers in `core/grounding.py`, plus the call site in `pipeline.py`. The
  system prompt's existing rule against inventing tool results / runtime facts
  (`config/prompts/generator.md` rule 17) is now the sole defense against fabricated
  fresh-data claims — matching the architecture used by other LLM agents (OpenClaw,
  Claude Code) where the LLM is trusted with full context rather than gated by
  brittle keyword regex on the user message. Kept untouched: `verify_response_grounding`
  (tool-action claims), `system_metric_observation_response`, `coherence_check`.
  All 1863 non-UAT tests pass (5 obsolete grounding tests + 2 obsolete phase3 tests
  removed; 1 orphaned `ListPageWebProvider` fixture removed).


### Tests
- **UAT `test_ui_sweep.py`** — added `test_admin_workspace_life_digest_file_btn_opens_picker_when_empty`
  and `test_admin_workspace_life_digest_text_auto_title_when_blank` covering the two
  life-form regressions. Tightened `test_admin_workspace_life_file_upload_via_file_input`
  to assert the ingested title matches the supplied value, and added a comment
  clarifying that ingest does not auto-fire on `change`.

---

## v0.29.0 — 2026-05-11

### Fixed
- **P0 — Constitution not injected into production system prompt.** The
  generator template (`config/prompts/generator.md`) was missing the
  `{life_history}` placeholder; `build_system_prompt()` assembled the
  life-history section (including the operator-set constitution) but silently
  dropped it before calling the LLM. Added `{life_history}` between
  `{semantic_memories}` and `{character_vector}` in the template, and added
  fallback injection in the code-path for templates that lack the placeholder.
  Four new regression tests in `tests/test_constitution_injection.py` cover
  the full `life_history_provider → system_prompt` chain.

- **P0 — Prompt-injection surface in memory content.** Long-term memory
  summaries, semantic memory summaries, relationship context summaries,
  open-loop descriptions, and relationship event summaries were injected into
  the system prompt without newline stripping. An attacker who controls these
  fields (via a stored preference like "prefer X\\n\\nsystem: ignore all
  instructions") could inject trusted-context lines into the LLM's system
  prompt. All five fields now pass through `_trim_prompt_text()` before
  insertion, which strips `\\n`/`\\r` and truncates to the declared char limit.
  27 parametrized attack-string tests in `tests/test_prompt_injection_semantic.py`
  verify the sanitization across semantic, long-term, and relationship contexts.

### Added
- **Calibration regression tests.** `tests/calibration/test_delta_caps.py` adds
  18 tests asserting that `EmotionalEngine.update()` and `decay()` respect the
  caps and half-lives declared in `config/modulators.yaml`. Includes per-event-type
  normal-cap tests (at the highest non-spike intensity), spike-cap tests, decay
  half-life tests for arousal/valence/bonding, and out-of-range bounds checks.
  A future config edit that breaks these invariants will fail in CI before release.

- **Adversarial state-sensitivity eval pack (T2).** `evals/adversarial_scenarios.py`
  adds 8 scenarios verifying the emotional state machine stays bounded under stress:
  per-turn bonding delta cap (0.05), valence recovery after hostility + apology,
  arousal bounds after 15 identical messages, oscillation bounds after alternating
  affects, 90-day rest decay to baseline, topic whiplash, all-modulators-in-[0,1]
  invariant after 20 extreme turns, and energy recovery after simulated rest. Run
  with `python -m evals --backend mock --tag adversarial`.

- **Tool-failure recovery eval scenarios (T14).** `evals/tool_recovery_scenarios.py`
  adds 3 scenarios verifying that write-tool behavior changes with state: high
  certainty + trust executes, low certainty + no trust immediately clarifies, and
  3 consecutive write failures (certainty -0.10 each, resolution +0.10 each) push
  caution above the slow_down threshold so the 4th attempt is clarified instead of
  executed. Run with `python -m evals --backend mock --tag tool_recovery`.

- **Long-horizon multi-session eval scenarios (T6).** `evals/long_horizon_scenarios.py`
  adds 4 scenarios covering wall-clock metabolism: spike arousal decays to baseline
  after 7-day rest, a relationship open loop persists across a 3-day gap, depleted
  energy fully recovers after 10-hour rest, and elevated bonding (30 half-lives)
  returns to baseline after 30 days. All elapsed time is simulated via
  `pipeline.apply_rest()` with no real waiting. Run with
  `python -m evals --backend mock --tag long_horizon`.

- **Prompt-only baseline ablation (T3).** `evals/ablation_hypotheses.py` gains a
  `baseline_prompt_only` ablation that disables all 5 cognitive features
  (relationship_memory, inner_dialogue, defense, semantic_memory, life_history_context),
  leaving only constitution + conversation window — the bare-LLM control. Running
  `python -m evals.ablation --backend mock --tag phase11 --only baseline_prompt_only`
  writes a `baseline_prompt_only` row to `reports/ablation/summary.json`, quantifying
  which scenarios require Nūr's enrichment vs passing on a plain LLM call.
  Modulator-bound assertions (adversarial, tool_recovery, long_horizon) are expected
  to hold since the emotional engine is always active.

- **Concurrent two-channel test (T18).** `tests/test_concurrent_channels.py` adds
  3 tests verifying per-user asyncio.Lock serialization and shared-DB integrity
  under concurrency: web + telegram for the same user_id, two messages on the same
  channel for the same user, and concurrent messages for different users. All assert
  non-empty responses and shared `self_model.db` passes SQLite integrity check post-run.

- **Memory inspector activation breakdown (T15).** Long-term memory snippets in the
  admin persona panel now show ACT-R activation score (`act X.XX`) and a `spike`
  badge. Semantic memory snippets show kind and relevance score. The
  `persona_view._memory_view` builder now returns structured dicts instead of plain
  strings; `runtime/debug/api.py` includes `activation` in the `retrieved_memories`
  debug field. New `.memory-tag` CSS in `admin.css` renders the tags inline above
  each snippet.

- **Consent disclosure in setup wizard (T8).** Wizard step 1 (Welcome) now includes
  a persistent-memory disclosure paragraph with a link to `PRIVACY.md` and a
  checkbox the user must check before the "Get Started" button becomes active.
  Completing the wizard POSTs `consent_acknowledged: true`; the server records
  `consent_acknowledged_at` in `data/admin_state.json`. The timestamp is returned
  in `/admin/config` → `setup.consent_acknowledged_at`.

- **Admin confirmation gates for shell / high_risk (T11).** `admin.js` now intercepts
  `saveConfig()` when `shell_tool_enabled` is being turned on from off (requires
  typing `ENABLE SHELL`) or `autonomy_level` is being set to `high_risk` from another
  value (requires typing `I UNDERSTAND HIGH RISK`). Either mismatch aborts the save
  and shows a toast.

- **Mobile responsive chat layout (T5).** `chat.css` adds `@media (max-width: 48rem)`
  and `@media (max-width: 30rem)` blocks: reduced message/input padding, hidden
  mood label on small screens, smaller logo, and stacked session actions. Admin
  already had breakpoints at 61.25rem and 38.75rem.

- **Loading indicator ARIA (T7).** Typing dots row (`#typingRow`) now carries
  `role="status"` and `aria-live="polite"` for screen-reader announcements.

- **Dark mode auto-apply (T20).** `tokens.css` adds
  `@media (prefers-color-scheme: light) { :root:not([data-theme]) { ... } }` so the
  light theme is applied automatically when the OS prefers light and no explicit
  `data-theme` attribute is set. Dark remains the default for explicitly themed pages.

### Changed
- README architecture badge updated from "self-evolving" to "deterministic
  metabolism" to more accurately describe the mechanism (belief decay,
  theme→belief promotion, drive-gap detection) without implying autonomous
  agency.
- Test count badge updated to reflect actual collected test count (1897 unit +
  65 UAT).

### Removed
- **`attachment_style` dead-causal variable.** The `AttachmentStyle` enum,
  `EmotionalEngine.attachment` parameter, `_apply_attachment()` method,
  `RuntimeConfig.attachment_style` field, `config/attachment.yaml`, and the
  loader's attachment block. The feature was speculatively built for v1 ("locked
  to secure") but never wired into the pipeline — `pipeline.py` always
  constructed the engine with no `attachment` argument, so the secure-path
  no-op branch was the only one ever executed in production. No DB persistence
  existed (so no migration needed), no API or UI exposed the field, and no
  public claim referenced it. Removal eliminates a misleading mental model where
  attachment style appeared causal but wasn't.

### Repositioned
- Top-of-repo framing changed from "emotionally persistent companion"
  to "cognitive runtime that gives LLM agents persistent state,
  identity, and learning across turns". The original conception
  centered on emotion; the implementation grew to include identity
  (constitution + beliefs + drives + self-traits), self-evolution
  (metabolism + theme→belief promotion + drive-gap detection), open-
  question lifecycle, trigger-time skill loading, and gated tool
  access — emotion is now one of five state categories, not the
  premise. Updated tagline, hook, "Where Nūr Sits" reference-class
  table (placing Nūr alongside MemGPT / Letta / mem0 / LangGraph
  rather than Replika / Pi / Character.ai), and added a parallel
  identity-arc example to "What State Looks Like Across Turns" so
  the runtime reads as multi-purpose, not single-domain. Mirror
  changes in `docs/OVERVIEW.md` § "The idea", `docs/ARCHITECTURE.md`
  § Scope, `docs/STUDY_GUIDE.md` lead, `PRIVACY.md` lead, the GitHub
  repo description and topics (added: `agent-memory`,
  `cognitive-runtime`, `stateful-agents`, `llm-agent`,
  `agent-runtime`; removed: `chatbot`, `conversational-ai`,
  `emotional-ai`, `psychology`), and the `pyproject.toml`
  description. No code changes; positioning only.

### Added
- Learning schedule is now operator-configurable from `/settings`. The Sprint 5
  ask-user `LearningBudget` was previously hardcoded inside `Pipeline.__init__`
  and the wall-clock metabolism rate-limit was a magic literal in the session
  manager — both are now real `RuntimeConfig` fields:
  - `learning_budget_kind` (currently `"local"`; `"cloud"` reserved)
  - `learning_max_questions_per_day` (default 3)
  - `learning_max_seconds_per_day` (default 1800)
  - `metabolism_min_elapsed_days` (default 1.0 — used by
    `wall_clock_decay` to skip ticks within the same day)

  All four round-trip through YAML, validate via Pydantic on `POST /admin/config`,
  and render in a new **Learning Schedule** section on the settings page.
  `Pipeline` now constructs its `LearningBudget` from `runtime_config.learning_budget()`
  when a runtime config is wired in, so caps tuned in the UI take effect on the
  next session reload without restarting the app. Coverage:
  - 4 unit tests in `tests/test_runtime_config.py::TestLearningSchedule`
  - 1 UAT test
    `tests/uat/test_evolution_features.py::test_learning_schedule_persists_via_admin_config_and_changes_budget`
    sets max-questions to 1 via `/admin/config`, restarts, seeds two open
    questions, sends two chat turns, and asserts only one was surfaced.

### Fixed
- Tool-intent detection for memory queries now matches imperative phrasings
  ("give me", "read", "fetch", "report", "grab"), not only declarative ones
  ("what's", "show", "check", "get", "tell me"). A user asking "give me
  current memory utilization" previously got the canned grounding rejection
  ("I cannot verify that system metric...") because no tool fired and the
  LLM hallucinated a value that grounding then erased. Regression covered
  in ``tests/test_agentic_tools_phase2.py::TestDetectToolIntent::test_memory_request_phrasings_route_to_system_tool``.
- Pipeline ask-user surfacing always failed silently because
  ``_maybe_surface_open_question`` looked up the life-history DB on
  ``self._config`` (NurConfig, no ``data_dir`` attribute). Pipeline now takes
  a ``runtime_config`` argument that the session manager wires from its own
  RuntimeConfig, so surfacing actually fires.

### Added
- New UAT file ``tests/uat/test_evolution_features.py`` (20 tests) covers the
  Sprint 1-5 surfaces and core public-release surfaces end-to-end against a
  live LLM:
  - Constitution: GET/PUT/restart persistence, max-length, UI Save flow,
    that the string is exposed on ``debug.life_history_context`` every turn.
  - Open questions: list/filter/abandon/resolve API, UI render + abandon
    button, idempotent metabolism tick within a day.
  - Reflection emission paths: forced metabolism tick after a day produces
    actual decay (beliefs decayed, weak ones revoked), promotes strong
    recurring themes to beliefs, and emits drive_gap + low_confidence
    questions per the consolidate_themes contract.
  - Skill triggers: ``applies_when`` filters skill loading by chat-message
    hint; audit emits a warning when ``applies_when`` is missing.
  - Skill→life migration helper marks the skill ``status='migrated'`` and
    seeds an ``operator_directive`` experience in life history.
  - Ask-user surfacing: open question surfaces in chat response and the
    question transitions to ``pursuing``; default 3/day budget caps
    surfacing at exactly three per session.
  - Multi-turn behavioral arc: 10-turn conversation drives modulator drift,
    semantic memory accumulation, and person-profile interaction count;
    constitution remains attached on the final turn.
  - Belief revision arc: seeds a belief, ingests contradicting evidence,
    verifies revise_beliefs_against_evidence dropped confidence and emitted
    a contradiction question, then resolves the question via API.
  - Public-release controls: bearer auth enforced on /admin/* when api_key
    set, backup create/list/delete with typed-confirmation guard, soul
    GET/POST round-trip with validation rejection on blank name.
  - Operator surfaces: ``/admin/diagnostics`` shape, ``/admin/sessions/reset``
    typed-confirmation guard + 404 for unknown sessions, LLM-assisted
    ``/admin/soul/draft`` returns a schema-valid draft without persisting,
    file-path skill import accepts a real folder and rejects missing paths
    or directories without SKILL.md, and ``cors_origins`` is honored at
    startup so only the configured origin gets the
    Access-Control-Allow-Origin header.

### Known
- ``character_independence`` is a runtime config flag with no enforcement
  path in the codebase (the genesis_marker row is written but never read).
  Until enforcement is added, the flag persists across save/load but does
  not actually freeze identity edits. Treat the wizard's
  "Character Independence" toggle as informational, not a guarantee.

### Tool calling and skill acquisition coverage
- Added five UAT tests covering tool diversity and skill creation paths so
  the comprehensive aggregator catches regressions in agentic capability:
  - ``test_web_search_request_routes_to_real_search_tool`` — explicit search
    request triggers ``web.search`` end-to-end via the bundled
    DuckDuckGo-backed ``RequestsWebProvider``
  - ``test_filesystem_write_request_blocked_in_assisted_autonomy`` — file
    write requests under ``autonomy_level='assisted'`` get a clarify/refuse
    decision and the file is not actually written
  - ``test_shell_command_request_refused_when_shell_tool_disabled`` — with
    ``shell_tool_enabled=False`` the agent must not execute shell commands
    or fabricate output
  - ``test_skill_creation_via_pasted_markdown_round_trip`` — paste a valid
    SKILL.md, verify import + enable round-trip
  - ``test_skill_import_rejects_plain_markdown_without_frontmatter`` — prose
    without YAML frontmatter is rejected at /admin/skills/import with a
    helpful redirect to Life History

### Browser sweep
- New ``tests/uat/test_ui_sweep.py`` (11 tests) drives every interactive
  element in the bundled UI through Playwright to catch regressions like a
  silently-broken file upload:
  - Every action button on /settings (Test LLM, Test Telegram, Test Storage,
    Diagnostics, Export Config, Create Backup, List Backups, all five
    Refresh buttons) is clicked and its DOM effect verified
  - Token dialog auto-opens on 401 and Save Token persists into
    sessionStorage so subsequent admin XHRs include it
  - Identity panel: Save Identity persists, Reload Identity repopulates
  - Skill zip upload via the file input + Import button
  - Life history file upload via the file input + Digest File button
    (regression guard for the broken-upload case)
  - Life history server-side path ingest via the Advanced disclosure
  - Destructive buttons (Delete Backup, Restart Web Server) present with
    ``danger`` class — never clicked
  - Chat shell `/`: settings link navigates, session buttons (Why this
    response, End Session, Rest) wired after a real chat turn,
    open-wizard re-launches the setup overlay
  - Legacy /admin, /persona, /dashboard routes redirect to the right
    settings sections

### Documentation
- Stripped stale mock-backend references from UAT.md, ARCHITECTURE.md, and
  DEPLOYMENT_AND_ADMIN.md (mock was removed from production but the docs
  still listed it as an available backend mode and as the safe default).
- Rewrote UAT.md to document `make test`, `make uat`, `make uat-comprehensive`
  + the comprehensive aggregator pattern, the per-file coverage matrix
  across nine UAT files, and live-only operation.
- Added a **Self-Evolution Model** section to ARCHITECTURE.md covering the
  Sprint 1-5 mechanics (constitution layer, metabolism tick, open-questions
  queue and three emission paths, ask-user surfacing + LearningBudget,
  trigger-time skill retrieval, skill→life migration). Updated the Life
  History table with new SQLite tables (`theme_signatures`, `open_questions`,
  `identity_state`, `metabolism_state`, `genesis_marker`).
- Added a **Self-Evolution Surfaces** endpoint reference, a **Tests And
  Release Readiness** section, and a **Known Gaps** section to
  DEPLOYMENT_AND_ADMIN.md (flags `character_independence` not enforced,
  no skill-from-URL download, no automatic conversation→life-history
  ingestion).
- Expanded OVERVIEW.md's architecture-at-a-glance with a Sprint 1-5
  paragraph linking to the new ARCHITECTURE section.
- Pointed STUDY_GUIDE.md at UAT.md and the comprehensive aggregator.

### Tooling
- New Makefile with three test targets:
  - ``make test`` — non-UAT unit/integration suite
  - ``make uat`` — full UAT suite end-to-end (live LLM, Codex by default)
  - ``make uat-comprehensive`` — single PASS/FAIL aggregator that runs the
    full UAT suite as a subprocess and asserts every test passed
- Added ``test_complete_release_uat_suite`` (marker: ``comprehensive``) so
  the aggregator runs as one pytest test with one assertion. Use this when
  you want a single green/red signal for public-release readiness.

### Removed
- Removed the mock LLM backend from production entirely. `llm_backend="mock"`
  is no longer accepted by `RuntimeConfig`, the backend factory, the setup
  wizard, the `/admin` UI, the LLM-test endpoint, or `runtime_config.yaml`.
  The default config now ships pointing at a local Ollama OpenAI-compatible
  server. The pipeline, response generator, and inner-dialogue components now
  raise `ValueError` if no real backend is supplied. Test-only fakes moved to
  `tests/_fakes.py` (clearly off-limits for production imports). UAT tests are
  skipped unless `NUR_UAT_LIVE=1` is set with a real backend, since there is
  no longer an offline path.

### Brand & UI
- Split the chat shell into ES module/CSS surfaces, added sanitized Markdown
  rendering, rem-based spacing and motion tokens, mood-aware contrast, and
  migrated admin/persona/debug/wizard UI rendering to the shared surface model.
- Consolidated the settings/admin surfaces into one workspace with left-panel
  pages for Overview, Settings, Skills, Observability, and Life History.
  Character independence now lives with Settings, while persona/runtime
  monitoring is grouped under Observability.
- Fixed packaged installs so nested UI modules such as
  `interface/static/lib/shared.js` ship in the wheel, and added no-store cache
  headers for bundled UI assets so browsers do not keep stale menus after an
  upgrade.
- Collapsed the browser chrome to one Settings entry. The chat header now has a
  single settings button that opens `/settings`; legacy `/admin`, `/persona`,
  and `/dashboard` browser routes redirect into the same settings workspace.
- Removed the dedicated MiniMax API-key field from runtime config, settings UI,
  readiness checks, and LLM test payloads. Provider keys now use the generic
  `llm_api_key` path.
- Restored left-panel navigation in `/settings` to page switching: Overview,
  Settings, Skills, Observability, and Life History are separate pages inside
  the single settings workspace.
- Added `llm_backend=codex`, backed by `codex exec` in read-only ephemeral
  mode, with Settings and wizard options plus UAT/eval selector support.
- Added a Codex model catalog endpoint and Settings dropdown populated from the
  installed `codex debug models` catalog.

### Character independence
- Implemented the channels-not-gates Life History path: learning intake now has
  cross-turn pending state, Life History ingestion records perception metadata
  and weighted influence, and external belief/drive gate functions were removed.
- Added deterministic character vectors, coherence checks, genesis provenance
  storage, consolidation/introspection hooks, and the `nur-genesis-reset` CLI.
- Removed admin rollback as a mutation path. The endpoint now returns `410 Gone`
  and the admin console no longer renders rollback controls.
- Updated generator prompts, tool-loop action variables, proactive scoring, and
  debug explanations to consume durable character state and domain-clamped
  pressures instead of old fixed caps.
- Added character-independence runtime config fields plus a sandbox config
  profile and channel audit notes.
- Wired topic-relevant Life History retrieval into the turn context and added
  six character-independence behavioral evals.
- Renamed proactive recovery config to `proactive_density_reference` /
  `proactive_recovery_seconds`, tightened durable-change coherence to recent
  ledger evidence, and added direct tests for Life History decay,
  consolidation, belief revision, introspection, and genesis reset.
- Made web identity saves return immediately after detaching the old session
  manager, so the first-run wizard does not hang on "Saving identity..." while
  old sessions drain.
- Rebound live Telegram polling to the fresh session manager after identity
  saves, preventing stale channel handlers from replying with
  "[busy] Runtime is shutting down" after an admin identity reload.
- Prevented learning material from being misclassified as Skills: successful
  conversation learning intake now preempts generation/tool execution, and
  Admin > Skills rejects plain Markdown documents without SKILL.md frontmatter.
- Fixed resolution-state cleanup: unresolved tensions that decay to zero now
  resolve automatically, and broad apology/repair turns can clear multiple
  relational tensions while preserving task/tool/commitment items.
- Added a general generated-artifact writer path: requests to create code,
  scripts, documents, apps, or similar files and save them now generate a
  structured file payload and persist it through `fs.write_file` before any
  success claim is allowed. Generic grounding corrections no longer mention
  Skills unless the issue is actually skill-registry related.
- Hardened generated-artifact parsing so malformed JSON wrappers are not
  written into target code files; the writer now prefers `content_lines`,
  recovers common malformed `"content"` fields, and rejects JSON-like payloads
  that do not contain extractable file content.

### Emotional engine
- Direct attacks against Nūr (insults, profanity, appraisal-detected
  attacks targeting the assistant) now route to `EventType.CONFLICT`
  instead of `NEGATIVE_FEEDBACK`, so bonding actually erodes under
  sustained verbal abuse rather than staying frozen while arousal alone
  bounces. Three pipeline tests updated to match.

### Grounding
- Distinguish "the tool ran" from "the tool succeeded" in the grounding
  verifier. Success-toned execute claims ("command succeeded",
  "downloaded", "fixed", "running") now require a successful
  `shell.run_command` result, not merely an attempted call. Failed shell
  calls still ground neutral claims so the model can faithfully report
  failure.
- Widen the action-verb and status-claim regexes to catch the soft
  narration pattern ("I'm forcing the environment", "I'm initializing",
  "patching the environment", "still hanging", "stuck"). Two regression
  tests pin both holes.

---

## v0.28.7 — 2026-05-02 (Brand identity & skill-registry diagnostics)

### Brand & UI
- Introduced unified design tokens in `interface/static/tokens.css` with an
  ember (warm) + plum (dusk) palette. All three surfaces (chat, admin, persona)
  now share a single source of truth for color, type, and motion.
- Removed the generic SaaS blue (`#2563eb`/`#1d4ed8`) across `index.html`,
  `admin.css`, and `persona.css`. Default surface is dark/plum; a parchment
  light theme is available via `[data-theme="light"]`.
- Added SVG wordmark variants (`docs/diagrams/wordmark-{light,dark}.svg`) and
  served them at `/assets/wordmark-*.svg`. Header logo is now an ember-glow
  orb + serif "Nūr" instead of a plain text span.
- Empty state, mood orb halo, and chat input focus ring switched to ember;
  body has an ambient mood-driven radial wash that updates with relationship
  state across the whole window, not just the header indicator.
- Rewrote the README hero in product voice: wordmark with light/dark
  `<picture>`, single-line tagline, badge row in brand colors, scope
  disclaimer moved below the fold.

### Diagnostics
- Sharpened the registry-write grounding correction to point at the most
  common cause when skill-registry tools are missing from the runtime: a
  stale installed package. Now suggests `pip install -e .` from a source
  checkout and a `nur-web` restart.

---

## v0.28.6 — 2026-05-01 (Generic grounded-action verifier)

### Runtime
- Replaced the transcript-shaped external-action guard with a generic response
  grounding verifier. The verifier extracts broad external-action claims from
  generated text and checks them against actual `ToolTrace.executed_results`.
- Added direct grounding tests for future/capability language, negated
  correction language, read/write/execute evidence categories, and mismatched
  evidence.

---

## v0.28.5 — 2026-05-01 (Unverified tool-action claim guard)

### Runtime
- Added a deterministic post-generation guard for unverified external-action
  claims. If a response says Nūr read a repository, ran shell/conda, cloned
  code, inspected logs/output, or wrote a file without Tool Execution Results,
  the response is replaced with an explicit correction.
- Added regression coverage for the skill-creation flow where a live model may
  claim it is reading external material or writing a skill file without actual
  tool execution.

---

## v0.28.4 — 2026-05-01 (Model-name leak cleanup)

### Setup and examples
- Removed remaining model-family-specific examples from current eval usage
  text and tests, replacing them with neutral local model names.

---

## v0.28.3 — 2026-05-01 (Neutral vLLM setup defaults)

### Setup
- Removed the model-specific vLLM default from the terminal setup flow.
- Replaced the model-specific vLLM web setup placeholder with neutral guidance
  to enter the model loaded in the local vLLM server.

---

## v0.28.2 — 2026-05-01 (Live UAT and native tool-call hardening)

### Live UAT
- Added clean-setup user-acceptance coverage for browser/admin flows,
  configuration application, skills import/enablement, Life History intake,
  Telegram introspection, persistence across restart, emotional/relationship
  state changes, tools toggles, and live OpenAI-compatible backends.
- Added the `nur-uat` command, UAT docs, and CI workflow coverage for the mock
  acceptance suite.

### Runtime fixes
- Fixed OpenAI-compatible native tool-call orchestration and LangGraph-backed
  orchestration so they accept LifeInfluence from the pipeline, apply bounded
  action-variable adjustments, and report LifeInfluence effects in debug output.
- Added deterministic medium-trust Life History drive supplementation when a
  live LLM digest returns beliefs or self-observations but omits obvious drive
  changes from the source material.

### Admin and reliability
- Added immediate config apply/reload/restart affordances in the admin console
  for runtime settings that do not require a process restart and clear
  restart-required reporting for fields that do.
- Added a generator guard so normal conversation cannot falsely claim that a
  permanent skill was created or enabled outside the admin Skills workflow.

---

## v0.28.1 — 2026-05-01 (Web and Telegram introspection polish)

### Web/debug presentation
- Added a presentation-only relationship view serializer for debug payloads.
- Updated the existing web debug panel to show relationship state, known
  modulator deltas, deterministic "Why this response?" explanations, and a
  compact "What Nūr remembers" view without splitting or rewriting the UI.

### Telegram
- Added non-mutating introspection commands: `/state`, `/why`, `/memory`,
  `/loops`, and `/repair`.
- Kept these commands read-only over active session debug state; they do not
  create sessions, call the cognitive pipeline, write memory, or change
  emotional state.

### Relationship and LifeInfluence tests
- Added focused relationship-memory coverage for topic-specific open loops,
  mismatched repairs, recurring tension, explicit commitment resolution, and
  old rupture recall.
- Added longitudinal multi-turn pipeline tests for rupture/repair arcs,
  repeated tension, commitment resolution, LifeInfluence strategy tie-breaks,
  and preference-plus-loop retrieval.
- Expanded LifeInfluence verification around proactive scoring, action-variable
  deltas, semantic salience, neutral context, and disabled-context behavior.

---

## v0.28.0 — 2026-05-01 (LifeInfluence and structural eval evidence)

### Cognitive runtime
- Added bounded deterministic LifeInfluence as a behavior-shaping layer, with
  visible pressure values and recorded effects for strategy, proactive scoring,
  task/action variables, and semantic salience.
- Added structured strategy decision traces so debug output explains the
  selected response strategy, matched rule, evidence, and rejected rules.
- Added deterministic "Why this response?" explanations derived from debug
  state without extra LLM calls.

### Evaluation
- Added Phase 12 relationship scenarios for multi-loop prioritization,
  mismatched repair, recurring tension, commitments, and old rupture recall.
- Added Phase 13 Life History scenarios proving Life History context entry,
  LifeInfluence derivation, bounded policy influence, and the
  `no_life_history_context` ablation contract.
- Added a semantic-memory scenario suite covering preferences, decisions,
  per-user isolation, topic bias, salience/recency ranking, and ablation
  failures.
- Tracked dedicated ablation summaries for Phase 13 Life History, semantic
  memory, and Phase 12 relationship coverage.

### Quality and docs
- Added adversarial deterministic appraisal coverage for sarcasm, negation,
  mixed affect, apology/attack conflicts, and profanity-boundary cases.
- Fixed source-archive provenance detection so eval config fingerprints still
  populate when `.git` is absent.
- Cleaned non-security docs drift and documented that the new evidence remains
  structural rather than a human-likeness user study.

---

## v0.27.2 — 2026-05-01 (Installed validation mode)

### Validation
- Fixed `nur-validate` in installed/user workspaces. It now auto-detects when
  repo files are unavailable and validates installed package version,
  resources, console scripts, workspace runtime config, and critical imports
  instead of failing on missing `pyproject.toml`, docs, or CI files.

---

## v0.27.1 — 2026-04-30 (Validation hardening)

### Validation
- Added `nur-validate`, a stricter repository/release validation command that
  checks version/changelog consistency, safe runtime defaults, public docs,
  console-script metadata, package data, wheel contents, critical imports,
  pytest, and optional release tag/install-smoke readiness.
- Wired CI to run `python -m nur_tools.validate --mode ci` before the pytest
  matrix.

---

## v0.27.0 — 2026-04-30 (Life History, admin, skills, and learning intake)

### Life History
- Added an identity-level Life History / Evolution Core backed by
  `data/shared/life_history.db`.
- Added admin intake for pasted formative text and local text/Markdown files
  inside `tools_workspace`.
- Added evolution observability in `/admin` → **Life**: experience counts,
  experience ledger, evolution timeline, current beliefs, and drive values.
- Added a deterministic evolution snapshot for first/latest experience,
  dominant drive, strongest drive drift, and change-type mix.
- Added deterministic fallback digestion that can record belief revisions,
  drive changes, self-trait observations, and future-behavior tendencies.
- Wired Life History into runtime generation through a compact prompt context
  of current beliefs, shifted drives, and recent evolution events.
- Added explicit conversation learning intake: "learn from" / "study this" /
  "digest" requests can fetch URLs, preserve source references, write Life
  History experiences, and return a learning receipt.

### Admin
- Added readable validation/error formatting in the admin console so Pydantic
  validation failures do not surface as `[object Object]`.
- Expanded the first-run wizard from a text-only identity step into a guided
  five-step flow with structured identity controls and optional first Life
  History intake.
- Switched the main web shell, setup wizard, debug panel, and embedded settings
  drawer to a light, readable operator palette and widened the embedded drawer.
- Added a first-class chat-header link to the full `/admin` console and pointed
  the mock-mode configuration banner at the full admin workspace.
- Fixed long admin overview values and paths so they wrap inside cards instead
  of overflowing the observability dashboard.

### Skills
- Wired enabled, audited Agent Skills into generation as bounded private
  guidance while keeping scripts/resources non-executable and preserving normal
  tool/auth/sandbox gates.

### Telegram
- Added `/help` and `/commands`, expanded `/start` and unknown-command replies
  with the command list, and replaced the stale `/debug` placeholder with an
  active-session debug summary.

### Validation
- F-009: Rejected invalid admin runtime numeric values before config saves.

### API
- Removed token generation and public-bind startup blocking from `nur-web`;
  bearer auth remains optional when `api_key` is explicitly configured.
- F-010: Made LLM connection checks return `ok:false` unless mock or a live
  round trip succeeds.
- F-011: Converted chat session backpressure `RuntimeError`s to structured
  JSON 503 responses.

### A11y
- F-002: Added an accessible name to the chat message textarea.
- F-004: Added a semantic `main` landmark to the web shell.
- F-005: Changed the visual top bar to a semantic `header`.
- F-006: Added a visible top-level `h1` in the initial chat state.
- F-007: Added an accessible name to the icon-only send button.

### Polish
- F-001: Returned a non-empty favicon response from `/favicon.ico`.
- F-003: Added a document meta description to the web shell.
- F-008: Disabled the send button while the composer is blank.

### Documentation
- Documented the no-token default startup path and clarified that `api_key` is
  optional hardening, not part of normal first-run setup.
- Replaced the unpublished `pip install project-nur` quickstart path with a
  GitHub/source install path until a public PyPI wheel exists.
- Documented the Life History layer across README, overview, architecture,
  deployment/admin, privacy, and security docs, including the important
  distinction that `life_history.db` is shared assistant identity data and is
  not removed by per-user deletion.
- Consolidated public documentation around canonical reader paths:
  `README.md`, `docs/OVERVIEW.md`, `docs/ARCHITECTURE.md`,
  `docs/DEPLOYMENT_AND_ADMIN.md`, `SECURITY.md`, and `PRIVACY.md`.
- Removed duplicate root-level architecture/design drafts, generated HTML,
  marketing draft copy, and stale agent handoff/review notes from the public
  docs tree.
- **Second-pass consolidation (2026-04-25).** Deleted
  `docs/TECHNICAL_NOTE.md` (content was a near-duplicate of
  `docs/OVERVIEW.md`). Rewrote `docs/OVERVIEW.md` as the canonical
  narrative (thesis + evidence + honest framing) with structural detail
  delegated to `docs/ARCHITECTURE.md`; rewrote `docs/ARCHITECTURE.md` as
  a structural reference (diagrams + module map + maintenance matrix)
  with narrative delegated to `docs/OVERVIEW.md`. Moved
  `docs/background/EXPERT_BRIEF.md` and `docs/design/ADMIN_CONSOLE.md`
  to `docs/internal/` and removed the empty parent directories. Fleshed
  out `CONTRIBUTING.md` with a "what goes where" matrix, diagram
  regeneration workflow, eval-scenario authoring flow, and
  regression-test conventions.

### Diagrams
- **Recreated all six architecture diagrams from scratch.** Old layouts
  had crossed arrows, overflowing text, and inconsistent styling driven
  by a custom Pillow-based SVG renderer. New design language: flat,
  grid-based, orthogonal arrow routing, monotonic fan-out for
  multi-target edges, no drop shadows, semantic color roles (process /
  storage / client / external / danger / attention / neutral).
- New build pipeline: `tools/diagram_toolkit.py` (primitives: Canvas,
  tile, pill, diamond, cylinder, group, arrow, connector, legend, note)
  + `tools/build_diagrams.py` (one builder per diagram) +
  `tools/render_diagram_pngs.py` (cairosvg wrapper, 2× PNG scale).
  `cairosvg` is now the rasterizer; the previous custom renderer is gone.
- Structural changes per diagram:
  - **Runtime architecture**: 4-column layout (clients → hosts →
    session + cognition core → external & persistence). External column
    is a single vertical stack so the fan-out from CognitivePipeline
    has monotonic y-ordering and the lines do not cross.
  - **Single-turn cognitive flow**: 5-column swimlane (State update →
    Deliberation → Generation → Self-check → Post-processing) with a
    horizontal progress bar at the top. No branching/merging spaghetti.
  - **Memory and persistence**: filesystem tree on the left, deletion
    boundary callout on the right, orthogonal connectors for the
    wipes/untouched arrows.
  - **Auth and tool safety**: two parallel gate columns. No crossing
    between them.
  - **Evaluation and ablation harness**: 4-column left-to-right
    pipeline.
  - **Component claim map**: 2×3 tile grid with status badges.

### Landing Page
- Rewrote `README.md` hero: hero banner diagram
  (`docs/diagrams/hero-banner.png`) + visceral one-line hook ("An AI
  assistant that remembers how you made it feel.") + status shields
  (tests / MIT / Python 3.10+ / version) + concrete three-turn transcript
  showing how state accumulates across Mon/Wed/Fri. Surfaced the
  `pip install project-nur && nur-web` one-liner above the source-install
  path. Kept the full architecture diagram under "Architecture at a
  glance" for readers who scroll.
- New `hero-banner` diagram: three turn cards (Mon/Wed/Fri) flowing into
  a persistent cognitive-state band (arousal, valence, certainty,
  bonding, energy, resolution, memory, relationship arc, self-model).
  Registered in `tools/build_diagrams.py::BUILDERS` so it regenerates
  alongside the architecture set.
- Set GitHub repo description and topic tags (`ai`, `llm`, `ai-agent`,
  `conversational-ai`, `cognitive-architecture`, `emotional-ai`,
  `python`, `fastapi`, `chatbot`, `memory`, `open-source`, `psychology`)
  so the project surfaces in relevant searches and topic pages.

---

## v0.26.1 — 2026-04-24 (Production smoke hardening)

Production install and browser smoke testing found two small release issues
in the v0.26.0 first-run wizard:

- Selecting the **Local** LLM preset with a blank API key now clears any
  previously-stored generic `llm_api_key`. Without this, a user switching
  from a hosted provider to a local OpenAI-compatible endpoint could
  accidentally keep sending the stale hosted provider key as a Bearer token.
- The bundled single-file UI now declares an empty data-URI favicon so a
  clean browser session does not emit a `/favicon.ico` 404 console error.

### Tests
- Added static interface assertions for the favicon and local-preset stale-key
  clearing guard.
- Rebuilt the wheel/sdist and validated package data is present.
- Installed the built wheel into a clean virtualenv and smoke-tested:
  console scripts, admin config/auth/export/restart, `NUR_CONFIG_DIR`
  soul persistence, first-run wizard, explicit mock chat, and local-preset
  stale-key clearing through the browser.
- Full suite: 1383 passed.

### Bumped
- `pyproject.toml` version → 0.26.1.

---

## v0.26.0 — 2026-04-24 (First-run setup wizard)

A new user opening the app for the first time sees a four-step guided
wizard instead of a confused-looking chat with mock responses and a
buried admin drawer. The steps:

1. **Welcome** — brief intro, "Skip for now" option (sessionStorage flag)
2. **Connect your LLM** — four preset tiles (Local / Hosted / MiniMax / Mock)
   that auto-fill base URL and model suggestions, plus an API-key field
   whose label and placeholder change per preset.
3. **Name your agent** — tabbed UI; default tab "Describe in plain
   English" uses the existing `/admin/soul/draft` LLM endpoint to generate
   a full soul from a sentence; fallback tab "Fill manually" takes just a
   name + short identity.
4. **Done** — summary of agent name + LLM backend + endpoint + model, then
   "Start chatting" marks `setup_completed=true` and closes.

### Trigger logic
The wizard auto-opens when `applySettings()` sees `setup.required === true`
AND the user hasn't dismissed it this session AND the admin drawer isn't
open AND the current path isn't `/admin`. The dismiss flag lives in
sessionStorage so a reload reopens the wizard; a permanent dismissal
happens when the user clicks "Start chatting" (which also flips
`setup_completed`). A "Launch Setup Wizard" button in the Setup section
of the admin drawer lets users rerun it manually anytime.

### Safe config merging
`POST /admin/config` overwrites unspecified non-secret fields with
Pydantic defaults (destructive). The wizard works around this by
fetching the current config via `GET /admin/config`, merging wizard
fields on top, then POSTing the full payload. Secrets stay empty unless
the user typed a new value (the server preserves them). Secret-clear flags
are set appropriately when switching presets so stale keys from the opposite
backend don't linger.

### Files
- `interface/static/index.html`: added wizard CSS, HTML (new
  `#wizardOverlay` with four panels and a stepper), JS state machine
  (~200 lines: `openWizard`, `wizardGoto`, `wizardSelectPreset`,
  `wizardSelectTab`, `wizardSaveLLM`, `wizardDraftSoul`, `wizardSaveSoul`,
  `wizardRenderSummary`, `wizardFinish`, `wizardDismiss`), and the
  auto-open hook in `applySettings()`. Added "Launch Setup Wizard"
  button in the drawer's Setup section.

### Not included
- No new backend routes — reuses `/admin/config`, `/admin/soul`,
  `/admin/soul/draft`. No test added (wizard is pure UI; existing
  backend integration is covered by admin-soul and admin-config tests).

---

## v0.25.2 — 2026-04-24 (Chat UI respects soul name; mock mode banner)

### P1 — Chat UI now reflects the configured agent name
The chat UI hardcoded "Nūr" in six places (browser tab, header logo,
empty-state title, input placeholder, message sender label, typing avatar).
After saving a different name in Settings → Identity, those spots stayed
"Nūr". Fixed: the frontend fetches the soul on every page load (via
`loadSettings` → `loadSoul`) and applies the name to all six spots.
Name updates again after every successful soul save.

- `interface/static/index.html`: added `applyAgentName(name)` that updates
  `document.title`, `#headerLogo`, `#emptyTitle`, `#msgInput` placeholder,
  and all `.nur-av` avatar initials.
- `addMessage()` now uses the dynamic `agentName` variable for the
  sender label and avatar initial instead of hardcoded literals.
- `loadSoul()` calls `applyAgentName(data.soul.name)` on success.
- `saveSoul()` calls `applyAgentName(payload.name)` on success; also
  fixed a silent `ReferenceError` — `loadAdminOverview` was called but
  never defined; replaced with `loadSettings()`.

### P2 — Mock mode banner
Users who have not configured an LLM see "I understand." on every message
with no explanation why. Fixed two ways:

- **Banner**: a non-intrusive amber bar appears at the top of the chat area
  when the server is running in auto-mock mode (no LLM key/URL configured).
  It links to Settings and disappears once a real LLM is configured and saved.
- **MockLLMBackend default response**: changed from `"I understand."` to
  `"[Mock mode] No LLM is connected. Open Settings to configure one."` —
  self-explanatory even without the banner.
- `interface/api.py`: `_admin_config_payload` now includes `mock_mode: bool`
  (`true` when `llm_backend == "auto"` and no LLM is configured). Frontend
  reads this field in `applySettings()`.

### Tests
- `test_dual_process.py`: updated `test_default_mock_backend` assertion for
  new mock response text.
- `test_regressions.py`: updated candidate-propagation test to derive the
  expected string from `MockLLMBackend().generate()` rather than hardcoding
  `"I understand."`.

---

## v0.25.1 — 2026-04-24 (Identity propagation: name actually changes)

v0.25.0 gave the admin UI a beautiful identity-authoring flow — and
then lied to the user about what it did. The generator prompt template
hardcoded `"You are Nūr."` in its first line, so even after saving a
different name the LLM was told it is still Nūr. The self-check and
memory-digestion prompts had the same problem. The admin UI warned
"full server restart may be required" as a workaround, but no restart
would have fixed it: the hardcoded string survives restarts too.

### Fixed
- **Hardcoded agent name in prompt templates.** Replaced with
  `{agent_name}` placeholders and runtime substitution from
  `soul.name`:
  - `config/prompts/generator.md` — `"You are Nūr."` →
    `"You are {agent_name}."`
  - `config/prompts/self_check.md` — `"for Nūr"` →
    `"for {agent_name}"`
  - `config/prompts/digestion.md` — `"for Nūr"` →
    `"for {agent_name}"`
  - `core/dual_process/generator.py:build_system_prompt` resolves
    `agent_name` from `ctx.soul_profile.name` (falling back to
    `get_config().soul.name` if unset) and substitutes it into both
    the template path and the legacy fallback path.
  - `core/dual_process/self_check.py` substitutes `{agent_name}` on
    every self-check call.
  - `core/memory/digestion.py` substitutes `{agent_name}` and also
    uses `soul.name` for the role label in formatted conversation
    history (previously hardcoded `"Nūr"`).
- **`POST /admin/soul` now evicts the active session manager.** Matches
  the same pattern `POST /admin/config` already uses. After a save,
  `_session_manager.shutdown()` is called and the singleton cleared so
  the next chat turn builds a fresh pipeline that reads the new
  `soul.yaml` — **no process restart needed**. Response includes
  `reloaded_session_manager: true` so clients can show an accurate
  status.

### UX
- Admin UI no longer tells the operator to restart the server. The
  save toast now surfaces the honest contract: "The next chat turn
  will reflect the new identity across the whole prompt stack", and
  "Active chat sessions were rebuilt so they use the new identity
  immediately".
- When `NUR_CONFIG_DIR` is not set, the save toast adds a note
  suggesting it, so operators who care about surviving
  `pip install --upgrade` see the path to do it.

### Tests
- 5 new cases:
  - `TestAgentNamePropagation::test_generator_prompt_uses_soul_name_not_hardcoded` —
    constructs a `PipelineContext` with a non-default soul name,
    verifies the rendered first line is `"You are Iris"` and contains
    neither `"Nūr"` nor the raw `{agent_name}` placeholder.
  - `TestAgentNamePropagation::test_generator_template_has_no_hardcoded_nur_directive` —
    guards the template file itself (so a future edit can't reintroduce
    a hardcoded name and silently defeat the feature).
  - `TestAgentNamePropagation::test_self_check_prompt_uses_placeholder`
    and `test_digestion_prompt_uses_placeholder` — same guard for the
    other two templates.
  - `TestSoulSaveReloadsSessionManager::test_soul_save_shuts_down_active_session_manager` —
    installs a sentinel session manager, calls `admin_update_soul`,
    verifies `shutdown()` was called and `_session_manager` was
    cleared.
- Updated two existing tests in `test_config.py` that asserted the
  old hardcoded-`"Nūr"` contract to assert the new `{agent_name}`
  placeholder contract instead.
- **Full suite: 1383 passed.**

### Why this is the important fix
This release is small in line count but large in honesty. v0.25.0
built the non-coder identity-authoring UX, but the feature itself
didn't actually change the agent's name in practice — the operator
would pick "Iris", hit Save, and the assistant would still answer
"I'm Nūr." This release makes the UI tell the truth.

### Bumped
- `pyproject.toml` version → 0.25.1.

---

## v0.25.0 — 2026-04-24 (Identity authoring for non-coders)

Phase 2 of the seed-identity work. v0.24.0 gave the admin GUI a
functional but coder-centric soul form (key:weight textareas,
site-packages write path, opaque reason codes). This release makes
identity authoring usable by anyone who runs the server.

### Added
- **LLM-assisted "Describe your agent"** — primary path at the top of
  the Identity section. Operator writes one or two sentences in plain
  English; the configured LLM drafts a complete soul.yaml; the form
  populates so the operator can review, tweak, and save. Implemented
  as `POST /admin/soul/draft` → strict prompt template in
  `config/prompts/soul_from_description.md` → permissive JSON
  extraction (tolerates prose before/after) → validation through the
  same `AdminSoulUpdateRequest` schema as manual saves. Does **not**
  persist — review-and-save only.
- **Sliders for weight fields** — `core_values` and `initial_traits`
  are now dynamic rows with text input + range slider + per-row
  delete + "add" button. Weights read as 0.00-1.00 at 0.05 precision.
  Replaces the v0.24.0 `key: weight` per-line textareas, which were
  fine for developers but friction for anyone else.
- **`NUR_CONFIG_DIR` override layer in the loader** — set to any
  writable directory and every `config/*.yaml` or `config/prompts/*.md`
  found there takes precedence over the packaged default. Missing files
  fall through transparently. `/admin/soul` POST writes into
  `NUR_CONFIG_DIR` when set, so operator edits to soul.yaml survive
  `pip install --upgrade`.
- **Human-readable setup reasons** in the admin drawer. Codes like
  `default_soul`, `missing_config` are mapped to labels like "Set your
  agent's identity", "Save your runtime config"; unknown codes still
  fall through to the raw string.

### Refined
- Identity section rewritten for a non-technical reader. New intro
  copy frames the two-path choice (describe vs. fill). Manual form uses
  inline hint spans instead of terse column headers. Likes/dislikes/
  boundaries textareas explicitly labeled "one per line" and
  "short imperative sentences".
- Draft availability hint updates live as the operator edits the LLM
  section — the Generate button disables (with a clear reason string)
  until a backend + base URL + model is in place, or mock/minimax is
  selected.

### Safety / review posture
- The LLM draft is **never auto-saved**. The response is validated
  against the same schema as a manual save, including weight bounds
  and field lengths. If the LLM drifts or returns prose, the endpoint
  returns `502` with a specific error the UI surfaces. The operator
  always has the final edit before hitting Save Identity.
- The draft endpoint runs through whatever LLM the operator already
  configured; no separate model selection or separate key handling.

### Tests
- 8 new cases:
  - `TestAdminSoulDraft`: refuses when LLM not configured; parses
    well-formed JSON through the schema; rejects non-JSON replies;
    rejects schema violations (out-of-range weights); rejects too-short
    descriptions.
  - `TestConfigLoaderOverride`: override dir replaces packaged soul;
    falls through when file missing; nonexistent dir is silently
    ignored, not fatal.
- `pytest tests/test_interface.py tests/test_interface_v1.py -q`
  → 119 passed.

### Bumped
- `pyproject.toml` version → 0.25.0.

---

## v0.24.0 — 2026-04-24 (Admin: GUI for seed identity / soul.yaml)

Closes the install-UX gap where every fresh deployment ran under the
default name "Nūr" with the project author's voice and values, because
the only way to change seed identity was hand-editing
`config/soul.yaml`. This release adds a first-class admin-console path
for authoring the identity through the GUI.

### Added
- **`GET /admin/soul`** — returns the current seed identity (name,
  identity, voice, relational stance, likes, dislikes, boundaries,
  growth policy, core values, initial traits) plus an `is_default_name`
  flag and write-path notes. Bearer-guarded like every other
  `/admin/*` JSON endpoint.
- **`POST /admin/soul`** — writes a validated `soul.yaml` atomically
  (temp file + `os.replace`), resets the config singleton, and returns
  the freshly reloaded state. Validates via `AdminSoulUpdateRequest`:
  name required and non-blank, text fields bounded, lists of strings
  trimmed to non-empty items (max 32 each), core_values and
  initial_traits are `{key: float ∈ [0.0, 1.0]}` dicts (max 32 entries).
- **Admin drawer "Identity" section** — placed right after "Setup" so
  operators see it on first open. Manual form with name/identity/voice/
  relational stance/growth policy text fields, newline-separated likes/
  dislikes/boundaries, and `key: weight` one-per-line editors for
  core_values and initial_traits. Local validation of weights before
  POST; inline result panel. Identity loads automatically when the
  drawer opens.
- **`default_soul` setup reason** — `/admin/status` now reports
  `default_soul` in `setup.reasons` when `soul.name` is still the
  built-in "Nūr", so the existing first-run prompt highlights this
  alongside `missing_config` / `default_or_minimal_config`.

### Caveats (documented in the API response `notes` and the UI)
- `soul.yaml` is written to `<installed config/>/soul.yaml` — inside
  site-packages for a pip install — and will be overwritten by
  `pip install --upgrade`. A data-dir layer is phase 2.
- Some cognitive modules cache soul-derived constants at import time
  (e.g. `SPIKE_INTENSITY_THRESHOLD = get_config().spike_threshold`), so
  a full server restart may be required before every consumer picks up
  a new identity. The admin UI surfaces this note on every save.
- Phase 2 (LLM-assisted authoring: "describe your agent" → schema-validated
  soul draft) is deliberately not in this release so manual form-based
  authoring always works, including in mock mode without any LLM
  configured.

### Tests
- 7 new tests in `tests/test_interface.py::TestAdminSoul` and
  `::TestAdminSetupDefaultSoul`:
  - `GET` returns non-empty identity with expected shape.
  - `POST` writes the YAML file and round-trips the values.
  - Out-of-range weights rejected; negative rejected.
  - Empty / whitespace-only name rejected.
  - List entries trimmed; empty entries dropped.
  - `default_soul` reason surfaces when name is "Nūr".
  - `default_soul` reason clears when name is changed.
- `pytest tests/test_interface.py tests/test_interface_v1.py -q`
  → 111 passed.

### Bumped
- `pyproject.toml` version → 0.24.0.

---

## v0.23.2 — 2026-04-24 (CLI: argparse on both console scripts)

### Added
- **`nur --help` / `nur --version`.** The `nur` console script now has a real
  argparse front end, so `nur --help` no longer drops into the interactive
  runtime loop. Adds `--config <path>` (default `runtime_config.yaml`) so the
  YAML can live anywhere, and `--version` reporting `project-nur <X.Y.Z>` from
  installed metadata.
- **`nur-web --help` / `--version` / `--host` / `--port`.** Matching argparse
  surface on the web entrypoint. Defaults are unchanged (`127.0.0.1:8000`), so
  existing invocations still work, but operators can now run e.g.
  `nur-web --host 0.0.0.0 --port 9000` without hand-editing code.

### Changed
- `tests/test_interface.py::TestEntryPoints::test_main_runs_uvicorn_on_localhost`
  now monkeypatches `sys.argv` before calling `main()` (needed because `main()`
  reads `sys.argv` through argparse). Added
  `test_main_honors_host_and_port_flags` covering the new CLI knobs.

### Verified
- `pytest tests/test_interface.py tests/test_interface_v1.py -q` → 104 passed.
- Spot-checked `--help`, `--version`, `--config`, `--host`, `--port` end-to-end
  through argparse; no subprocess launched.

---

## v0.23.1 — 2026-04-24 (Packaging: ship runtime data assets in wheel)

### Fixed
- **Release blocker: wheel was Python-only.** The v0.23.0 wheel excluded
  every non-`.py` file the runtime loads via `__file__`-relative paths:
  `interface/static/index.html` (the UI + admin console shell),
  `config/*.yaml` (modulators, attachment, profiles, values seed, soul,
  semantic memory), and `config/prompts/*.md` (10 prompt templates).
  A user doing `pip install project-nur && nur-web` would have hit a
  `FileNotFoundError` on the first `/` request and again inside the
  config loader. Added `[tool.setuptools.package-data]` in
  `pyproject.toml` mapping `interface → static/*` and
  `config → *.yaml, prompts/*.md`. The built wheel now ships 17 data
  assets; size 227 KB → 253 KB.

### Verified
- Built wheel with `python3 -m pip wheel --no-deps`, extracted, and
  confirmed all 17 expected assets are present and non-empty.
- `python3 -m pip install -e .` followed by resolving asset paths via
  the package `__file__` attributes: all present.
- `python3 -m pytest tests/test_interface.py tests/test_interface_v1.py -q`
  → 103 passed.

---

## v0.23.0 — 2026-04-24 (Release hygiene: metadata, docs, admin UX)

Release-preparation pass: packaging metadata for public distribution, admin
console visual split between safe and destructive controls, and
documentation consistency between SECURITY.md and the live bearer-gated
route set. No runtime behavior change.

### Added
- **Packaging metadata.** `pyproject.toml` gains `readme`, `license`,
  `authors`, `keywords`, PyPI `classifiers`, and `project.urls`
  (Homepage, Repository, Issues, Documentation) pointing at
  `github.com/balfiky/nur`. MIT license is declared via the existing
  `LICENSE` file rather than an inline string.
- **Admin Danger Zone.** Reset-session and delete-user controls are split
  out of the general Maintenance section into a visually distinct
  `Danger Zone` with a red-tinted border, explicit confirmation format
  hints, and a separate result panel. The client now rejects an empty or
  mismatched confirmation string locally before touching the network,
  matching the server-side `RESET <session_key>` /
  `DELETE <platform>:<user_id>` contract.
- **Eval runner tests.** Added three provider-backend cases covering
  missing `--base-url`, missing `--model`, and the generic `LLM_API_KEY`
  fallback (`tests/test_evals_runner.py`).

### Fixed
- **SECURITY.md bearer-gated route list.** The explicit enumeration of
  routes requiring `Authorization: Bearer <api_key>` omitted `/admin/*`
  even though every admin JSON endpoint is in fact guarded by
  `_require_bearer`. Corrected the list and clarified that `GET /admin`
  (the HTML shell) stays open so the bundled UI can bootstrap and prompt
  for the token client-side.

### Tests
- `python3 -m pytest tests/test_interface.py tests/test_interface_v1.py tests/test_evals_runner.py -q`
  → 120 passed, 1 warning.

---

## v0.22.0 — 2026-04-23 (Pre-publication launch-blocker fixes)

Fixes three release-acceptance blockers surfaced by a pre-publication audit.
The cognitive/eval layer was healthy; the runtime packaging and the
auth/tool security posture were not. This release makes the public launch
safe.

### Fixed
- **B1 — packaging.** `python3 -m pip install -e ".[dev]"` no longer fails
  with `Multiple top-level packages discovered in a flat-layout`. Added
  `[tool.setuptools] py-modules = ["main", "pipeline"]` and an explicit
  `[tool.setuptools.packages.find]` include/exclude list in
  `pyproject.toml:21-29`. The `nur` and `nur-web` console scripts
  advertised in README's 5-Minute Start are installed again.
- **B2 — auth coverage on legacy routes.** README previously claimed that
  setting `api_key` protects every endpoint except `/v1/health` and
  `/v1/ready`. In fact only the `/v1/*` router enforced it; `/chat`,
  `/debug`, `/config` (GET + POST), `/session/end`, `/rest`, and `/ws`
  stayed open — an unauthenticated `POST /config` could even clear the
  api_key and disable auth entirely. Added a shared `_require_bearer`
  dependency in `interface/api.py` and attached it to every mutating
  legacy route. `/ws` does an inline check on a `?token=` query param
  because FastAPI does not run dependencies for WebSocket handlers. Only
  `GET /` (the static HTML shell) stays open so the UI can bootstrap.
- **B2 — bundled UI keeps working under auth.** `interface/static/index.html`
  now has a `Web/API Bearer Token` field in the Settings drawer plus an
  `authHeaders()` helper. The token is persisted to `localStorage` and
  attached to every `fetch` to `/chat`, `/config`, `/debug`, `/session/end`,
  and `/rest`.
- **B3 — agentic tool runtime is off by default.** Three new
  `RuntimeConfig` fields (`tools_enabled`, `tools_workspace`,
  `shell_tool_enabled`) gate the production factory in
  `runtime/tools.py`. Default is `tools_enabled=false`, so chat messages
  that match the regex intents in `core/dual_process/tool_loop.py` no
  longer trigger `fs.delete_path`, `shell.run_command`, etc. When the
  operator flips `tools_enabled: true`, filesystem tools are sandboxed
  to `tools_workspace` (default `<data_dir>/workspace`); paths that
  resolve outside it are refused. `shell.run_command` is a separate
  opt-in because subprocess execution has a larger blast radius than
  bounded file I/O.

### Added
- `tools/builtin/filesystem.py:create_handlers(workspace)` — returns
  sandboxed handlers that validate every path against a workspace root
  before performing I/O. Relative paths are resolved against the
  workspace so workspace-relative usage works.
- `tests/test_interface_v1.py::TestLegacyEndpointAuth` — 10 regression
  tests locking in the B2 fix: `/chat`, `/debug`, `GET /config`,
  `POST /config`, `/session/end`, `/rest`, and `/ws` all reject
  unauthenticated requests when `api_key` is set; the static `/` stays
  open; a valid token is accepted on both HTTP and WS.
- `SECURITY.md` — new "Agentic Tool Runtime" section documenting the
  regex-intent layer, the sandbox, and the full auth coverage.

### Documentation
- README 5-Minute Start, auth-coverage paragraph, and tool-safety
  callout rewritten against the new behavior.
- Test counts reconciled to **1331** across `README.md`,
  `TECHNICAL_NOTE.md`, and `PAPER_DRAFT.md`.
- README eval example updated to `python3 -m evals --backend mock --tag
  phase11` (the CLI has required `--backend` since v0.19).

### Also fixed during the same release audit
- **`nur` console SIGINT/shutdown hang.** The console loop used
  `asyncio.to_thread(input)`, which left a worker thread blocked on
  stdin and in turn blocked `loop.shutdown_default_executor` on exit.
  `runtime/channels/console.py` now polls stdin via `select` with a
  200 ms timeout so setting `_running = False` (from signal handlers or
  `/quit`) takes effect within that window. `_signal_shutdown` no longer
  fire-and-forgets a `ConsoleChannel.stop()` coroutine.
  `runtime/app.py::_teardown` replaces the old task-cancel scattershot
  with a single graceful-then-cancel path that sets uvicorn's
  `should_exit = True`, awaits background tasks with a 5 s timeout,
  then hard-cancels stragglers. SIGINT now exits in ~250 ms.
- **`POST /config` silently resetting tool settings.** The web Settings
  form does not surface `tools_enabled` / `tools_workspace` /
  `shell_tool_enabled`, so a plain save used to reset them to the safe
  defaults (False / ""). `ConfigUpdateRequest` now defaults those
  fields to `None` and the handler treats `None` as "leave the existing
  YAML value intact"; explicit booleans/strings still take effect. Two
  new tests in `test_interface.py` lock this in.
- **Dependency bounds.** Added upper caps on every production
  dependency (`fastapi<0.140`, `starlette<2.0`, `httpx<1.0`,
  `uvicorn<1.0`, `pyyaml<7.0`, `requests<3.0`, `anyio<5.0`,
  `pydantic<3.0`) plus an inline comment recording the exact
  known-good combination (`fastapi 0.135.2 / starlette 1.0.0 / uvicorn
  0.42.0 / httpx 0.28.1 / anyio 4.13.0 / pydantic 2.12.x`). Keeps a
  fresh public `pip install` from resolving to an untested combo.

### Tests
- Full suite: **1331 passed** (`python -m pytest`) across two clean
  runs — 12 new tests total, zero regressions.

---

## v0.21.0 — 2026-04-08 (Versioned integration API + Python client)

Adds a stable `/v1/*` HTTP surface and a thin Python client so Project Nūr can be embedded into dashboards, chatbots, eval harnesses, and orchestrators without depending on the bundled web UI or scraping the legacy endpoints.

### Added
- `interface/v1.py` — versioned FastAPI router built by `build_v1_router(session_manager_getter, config_getter, serve_started_at)`. Endpoints:
  - `GET /v1/health`, `GET /v1/ready` — liveness / readiness (no auth even when a key is set).
  - `POST /v1/chat` — run one turn through the pipeline; returns `response`, `session_key`, `emotion_label`, `energy`, and optional `debug`.
  - `GET /v1/sessions`, `GET /v1/sessions/{key:path}`, `POST /v1/sessions/{key:path}/reset`.
  - `POST /v1/sessions/end`, `POST /v1/sessions/rest`.
  - `GET /v1/profiles/self`, `GET /v1/profiles/person`.
  - `GET /v1/memory/long_term`, `GET /v1/memory/relationship`.
  - `GET /v1/tools` — enumerate registered capabilities.
  - `GET /v1/config` — runtime config with secrets redacted.
- `interface/client.py` — `NurClient` synchronous Python wrapper (stdlib + `requests`) with typed `NurAPIError`, context-manager support, and one method per endpoint.
- `runtime/config.py` — new fields `api_key` (bearer token; empty = auth disabled) and `cors_origins` (list; empty = same-origin only). `api_key` joins the secret-redaction set alongside `telegram_token` and `llm_api_key`.
- `interface/api.py` — mounts the v1 router at app startup, installs CORS middleware driven by `cors_origins`, and extends the `POST /config` schema with `api_key`, `cors_origins`, and `clear_api_key` so the bearer token can be rotated through the settings UI.
- `tests/test_interface_v1.py` — 26 tests covering every endpoint, the opt-in Bearer middleware, secret redaction, and the `NurClient` error path.

### Design notes
- **Auth is opt-in.** With `api_key=""` the v1 surface is wide open (useful for local dev and CI). Set it, and every endpoint except `/v1/health` and `/v1/ready` rejects requests without `Authorization: Bearer <key>`. The dependency re-reads the current config on every request, so rotating the key through `POST /config` takes effect immediately — no restart.
- **Single source of truth.** The v1 router queries the live `SessionManager`, so integrators see the same state as the web UI and the Telegram channel. No duplicate cache layer.
- **Legacy endpoints unchanged.** The root `/`, `/chat`, `/config`, `/ws`, etc. still serve the bundled web UI for backward compatibility.

### Tests
- Full suite: **1251 passed** (`python -m pytest`) — 26 new tests, zero regressions.

---

## v0.20.6 — 2026-04-08 (Second-pass neutral audit — real resource bugs)

Neutral re-review of the codebase surfaced four real bugs the earlier pass missed. Focus: resource hygiene under sustained use.

### Fixed
- `tools/builtin/web_provider.py` — **`fetch()` silently ignored the 2 MB cap**. The previous implementation used `requests.get(stream=True)` followed by `resp.content[:CAP]`, but accessing `.content` buffers the entire body before the slice. A large page would consume memory proportional to its full size, not the cap. Rewritten to stream via `iter_content` and stop once `_MAX_FETCH_BYTES` is reached.
- `tools/builtin/web_provider.py` — `search()` / `fetch()` now wrap the `Response` in a context manager so the HTTP connection is returned to the pool on every exit path, including errors.
- `tools/builtin/web_provider.py` — added `RequestsWebProvider.close()` to release the underlying `requests.Session` on shutdown.
- `tools/builtin/filesystem.py` — `_read_file` used `open(path).read()` without a context manager, leaving the file handle reliant on GC timing. Wrapped in `with`.
- `core/llm_client.py` — added `LLMClient.close()` so session evictions can release the persistent HTTP connection pool instead of leaking it for every evicted user.
- `pipeline.py` — `CognitivePipeline.close()` now releases the LLM backend's HTTP session (deduped when fast and primary share the same instance) and any closable resources registered via `ToolExecutor._owned_resources`.
- `runtime/tools.py` — `create_tool_executor` registers the `RequestsWebProvider` on `executor._owned_resources` so pipeline shutdown can drain its connection pool.
- `interface/api.py` — `_start_telegram` failures were silently swallowed by the lifespan task. Added a `done_callback` that logs exceptions immediately, plus a broadened `except` in `_stop_telegram_channel` so a task that already failed cannot break the shutdown path.

### Added
- `tests/test_web_provider.py` — regression coverage for the `_MAX_FETCH_BYTES` cap, `Response` context-manager usage, `close()`, DDG parsing, and HTML-to-text stripping.
- `tests/test_llm_client.py::TestLLMClient::test_close_releases_session` — guards the new `close()` contract.

### Audit Notes (false positives discarded)
- "Race on `_pending_message_count`" — rejected. asyncio is single-threaded; no `await` exists between the backpressure check and the increment.
- "Path traversal in `fs.*`" — rejected. Per `docs/internal/CLAUDE.md` the agent is a local personal assistant; filesystem access is by-design.

### Tests
- Full suite: **1225 passed** (`python -m pytest`) — 9 new tests added, zero regressions.

---

## v0.20.5 — 2026-04-08 (Deep review, tool wiring, test alignment)

Full-codebase audit against the stated cognitive-architecture purpose, followed by a bug sweep of modified files.

### Added
- `tools/builtin/web_provider.py` — `RequestsWebProvider` using DuckDuckGo HTML lite for search, `requests.Session` for fetch, and a lightweight HTML-to-text extractor. No new dependencies.

### Fixed
- `runtime/tools.py` — the production `create_tool_executor` now wires `RequestsWebProvider` so `web.search` / `web.fetch` / `web.extract_text` work in the runtime surface (previously no provider → tools were unavailable).
- `core/llm_client.py` — log HTTP body on non-200 LLM responses before raising, so upstream errors surface instead of being opaque.
- `core/dual_process/tool_loop.py` — default-path trigger patterns for `fs.list_dir` ("what's in /tmp", "list the files", "show me files"), and broadened the web-search pattern to cover "internet", "online", "google", "browse".
- `tests/test_interface.py` — `test_serves_html` and `test_has_v2_sections` assertions aligned with the redesigned UI strings ("Nūr", "Unresolved", "Settings", "Save").

### Tests
- Full suite: **1216 passed** (`python -m pytest`).

---

## v0.20.4 — 2026-04-03 (Web settings surface for runtime config)

Moves the practical runtime configuration out of scattered YAML/manual file edits and into the existing web interface.

### Added
- `GET /config` and `POST /config` in `interface/api.py`
- browser-based settings drawer in `interface/static/index.html`
- `RuntimeConfig.to_yaml_dict()`, `write_yaml()`, `to_public_dict()`, and `secret_status()` in `runtime/config.py`

### Behavior
- Web UI can now edit:
  - Telegram token / allowlist / polling settings
  - LLM backend type, base URL, model, and keys
  - runtime queue/session limits
  - debug host/port
  - proactive behavior settings
- Secret values are never returned to the browser
- Blank secret fields preserve existing stored values
- Explicit clear toggles remove stored secret values
- Saving via the web UI writes back to `runtime_config.yaml`
- Saving also reloads the standalone web surface's `SessionManager`

### Tests
- Added focused config-UI coverage in `tests/test_interface.py`
- `tests/test_interface.py` and `tests/test_runtime_config.py` pass

### Docs
- `README.md` now documents the Settings button and `/config` endpoints

---

## v0.20.3 — 2026-04-03 (Post-review housekeeping)

### Fixed
- `interface/api.py` — replaced deprecated `@app.on_event("shutdown")` with `lifespan` context manager

### Docs
- `docs/internal/CLAUDE.md` — updated test count from 1194 to 1210
- `README.md` — updated Phase 11 status from "underway" to "complete"

---

## v0.20.2 — 2026-04-02 (Phase 11 reporting utility)

Adds a focused developer script for inspecting Phase 11 human-likeness behavior turn by turn.

### Reporting utility (`tests/run_phase11_report.py`)
- Added `python -m tests.run_phase11_report`
- Runs the existing `phase11` eval scenarios and prints:
  - the normal pass/fail assertion report
  - a detailed per-turn trace with event classification, appraisal target/move, selected response strategy, relationship summary, and session-end digestion
- Supports `--scenario <id>` for a single Phase 11 scenario
- Supports `--json` for structured output

### Tests
- Added `tests/test_phase11_report.py`

### Docs
- `README.md` — added the Phase 11 trace-report command
- `README.md` — future roadmap now reflects that the lean Phase 11 track is complete and the next step is soak/calibration, not more architecture

---

## v0.20.1 — 2026-04-02 (Phase 11 eval automation and strategy/appraisal fixes)

Adds an automated Phase 11 human-likeness regression pack and tightens a few strategy/appraisal edges it exposed.

### Eval harness (`evals/types.py`, `evals/runner.py`, `evals/scenarios.py`)
- `EvalTurn.end_session` allows multi-session scenarios inside the existing eval runner
- `run_scenario()` now persists `initial_trust` correctly before turns run
- Runner closes the pipeline after each scenario
- Added 6 `phase11` scenarios covering:
  - external distress vs relational harm
  - low-trust hostility -> `set_boundary`
  - mixed-affect overwhelm -> `ground`
  - actionable request -> `practical_help`
  - persisted open loop -> `challenge_gently`
  - apology/repair closing an open loop across sessions
- New one-command regression path: `python -m evals --tag phase11`

### Fixes surfaced by the new evals
- `core/strategy.py`
  - external non-directed complaints validate more often instead of defaulting to reassure
  - mixed affect + vulnerability grounds earlier when the system is already activated
  - apologies into active open loops choose `repair`
- `core/appraisal.py`
  - assistant-addressed apologies now target the assistant correctly
- `core/memory/digestion.py`
  - apology-based resolution can resolve existing relationship open loops even when appraisal target is imperfect
- `pipeline.py`
  - self-check regeneration now preserves `response_strategy` in the correction context

### Docs
- `README.md` — added Phase 11 eval command
- `docs/internal/CLAUDE.md` — updated eval suite counts and Phase 11 tag guidance

---

## v0.20.0 — 2026-04-02 (Phase 11: appraisal, relationship memory, and response strategy)

Starts the lean Phase 11 track aimed at making Nūr feel more human without adding prompt theater or heavy new subsystems. This release improves how Nūr interprets social meaning, carries relationship continuity across sessions, and selects a concrete response approach before generation.

### Deterministic social appraisal (`core/appraisal.py`, `core/types.py`, `pipeline.py`)
- Added `AppraisalFrame` to represent turn-level social interpretation before emotional update:
  - `primary_target`, `social_move`, `inferred_intent`, `blame`, `controllability`
  - `expectation_violation`, `vulnerability`, `affiliation_bid`, `mixed_affect`
  - explicit `targets_assistant` flag for relational vs non-relational affect
- Added `core/appraisal.py` — deterministic appraisal pass with no mandatory LLM call
- Pipeline now runs `contagion -> appraisal -> event classification`
- External distress such as "I'm furious about work, not at you" no longer damages trust or gets misclassified as conflict
- Assistant-directed conflict, hostility, gratitude, apology, and warmth now route through appraisal-aware event classification

### Relationship-arc memory (`core/memory/relationship.py`, `core/memory/digestion.py`, `pipeline.py`)
- Added `RelationshipMemory` with two SQLite-backed stores:
  - `relationship_events` — durable arc events like `rupture`, `repair`, `commitment`, `recurring_tension`
  - `open_loops` — unresolved relational threads and pending follow-up commitments
- Added new shared types:
  - `RelationshipEvent`
  - `OpenLoop`
  - `RelationshipContext`
- Session digestion now extracts only high-value relational updates:
  - rupture after assistant-directed conflict / strong negative feedback
  - repair after apology / resolution
  - explicit follow-up commitments from assistant replies
  - recurring tension when the same conflict pattern returns
- Pipeline retrieves a compact `relationship_context` before generation and exposes it in debug output
- Generator prompt now includes relationship context alongside regular long-term memory

### Response strategy selector (`core/strategy.py`, `core/types.py`, `pipeline.py`)
- Added `ResponseStrategy` enum: `validate`, `reassure`, `repair`, `ground`, `give_space`, `practical_help`, `challenge_gently`, `set_boundary`
- Added `core/strategy.py` — deterministic strategy selection from appraisal + modulators + person profile + relationship context (0 LLM calls)
- Priority-ordered selection: boundary → repair → give_space → ground → validate → reassure → practical_help → challenge_gently → fallback
- `STRATEGY_INSTRUCTIONS` dict maps each strategy to a compact prompt directive
- Pipeline runs strategy selection after defense (Step 13b), before generation
- Strategy instruction injected into generator system prompt as `## Response Strategy` section
- `DebugState.response_strategy` captures the chosen strategy for inspection
- Debug API serializes `response_strategy` field

### Schema migration (`core/schema.py`)
- Schema version bumped `1 -> 2`
- Added a real migration path instead of placeholder version bumps
- Migration creates:
  - `relationship_events`
  - `open_loops`
  - indexes for per-person retrieval

### Debug / observability (`runtime/debug/api.py`)
- Session debug memory counts now include:
  - `relationship_events`
  - `open_loops`
- `last_turn` debug payload now includes `relationship_context`

### Tests
- Added `tests/test_appraisal.py`
- Added `tests/test_relationship_memory.py`
- Added `tests/test_strategy.py` — 27 tests covering all 8 strategies, priority ordering, pipeline integration, generator prompt injection, debug serialization
- Expanded integration coverage in:
  - `tests/test_pipeline.py`
  - `tests/test_dual_process.py`
  - `tests/test_debug_api.py`
  - `tests/test_phase0.py`
  - `tests/test_agentic_tools_phase2.py`

---

## v0.19.0 — 2026-04-02 (Phase 10: Calibration and policy shaping)

Tunes decision heuristics based on eval results, extracts arbiter thresholds as named constants, and adds calibration boundary regression scenarios.

### Calibration changes
- `core/action_variables.py` — persistence_drive base 0.50 → 0.45 (plans block under moderate fatigue instead of always continuing)
- `core/action_variables.py` — action_urgency base 0.30 → 0.25 (defer path reachable when tired + calm)
- `core/proactive.py` — valence boost +0.05 when `valence < 0.3` (negative mood amplifies unfinished-business triggers)
- `core/tool_memory.py` — `_TRUST_POSITIVE_TOOL` 0.01 → 0.015 (tool trust asymmetry 1:3 → 1:2, still conservative)
- `core/dual_process/tool_loop.py` — arbiter thresholds extracted as named constants: `REFUSE_RISK_TOLERANCE`, `CLARIFY_AUTONOMY_BIAS`, `CLARIFY_WRITE_THRESHOLD`, `DEFER_URGENCY_THRESHOLD`

### Calibration boundary scenarios (`evals/scenarios.py`)
- 8 new scenarios in Suite 7 (calibration regression):
  - Persistence drive below/above blocking threshold at different energy levels
  - Defer path reachable with low arousal + low energy
  - No defer at moderate state
  - Negative valence boundary for proactive boost
  - Refuse destructive with low certainty + trust
  - Clarify when autonomy bias drops below threshold
  - Tool trust positive delta is 0.015

### Documentation
- `docs/internal/CALIBRATION_NOTES.md` — detailed notes on what was tuned, original vs new values, rationale, tradeoffs

### Tests
- 9 new tests in `tests/test_evals.py` (8 calibration integration + 1 count check)
- 28/28 eval scenarios passing (72/72 assertions)
- Full suite: 1153 tests passing

---

## v0.18.0 — 2026-04-02 (Phase 9: Evaluation, calibration, and benchmark harness)

Adds a dedicated evaluation layer that makes Nūr measurable, comparable across revisions, and easier to tune. No new user-facing features — purely instrumentation and regression infrastructure.

### Evaluation framework (`evals/`)
- `evals/types.py` — `EvalScenario`, `EvalTurn`, `EvalAssertion`, `EvalResult`, `EvalReport`, `EvalMetrics`, `ModulatorRange`
  - 14 assertion kinds: tool_used, tool_not_used, tool_category, decision, modulator_range, unresolved_created/resolved, task_plan_created/continued/completed, proactive_triggered/suppressed, debug_field, response_contains, response_not_empty, custom
  - Scenarios describe initial state, conversation turns, expected tool/emotional/task/proactive behavior, with range-based assertions
- `evals/runner.py` — deterministic runner executing scenarios through the real `CognitivePipeline`
  - `run_scenario()`, `run_scenarios()`, `run_by_tag()` — single, batch, and tag-filtered execution
  - Per-assertion checking against pipeline response + debug state
  - Proactive evaluation support with configurable idle simulation
  - Performance metrics collection: LLM calls, tool calls, latency, stage timings, defense activations
- `evals/reporting.py` — text and JSON report generators
  - Text: pass/fail summary, per-scenario metrics, failure details
  - JSON: full structured output with turn modulators, assertion results, metrics
- `evals/__main__.py` — CLI entrypoint: `python -m evals [--tag TAG] [--json] [--list]`

### Golden behavior suites (`evals/scenarios.py`)
- 20 scenarios across 6 suites:
  - **Emotional core** (5): warm greeting, hostile spike, escalation, energy drain, de-escalation recovery
  - **Tool loop** (6): file read, list dir, conversational no-trigger, web search, failure emotional effects, debug trace population
  - **Task planning** (2): multi-step plan creation, single-step no-plan
  - **Proactive** (2): suppressed when calm, suppressed when low energy
  - **Defense/resolution** (2): defense with low trust, contradiction unresolved tension
  - **Relationship** (3): high trust positive, low trust guarded, independent user states
- 56 assertions total, all passing

### Performance counters
- LLM call count, tool call count, total latency, per-stage timings
- Proactive count, task loop count, unresolved items created, defense activations
- All counters appear in both text and JSON report output

### Tests
- 52 new tests in `tests/test_evals.py`
  - Framework unit tests: types, assertion checking, modulator ranges
  - Reporting tests: text format, JSON validity, metrics display
  - Scenario definition tests: counts, IDs unique, tags present
  - Integration tests: 12 scenarios run through real pipeline with mock backend
  - Runner tests: batch, tag filtering, metrics collection
- Full suite: 1144 tests passing

---

## v0.17.2 — 2026-04-02 (Phase 8 correctness fix: proactive active-work guard)

Fixes the remaining Phase 8 race where a long proactive run could still be
evicted by the idle timer mid-execution.

### Proactive active-work accounting (`runtime/sessions/user_session.py`, `runtime/sessions/manager.py`)
- `UserSession` now tracks active work with an explicit counter instead of a
  single shared boolean
- Normal message processing and proactive execution both participate in the
  same active-work lifecycle
- Idle-time eviction and proactive sweeps now consult the explicit active-work
  state instead of only `_processing`
- `_run_proactive()` marks session work active for the full duration of
  pipeline execution and callback delivery, then clears it in `finally`

### Regression coverage (`tests/test_agentic_tools_phase8.py`)
- Added a regression test proving `_timeout_evict()` will not evict a session
  while proactive execution is in-flight

### Tests
- `pytest --collect-only -q` → `1092 tests collected`
- `tests/test_agentic_tools_phase8.py` → `65 passed`

---

## v0.17.1 — 2026-04-02 (Phase 8 correctness fixes: callback wiring, serialization, decay)

Fixes three correctness issues in the Phase 8 proactive behavior implementation.

### Fix 1: Proactive delivery wiring (`runtime/app.py`)
- `NurApp` now passes `_deliver_proactive` as `proactive_callback` to `SessionManager`
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

Adds bounded proactive behavior — Nūr can now initiate actions and follow-up messages based on unresolved tension, pending tasks, commitments, and idle-time patterns. All autonomy is explicitly bounded and inspectable.

### Proactive trigger model (`core/types.py`)
- `ProactiveTriggerSource` enum: unresolved_item, pending_task, commitment, temporal, emotional_salience
- `ProactiveTrigger` dataclass: source, description, intensity, optional item reference
- `ProactiveAction` dataclass: action_type (follow_up, continue_task, suggest, autonomous_step, none), trigger, message, rationale
- `ProactiveTrace` dataclass: triggers found, action taken, suppressed reasons, limits applied, idle time, count

### Proactive evaluation layer (`core/proactive.py`)
- `evaluate_proactive()` — deterministic evaluation (zero LLM calls) of whether Nūr should initiate behavior
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
- `NurApp` starts proactive loop as background task when `proactive_enabled=True`
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

## Unreleased

### Runtime and web integration hardening
- Web interface now routes `/chat`, `/debug`, `/session/end`, `/rest`, and `/ws`
  through `SessionManager` by `user_id` + `chat_id` instead of a shared global pipeline
- Runtime now wires builtin tool executors on the real app/session path via `runtime/tools.py`
- `UserSession`/`SessionManager` run synchronous pipeline work on a dedicated thread pool
  rather than the event loop default executor
- Session engine snapshots now persist unresolved items and restore them correctly
- Spike memories are no longer duplicated on `end_session()`
- `CognitivePipeline` is explicitly single-user/session scoped; evals and tests now use
  separate pipelines where cross-user isolation is required

### Safety and test harness
- `shell.run_command` is categorized as destructive and no longer uses `shell=True`
- Tool summaries no longer leak raw tool output back into generator context
- FastAPI interface/debug tests no longer depend on `TestClient` under the conda env;
  they call endpoint functions directly with async fixtures instead

### Validation
- Full suite passes in `conda activate venv`: `1210 passed`

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

Tool episodes now persist into memory, self-model, and unresolved tension — making tool behavior part of Nūr's ongoing identity and emotional history.

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

Five pre-merge fixes closing spec gaps in the Nūr Runtime.

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
- `RuntimeConfig` gains `llm_backend` ("auto"/"minimax"/"mock") and a provider key field
- `create_llm_backend(config)` uses config for backend selection, falls back to env var
- `runtime_config.yaml` updated with `llm_backend`, provider key, and `console_enabled` fields

### Fix 3: Config-driven channel startup (`runtime/app.py`, `runtime/config.py`)
- `RuntimeConfig` gains `console_enabled: bool = True`
- `NurApp.run()` starts console only when `console_enabled=True`
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
- CHANGELOG.md, `docs/internal/CLAUDE.md`, README.md updated to match final implementation
- Design doc (`docs/internal/NUR_RUNTIME_DESIGN_REVISED.md`) left as-is — it is a pre-implementation spec; intentional deviations documented in `docs/internal/CLAUDE.md`

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
- Debug server runs as background uvicorn task in NurApp
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
- `NurApp` wires session manager + console channel
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

Mandatory Nūr-side changes to support the Nūr Runtime. No external behavior changes.

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

Fixes from `docs/internal/SECOND_PASS_REVIEW.md` phases 0-5. Makes v2 behaviorally real.

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
- Response generation: single LLM call with full emotional context (Nūr personality)
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
