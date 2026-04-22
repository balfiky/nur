# Contributing to Project Nūr

Thank you for your interest in contributing. Nūr is a research-oriented hybrid
cognitive architecture. Contributions that sharpen the architecture, improve
inspectability, or strengthen empirical validation are especially welcome.

## Ground Rules

- **Respect the single-mind boundary.** The cognitive pipeline owns emotional
  state, memory, self-model, and tool policy. Runtime/channel code must not
  shadow or duplicate those concerns.
- **Math first, LLM only where necessary.** Modulator updates, decay, profile
  math, strategy selection, and retrieval ranking must remain deterministic
  and unit-testable.
- **Every change needs a test.** Either a focused unit test, an eval scenario,
  or a regression lock in `tests/test_regressions.py`.
- **Keep prompts and constants config-driven.** New thresholds go in
  `config/*.yaml`; new prompts go in `config/prompts/`.

## Development Setup

```bash
git clone https://github.com/balfiky/nur.git
cd nur
python3 -m pip install -e ".[dev]"
python3 -m pytest
```

## Pull Request Checklist

- [ ] Tests pass: `python3 -m pytest`
- [ ] Eval suite passes: `python3 -m evals`
- [ ] No secrets, API keys, or user session data committed
- [ ] `CHANGELOG.md` updated under an appropriate version header
- [ ] For behavioral changes: a scenario in `tests/calibration/scenarios.py`
      or `evals/scenarios.py` that locks the new behavior
- [ ] For new cognitive components: an ablation-friendly toggle so the system
      can run with/without the component for validation

## What We're Especially Looking For

- **Empirical validation**: ablation studies, baseline comparisons, blinded
  human ratings, benchmarks against real LLM backends (not mocks).
- **Scientific grounding**: tightening the mapping between implementation and
  the cited theories (PSI, ACT-R, CLARION), or honest reframing where the
  current implementation diverges.
- **Privacy & safety**: retention controls, data-export/delete APIs, consent
  surfaces, anthropomorphism guardrails.

## Reporting Issues

Open a GitHub issue. For anything security- or privacy-sensitive, see
[SECURITY.md](SECURITY.md) before filing publicly.

## Code of Conduct

Be respectful, be direct, disagree on substance. No personal attacks.
