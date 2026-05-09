# Project Nūr Study Guide

This guide is the repo-local entry point for reviewers. Older external study
guides are historical; the source tree is the reference for current behavior.

Use these documents in order:

1. [OVERVIEW.md](OVERVIEW.md) for the thesis and current claim boundaries.
2. [ARCHITECTURE.md](ARCHITECTURE.md) for the runtime, memory model, and the
   self-evolution mechanics (constitution, metabolism, open questions,
   ask-user surfacing, trigger-time skills, skill→life migration).
3. [DEPLOYMENT_AND_ADMIN.md](DEPLOYMENT_AND_ADMIN.md) for setup, operator
   surfaces, the admin endpoint reference, and known gaps.
4. [UAT.md](UAT.md) for the live test suite (`make uat`,
   `make uat-comprehensive`) and the per-file coverage matrix.
5. [research/PAPER_DRAFT.md](research/PAPER_DRAFT.md) for the longer research framing.

For empirical checks, start with the Phase 11 and Phase 12 relationship
scenarios in `evals/scenarios.py`, the ablation priors in
`evals/ablation_hypotheses.py`, and the checked-in ablation summary under
`reports/ablation/summary.json`. For end-to-end behavior verification
against a real LLM, run `make uat-comprehensive` for a single PASS/FAIL
signal across all 63 UAT tests.
