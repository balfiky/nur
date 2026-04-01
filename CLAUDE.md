# Project Nur

Read PROJECT_NUR_ARCHITECTURE.md for the full vision.
Read PROJECT_NUR_BUILD_PLAN.md for the v1/v2 roadmap.
Read README.md for setup, usage, API reference, and module documentation.

## v1 Status: COMPLETE

All v1 components are built, tested (288 tests), and wired together.

### What's built (v1)
- core/types.py — All shared type contracts
- core/emotional_engine.py — 5-modulator PSI state machine (decay, energy, contagion, attachment)
- core/memory/ — Short-term buffer + SQLite long-term (ACT-R retrieval) + session digestion
- core/profiles/ — Person + self + topic profiles + contradiction detection (unified mechanism)
- core/contagion.py — Emotional detection (LLM + rule-based fallback)
- core/dual_process/ — Response generation + self-check (rule-based + optional LLM)
- core/llm_client.py — MiniMax M2.7-highspeed API client
- pipeline.py — Full 15-step cognitive pipeline orchestrator
- interface/ — FastAPI + WebSocket + debug dashboard
- config/ — YAML configs + 6 prompt templates
- tests/ — 288 tests including calibration scenarios and emotional journey tests

### What's NOT built (v2 features — do not exist yet)
- Resolution as 6th modulator (v2.1)
- Inner dialogue / iterative dual process (v2.2)
- Anticipation / forward modeling (v2.3)
- Defense mechanisms (v2.4)
- Dynamic value drift (v2.5)
- Attachment style unlocking (v2.6)
- Deep periodic self-reflection (v2.7)
- Growth milestone tracking (v2.8)

## Rules
- Every module is independent with clear inputs/outputs
- Shared types live in core/types.py — all modules import from there
- Pure math where possible, LLM calls only where necessary
- SQLite for persistence, no external DB servers
- Every module gets its own tests
- Python 3.10+, FastAPI for web, YAML for config
- LLM functions always have rule-based fallback (graceful degradation)
- Config-driven constants — no hardcoded thresholds in module code
- Start exaggerated emotional effects, dampen later during calibration

## Key patterns
- LLMBackend protocol: `generate(system_prompt: str, user_message: str) -> str`
- MockLLMBackend returns "I understand." — triggers rule-based fallbacks in all LLM-dependent code
- Config singleton: `from config.loader import get_config`
- All LLM text interpretation: try JSON parse from LLM, fall back to keywords on failure
- Trust asymmetry: +0.02 positive, -0.15 negative (7.5x negativity bias)
- Spike threshold: intensity >= 0.8 bypasses confidence threshold for long-term memory writes
- Self-profiling uses entity ID `__self__` with same ProfileStore as person profiles

## LLM provider
- MiniMax M2.7-highspeed (Plus-Highspeed token plan, 4500 req/5hrs)
- OpenAI-compatible API at https://api.minimax.io/v1
- API key via MINIMAX_API_KEY env var
- Model returns `<think>...</think>` reasoning tags — stripped by LLMClient

## Testing
- `pytest` runs all 288 tests
- `python -m tests.run_journey_report` for detailed emotional journey output
- Tests work without API key (MockLLMBackend + rule-based fallbacks)

## Build order (v1 — completed)
1. core/types.py (shared contracts)
2. core/emotional_engine.py (5 modulators, decay, energy)
3. core/memory/ (short-term + long-term + digestion)
4. core/profiles/ (person + self + topic + contradiction)
5. core/contagion.py (bounded, arousal + valence only)
6. core/dual_process/ (single pass + self-check)
7. pipeline.py (wire everything)
8. interface/ (FastAPI + debug dashboard)
9. config/ (YAML extraction + prompt templates)
10. tests/calibration/ (scripted scenarios + trace viewer)
11. core/llm_client.py (MiniMax API + think-tag stripping)
12. LLM wiring (contagion, classify_event, detect_topics all use LLM with fallback)
13. tests/test_emotional_journey.py (end-to-end journey tests)
