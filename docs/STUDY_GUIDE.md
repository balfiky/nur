# Project Nūr Study Guide

This guide is the repo-local entry point for reviewers. Older external study
guides are historical; the source tree is the reference for current behavior.

Use these documents in order:

1. [OVERVIEW.md](OVERVIEW.md) for the thesis and current claim boundaries.
2. [ARCHITECTURE.md](ARCHITECTURE.md) for the runtime and memory model.
3. [DEPLOYMENT_AND_ADMIN.md](DEPLOYMENT_AND_ADMIN.md) for setup and operator surfaces.
4. [research/PAPER_DRAFT.md](research/PAPER_DRAFT.md) for the longer research framing.

For empirical checks, start with the Phase 11 and Phase 12 relationship
scenarios in `evals/scenarios.py`, the ablation priors in
`evals/ablation_hypotheses.py`, and the checked-in ablation summary under
`reports/ablation/summary.json`.
