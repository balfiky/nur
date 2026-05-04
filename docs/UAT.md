# User Acceptance Testing

Nūr has structural unit/eval coverage and a separate UAT layer for installed,
operator-facing behavior. UAT starts a real `nur-web` process in a temporary
workspace, drives the bundled Web/Admin UI through Chromium, and checks the
same API payloads a user or channel sees.

## Install UAT Dependencies

```bash
python3 -m pip install -e ".[dev,uat]"
python3 -m playwright install chromium
```

## Mock UAT

Mock UAT is deterministic and does not call a paid model:

```bash
nur-uat --artifacts reports/uat/mock
```

It covers:

- first-run setup wizard from blank setup state through chat
- clean temp runtime setup and real `nur-web` startup
- Web chat from first load to response
- debug relationship state, deterministic explanation, and memory panels
- Admin page navigation, config save, live apply, and restart-required reporting
- Skills import/enable from pasted `SKILL.md`, server folder, and uploaded zip
- Claude/Codex-style skill packages with scripts/resources audited but not executed
- enabled skill context reaching a later turn's debug payload
- conversational attempts to "create a permanent skill" corrected unless Admin Skills confirms it
- Life History text, browser upload, local-file digest, rollback, and later LifeInfluence
- emotional state movement across distress, rupture, and repair arcs
- relationship open loops, repair, semantic preference retrieval, and commitment persistence
- tools inventory with enabled/disabled settings and read-only filesystem execution
- session state files across web-server restart
- Telegram introspection commands without mutating cognition
- visible-control accessible-name inventory

## Coverage Matrix

| Surface | UAT coverage |
| --- | --- |
| Installation-style startup | Real `nur-web` process, temp config, temp data dir, health/ready probes |
| First-run setup | Wizard mock backend, identity save, formative Life digest, then chat |
| Web chat | Browser sends a turn, assistant response renders, debug drawer opens |
| Debug/explainability | Relationship view, strategy trace, explanation, memory inspector, modulator state |
| Admin options | Runtime/model/tools/channel/access/maintenance pages, config save, reload, restart-required fields |
| Skills | Pasted import, folder import, zip upload, scripts/resources audit, enable/disable, enabled prompt context |
| Skill hallucination guard | Conversation cannot claim permanent skill creation without registry evidence |
| Life History | Text/upload/local-file ingestion, evolution records, rollback, prompt context, LifeInfluence pressures/effects |
| Emotion/relationship | Distress, assistant-targeted rupture, repair, semantic preference + open loop, commitment persistence |
| Tools | Disabled inventory returns zero, enabled inventory lists tools, read-only file action executes |
| Persistence | Hot session state/history survive web-server restart |
| Telegram | `/state`, `/why`, `/memory`, `/loops`, `/repair` inspect without creating sessions or processing turns |
| Accessibility/control inventory | Visible buttons/links/inputs/selects/textareas must have accessible names |

Not covered by mock UAT: subjective human-likeness, production network exposure,
provider-specific model quality, and high-cost live tool runs. Use live UAT for
backend integration and keep human-likeness for a separate blinded study.

## Live UAT

Live UAT is intentionally explicit. If `--live` is set and the configured key,
model, or base URL is missing, the run fails rather than falling back to mock.

OpenAI-compatible/provider example:

```bash
export LLM_API_KEY=...
nur-uat \
  --live \
  --backend openai_compatible \
  --base-url https://your-provider.example/v1 \
  --model your-model \
  --artifacts reports/uat/live
```

MiniMax example:

```bash
export LLM_API_KEY=...
nur-uat \
  --live \
  --backend minimax \
  --model your-model \
  --artifacts reports/uat/live
```

Useful options:

```bash
nur-uat --headed                 # watch the browser locally
nur-uat --keep-workspace         # keep the temp data/config workspace
nur-uat --pytest-args -- -k chat # pass extra args to pytest
```

## CI

The UAT workflow runs the mock pass and uploads artifacts on push and pull
request. The live pass is opt-in because it requires repository secrets and may
spend provider quota.

Run live UAT in CI by either:

- manually starting the UAT workflow with `run_live=true`, or
- setting repository variable `NUR_RUN_LIVE_UAT` to `1` or `true`.

Live UAT has no mock fallback. It requires repository variables such as
`NUR_UAT_BACKEND`, `NUR_UAT_MODEL`, and `NUR_UAT_BASE_URL`, plus the matching
secret selected by `NUR_UAT_API_KEY_ENV` or the default `LLM_API_KEY`.
Pull requests from forks may not have access to those
secrets, so run `nur-uat --live` locally or through a trusted branch before
release.

UAT remains structural/inspectable. It does not claim human-likeness or measure
subjective response quality.
