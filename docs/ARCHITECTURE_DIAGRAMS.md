# Project Nūr — Architecture Diagrams

These are rendered diagram previews for Markdown readers. PNG is used
for compatibility; the SVG files remain in `docs/diagrams/` as the
source assets.

If you download this Markdown file by itself, the diagrams will not
appear because the image files live under `docs/diagrams/`. For the main
GitHub-friendly walkthrough, use:

- [../PROJECT_NUR_OVERVIEW.md](../PROJECT_NUR_OVERVIEW.md)

For a single reader-facing file with the diagrams embedded directly, use:

- [../PROJECT_NUR_OVERVIEW.html](../PROJECT_NUR_OVERVIEW.html)

Contents:

- [A. High-level runtime architecture](#a-high-level-runtime-architecture)
- [B. Single-turn cognitive flow](#b-single-turn-cognitive-flow)
- [C. Memory and persistence model](#c-memory-and-persistence-model)
- [D. Auth and tool-safety boundary](#d-auth-and-tool-safety-boundary)
- [E. Evaluation and ablation harness](#e-evaluation-and-ablation-harness)
- [F. Component claim map](#f-component-claim-map)

---

## A. High-level runtime architecture

Two independent entry points share the same session and cognition layer.
`nur` runs the console + Telegram + debug API via `NurApp`; `nur-web`
runs FastAPI via uvicorn and serves the web UI, the legacy endpoints,
and the versioned `/v1/*` router. They do not run together in the same
process.

![High-level runtime architecture](diagrams/runtime-architecture.png)

[Open the SVG directly](diagrams/runtime-architecture.svg)

Notes:

- `SessionManager` holds a `_user_locks` dict keyed by `platform:user_id`
  so two chat contexts for the same user do not write to `nur.db`
  concurrently.
- `UserSession` bridges sync/async: the pipeline is plain synchronous
  Python; the session runs `pipeline.process` on a `ThreadPoolExecutor`
  and gates it on a per-user lock.
- The debug API at `:8077` is read-only inspection only; channels do
  not receive messages through it.
- There is no WhatsApp channel. There is no OpenClaw integration.

---

## B. Single-turn cognitive flow

The order matches `CognitivePipeline.process` in `pipeline.py`.
Diamonds are gates that can skip or short-circuit a stage; rectangles
are unconditional. LLM-call counts are annotated in the diagram.

![Single-turn cognitive flow](diagrams/single-turn-cognitive-flow.png)

[Open the SVG directly](diagrams/single-turn-cognitive-flow.svg)

Per-turn LLM budget: the common case is **1** call. Optional paths add
inner dialogue, LLM self-check, and one possible regeneration. There is
no fixed safe upper bound in the current implementation because
inner-dialogue parsing can retry.

---

## C. Memory and persistence model

Keys derived from `platform:user_id[:chat_id]` map to directories and
files on disk. Each user gets their own SQLite database; per-session
engine state lives in its own JSON file; the assistant's self-model is
deliberately separated into a shared DB so per-user deletion never
erases the growth history of the assistant itself.

![Memory and persistence model](diagrams/memory-persistence-model.png)

[Open the SVG directly](diagrams/memory-persistence-model.svg)

Deletion semantics, from `PRIVACY.md` and enforced in
`interface/v1.py::delete_user`:

- Evicts every live session for the `platform:user_id`.
- Removes the per-user directory, including `nur.db`,
  `engine_state.json`, and every `sessions/*.json`.
- Leaves `data/shared/self_model.db` untouched by design.
- Returns best-effort row counts per table so callers can log what was
  wiped.

---

## D. Auth and tool-safety boundary

Two independent switches. `api_key` in `runtime_config.yaml` controls
HTTP access. `tools_enabled`, `shell_tool_enabled`, and
`tools_workspace` control whether a chat turn can trigger side effects
on the host. Flipping one does not flip the other.

![Auth and tool-safety boundary](diagrams/auth-tool-safety-boundary.png)

[Open the SVG directly](diagrams/auth-tool-safety-boundary.svg)

Tracked defaults in `runtime_config.yaml`:

```yaml
api_key: ''
tools_enabled: false
tools_workspace: ''
shell_tool_enabled: false
```

A tracked value of `api_key: ''` is the local-dev posture, not a
production deployment. Ship it set, and ship tools off unless you
explicitly need agentic side effects.

---

## E. Evaluation and ablation harness

The eval CLI is not a unit-test runner. It runs scripted behavioral
scenarios against a real pipeline with a user-chosen backend, stamps the
run with git + config-hash provenance, and emits JSON reports.

![Evaluation and ablation harness](diagrams/evaluation-ablation-harness.png)

[Open the SVG directly](diagrams/evaluation-ablation-harness.svg)

See `evals/ablation_hypotheses.py` for the pre-registered predictions:
every ablation variant declares which scenarios it expects to break
before the run. Post-hoc labels must match, or the audit flags them.

---

## F. Component claim map

What the current evidence actually supports versus what the architecture
claims to contribute. This is the anchor for the Technical Note's
evaluation section and the Paper Draft's discussion section.

![Component claim map](diagrams/component-claim-map.png)

[Open the SVG directly](diagrams/component-claim-map.svg)

Reading the map honestly:

- **Relationship memory is load-bearing on this suite, and only this
  suite.** The ablation result is reproducible and pre-registered. It
  does not say relationship memory is the only load-bearing component,
  only that it is the one the current suite can falsify.
- **Semantic memory shows zero effect because nothing in the suite
  reaches for it.** The negative-control label is important: an
  unexpected failure under this ablation would have indicated hidden
  coupling. Zero failures is the right answer.
- **Inner dialogue and defense sit upstream of what Phase 11 measures.**
  Grading them requires a different scenario design, likely with blinded
  human judges.
- **Unified self/other profiling is an architectural claim.** It changes
  what the system is, not a number any current assertion exercises.

---

## Maintenance notes

- If you change the pipeline stage order in `pipeline.py::process`,
  update **B**.
- If you add a new SQLite table or move one between DBs, update **C** and
  `PRIVACY.md`.
- If you add a new HTTP route, update **D** and decide explicitly whether
  the new route is protected or exempt, then add a regression test in
  `tests/test_interface_v1.py::TestLegacyEndpointAuth`.
- If you add a new ablation variant, update **E** and **F**, and add a
  hypothesis entry in `evals/ablation_hypotheses.py` before running.
