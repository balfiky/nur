# Project Nūr

Read PROJECT_NUR_ARCHITECTURE.md for the full vision.
Read PROJECT_NUR_BUILD_PLAN.md for the build plan.

We are building v1 only. Do not build v2 features.

## Rules
- Every module is independent with clear inputs/outputs
- Shared types live in core/types.py — all modules import from there
- Pure math where possible, LLM calls only where specified in build plan
- SQLite for persistence, no external DB servers
- Every module gets its own tests
- Python 3.11+, FastAPI for web, YAML for config
- Start exaggerated emotional effects, we'll dampen later

## Build order
1. core/types.py (shared contracts)
2. core/emotional_engine.py (5 modulators, decay, energy)
3. core/memory/ (short-term + long-term + digestion)
4. core/profiles/ (person + self + topic + contradiction)
5. core/contagion.py (bounded, arousal + valence only)
6. core/dual_process/ (single pass + self-check)
7. pipeline.py (wire everything)
8. interface/ (FastAPI + debug dashboard)

## Current phase: 3 — core/profiles/ (person + self + topic + contradiction)
