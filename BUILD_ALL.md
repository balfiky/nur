# Build Phases 4-9 sequentially

For each phase: build it, run its tests, fix any failures,
then move to the next. Read existing code before each phase
to use actual method names and interfaces.

## Phase 4: core/contagion.py
Detect user emotional tone from text, return DetectedEmotion.
Mirror into arousal + valence only, bounded ±0.15, weighted by
bonding score. Rule-based keyword/pattern detector for now.
With tests.

## Phase 5: core/dual_process/
generator.py: takes PipelineContext, produces response via one LLM call.
self_check.py: checks tone fit, profile contradictions, overconfidence,
bluntness given self-profile flaws. Returns pass/fail + correction notes.
Swappable LLM backend (OpenAI/Anthropic/Ollama). With tests.

## Phase 6: pipeline.py
Orchestrator wiring all modules. Follow v1 flow steps 1-15 from
PROJECT_NUR_BUILD_PLAN.md. Takes user input + user_id, returns
response + full debug state. With tests.

## Phase 7: interface/
FastAPI + WebSocket. POST /chat, GET /debug, POST /session/end.
Web UI: chat panel left, debug panel right with 5 modulator gauges,
energy meter, active profiles, memory retrievals, contradiction flags.
Plain JS or HTMX, no React.

## Phase 8: config/
Extract all hardcoded values into YAML files: modulators.yaml,
attachment.yaml, profiles_schema.yaml, values_seed.yaml.
Update modules to load from config. Add config/prompts/ with
system prompts for generator, self_check, digestion as markdown.

## Phase 9: tests/calibration/
Scripted scenarios: trust building (10 sessions), betrayal,
topic avoidance, contagion, conflict recovery, energy depletion.
Full pipeline runs with modulator trace logging.
trace_viewer.py using matplotlib.
