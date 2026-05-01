# Contributing to Project Nūr

Nūr is a research-oriented hybrid cognitive architecture. Contributions
that sharpen the architecture, improve inspectability, or strengthen
empirical validation are especially welcome.

## Ground Rules

- **Respect the single-mind boundary.** The cognitive pipeline owns
  emotional state, memory, self-model, and tool policy. Runtime/channel
  code must not shadow or duplicate those concerns.
- **Math first, LLM only where necessary.** Modulator updates, decay,
  profile math, strategy selection, and retrieval ranking must remain
  deterministic and unit-testable.
- **Every change needs a test.** Either a focused unit test, an eval
  scenario, or a regression lock in `tests/test_regressions.py`.
- **Keep prompts and constants config-driven.** New thresholds go in
  `config/*.yaml`; new prompts go in `config/prompts/`.

## Development Setup

```bash
git clone https://github.com/balfiky/nur.git
cd nur
python3 -m pip install -e ".[dev]"
python3 -m pytest -q        # full suite; see CI for the current collected count
```

Optional:

```bash
python3 -m evals --backend mock --tag phase11    # behavioral eval pack
```

## What Goes Where

Code:

| Change kind | Primary path |
|---|---|
| Cognitive logic | `core/` (emotional engine, memory, profiles, dual process, appraisal, defense) |
| Turn orchestration | `pipeline.py` |
| Session / channel / runtime | `runtime/` |
| HTTP surface | `interface/api.py`, `interface/v1.py` |
| Web UI | `interface/static/index.html` |
| Evals and scenarios | `evals/`, `tests/calibration/scenarios.py` |
| Tests | `tests/` (unit by module; integration in `test_interface*`, `test_runtime*`, `test_regressions.py`) |

Docs:

| Change kind | Update |
|---|---|
| Pipeline stage order | `tools/build_diagrams.py::build_single_turn_flow` + `docs/ARCHITECTURE.md` Single-Turn section |
| Storage / table changes | `tools/build_diagrams.py::build_memory_persistence` + `docs/ARCHITECTURE.md` + `PRIVACY.md` |
| HTTP route or auth changes | `tools/build_diagrams.py::build_auth_tool_safety` + `docs/ARCHITECTURE.md` + `SECURITY.md` |
| Admin / wizard behavior | `docs/DEPLOYMENT_AND_ADMIN.md` |
| New eval variant | `tools/build_diagrams.py::build_evaluation_harness` + `evals/ablation_hypotheses.py` **before** running the eval + `docs/ARCHITECTURE.md` Evaluation section + `docs/OVERVIEW.md §4` results table |
| Component evidence status changes | `tools/build_diagrams.py::build_component_claim_map` + `docs/OVERVIEW.md §5` |
| Release notes | `CHANGELOG.md` under a new version header |

## Regenerating Diagrams

Architecture diagrams are generated from Python by
`tools/build_diagrams.py`, rendered to SVG by the toolkit in
`tools/diagram_toolkit.py`, and rasterized to PNG by cairosvg. Both
`.svg` and `.png` are tracked in `docs/diagrams/`.

After editing the generator, run:

```bash
python3 tools/render_diagram_pngs.py
```

Then commit the regenerated `.svg` and `.png` together so the rendered
artifacts stay in lockstep with the source.

Design language for new diagrams:

- palette and primitives live in `tools/diagram_toolkit.py`
- semantic colors: `process` (runtime), `storage` (data), `client`
  (channel), `external` (LLM), `danger` (side effects), `attention`
  (gate / decision), `neutral` (utility)
- flat design, orthogonal arrow routing, no drop shadows
- diagonal fan-out arrows are fine when source/target y-ordering is
  monotonic (prevents crossings)

## Adding An Eval Scenario

1. Author the scenario in `evals/scenarios.py` or
   `tests/calibration/scenarios.py` depending on scope.
2. Before running, register the outcome hypothesis in
   `evals/ablation_hypotheses.py`: one row per (scenario, variant)
   with `expected_failure`, `unexpected_failure`, `no_effect`, or
   `newly_passing`. This is the pre-registered prediction the eval
   report labels each outcome against.
3. Run `python3 -m evals --backend mock --tag <your-tag>` for a
   fast offline pass, then `--backend provider` (or similar) for a
   live-LLM run when relevant.
4. If a new component becomes load-bearing or shifts category, update
   `docs/OVERVIEW.md §5` and
   `tools/build_diagrams.py::build_component_claim_map`.

## Writing A Regression Test

Regressions for confirmed-fixed bugs live in
`tests/test_regressions.py`. Each test should name the original bug in a
comment and assert the fix. Keep them short; one class per bug family.

## Pull Request Checklist

- [ ] Tests pass: `python3 -m pytest`
- [ ] Eval suite passes when relevant: `python3 -m evals`
- [ ] Diagrams regenerated if any architecture-sensitive change touched
      the build generator
- [ ] Docs updated per the matrix above
- [ ] `CHANGELOG.md` updated under an appropriate version header
- [ ] No secrets, API keys, or user session data committed
- [ ] For behavioral changes: a scenario that locks the new behavior
- [ ] For new cognitive components: an ablation-friendly toggle so the
      system can run with/without the component for validation

## What We Are Especially Looking For

- **Empirical validation**: ablation studies, baseline comparisons,
  blinded human ratings, benchmarks against real LLM backends (not
  mocks).
- **Scientific grounding**: tightening the mapping between
  implementation and the cited theories (PSI, ACT-R, CLARION), or
  honest reframing where the current implementation diverges.
- **Privacy and safety**: retention controls, data-export/delete APIs,
  consent surfaces, anthropomorphism guardrails.

## Reporting Issues

Open a GitHub issue. For anything security- or privacy-sensitive, see
[SECURITY.md](SECURITY.md) before filing publicly.

## Code Of Conduct

Be respectful, be direct, disagree on substance. No personal attacks.
