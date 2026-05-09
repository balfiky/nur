# User Acceptance Testing

Nūr has structural unit/eval coverage and a separate UAT layer for installed,
operator-facing behavior. UAT starts a real `nur-web` process in a temporary
workspace, drives the bundled Web/Admin UI through Chromium, and checks the
same API payloads a user or channel sees.

UAT is **live-only**. Nūr no longer ships a mock LLM backend in production, so
the suite always calls a real model. Setting `NUR_UAT_LIVE=1` plus a
configured `NUR_UAT_BACKEND` is required; otherwise tests are skipped with a
clear reason.

## Install UAT Dependencies

```bash
python3 -m pip install -e ".[dev,uat]"
python3 -m playwright install chromium
```

## Run The Full Suite

The repository includes a Makefile with three test entry points:

```bash
make test                 # non-UAT unit/integration suite (1830 tests, ~3 min)
make uat                  # full UAT suite (64 tests, ~14 min, live LLM)
make uat-comprehensive    # single PASS/FAIL aggregator that runs the same UAT
                          # tests as a subprocess and asserts they all pass
```

`make uat` defaults to the Codex CLI backend. Override with:

```bash
NUR_UAT_BACKEND=openai_compatible \
  NUR_UAT_BASE_URL=http://localhost:11434/v1 \
  NUR_UAT_MODEL=llama3.2 \
  make uat
```

Direct pytest invocation works too:

```bash
NUR_UAT_LIVE=1 NUR_UAT_BACKEND=codex \
  python -m pytest tests/uat/ -m "uat and not comprehensive"
```

The `comprehensive` marker is the single aggregator
(`tests/uat/test_evolution_features.py::test_complete_release_uat_suite`). It
subprocesses pytest internally, so each underlying test still gets a clean
fixture. Use it when you want one green/red signal — for example before a
release.

## Coverage Matrix

The suite contains 63 tests across nine files.

| Surface | UAT files |
|---|---|
| First-run setup wizard | `test_first_run_wizard.py` |
| Web chat + debug panels | `test_clean_setup_chat.py` |
| Admin pages, navigation, config save | `test_admin_options.py` |
| Cognitive journeys (distress / rupture / repair, semantic, commitment, tools toggle, restart-required UI) | `test_cognitive_journeys.py` |
| Accessible-name inventory across visible controls | `test_control_inventory.py` |
| Skills import, audit, enable, fake-skill guard, life ingest | `test_life_skills_tools.py` |
| Session persistence across server restart | `test_persistence_restart.py` |
| Persona/observability dashboard across channels | `test_persona_dashboard.py` |
| Telegram non-mutating introspection commands | `test_telegram_introspection.py` |
| Sprint 1–5 evolution features + public-release admin surfaces (constitution, open questions, metabolism, skill migration, ask-user surfacing, bearer auth, backup, soul flow, diagnostics, sessions reset, soul draft, file-path skill import, CORS) | `test_evolution_features.py` |
| Browser sweep of every clickable element + file uploads + token dialog + chat-shell session buttons + legacy URL redirects | `test_ui_sweep.py` |

Specific high-value scenarios:

- Constitution layer: persists across restart, surfaces in the LLM prompt's
  `life_history_context`, max-length enforced, UI Save flow round-trips.
- Open questions queue: lifecycle (list/filter/abandon/resolve), contradiction
  emission via `revise_beliefs_against_evidence`, drive-gap and low-confidence
  emission via metabolism reflection, UI rendering and Abandon button.
- Self-evolution metabolism: forced `last_decay_at` in the past triggers real
  belief decay (weak <0.2 revoked) and theme→belief promotion at the next tick.
- Trigger-time skill retrieval: `applies_when` filter exposes skills only when
  the user message hint matches; missing field surfaces an audit warning.
- Skill→life migration: `migrate_skill_to_life` flips the skill to
  `status='migrated'` and seeds an `operator_directive` life experience.
- Ask-user surfacing: open question surfaces in chat, transitions to
  `pursuing`, and the default 3/day learning budget caps surfacing at exactly
  three per session.
- Multi-turn behavioral arc: belief seed → contradicting evidence → confidence
  drop + contradiction question emitted → drive shift + non-zero life-influence
  pressure on a follow-up turn → operator resolves the question.
- Public-release controls: bearer auth enforced when `api_key` set; backup
  CRUD with typed-confirmation guard; soul GET/POST round-trip; sessions
  reset eviction; diagnostics shape; LLM-driven soul draft validates schema
  without persisting; file-path skill import accepts valid folders + rejects
  non-existent paths and directories without `SKILL.md`; CORS origins respected
  at startup.
- Browser sweep: every action button on `/settings` (Test LLM/Telegram/Storage,
  Diagnostics, Export Config, Backup create/list, all five Refresh buttons);
  file uploads via `#skillUpload` and `#lifeUploadFile`; token dialog
  auto-opens on 401 and Save Token persists; identity panel Save+Reload;
  destructive buttons present with `danger` class; chat-shell session buttons
  (Why this response, End Session, Rest); open-wizard relaunches setup;
  `/admin`, `/persona`, `/dashboard` legacy redirects.
- Tool diversity: `system.memory_usage` end-to-end via chat, `web.search` via
  the bundled DuckDuckGo provider, `fs.read_file` workspace read, `fs.write_file`
  blocked under assisted autonomy, `shell.run_command` refused when shell tool
  disabled, and the imperative-phrasing memory-query regression.

Not covered by UAT: subjective human-likeness, production network exposure,
provider-specific model quality, mobile viewport behavior, keyboard navigation,
theme switching. Use a blinded user study for human-likeness; use direct
manual checks for the visual fidelity items.

## Backend Selection

`NUR_UAT_BACKEND` selects the live backend the suite runs against. The
fixture writes a real `runtime_config.yaml`, starts `nur-web`, and the suite
talks to it through HTTP and the browser.

OpenAI-compatible / hosted provider:

```bash
export LLM_API_KEY=...
NUR_UAT_LIVE=1 \
  NUR_UAT_BACKEND=openai_compatible \
  NUR_UAT_BASE_URL=https://your-provider.example/v1 \
  NUR_UAT_MODEL=your-model \
  python -m pytest tests/uat/ -m "uat and not comprehensive"
```

Codex CLI (uses your local Codex login, no API key needed):

```bash
NUR_UAT_LIVE=1 NUR_UAT_BACKEND=codex \
  python -m pytest tests/uat/ -m "uat and not comprehensive"
```

`NUR_UAT_BACKEND=mock` is no longer accepted; the production runtime never
runs against a fake backend, and UAT mirrors that.

## Useful Options

```bash
NUR_UAT_HEADED=1 make uat           # watch the browser locally
NUR_UAT_KEEP_WORKSPACE=1 make uat   # keep the temp data/config workspace
NUR_UAT_ARTIFACTS=reports/uat/run1 make uat   # custom artifact directory
```

Failure screenshots and the server log land under
`reports/uat/<test_node_id>/` for any failing test.

## CI

The UAT suite is opt-in in CI because it spends provider quota on every run.
A workflow that exercises the live suite should set `NUR_UAT_LIVE=1` and the
appropriate backend env vars from repository secrets, then run
`make uat-comprehensive` for a single PASS/FAIL signal.

UAT remains structural and inspectable. It does not claim human-likeness or
measure subjective response quality.
