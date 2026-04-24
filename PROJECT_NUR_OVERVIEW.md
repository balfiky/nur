# Project Nūr — Overview

> A practical overview of the idea, theory stance, implementation, safety
> posture, and evaluation evidence behind Nūr: a hybrid cognitive
> architecture for AI assistants with persistent emotional state and
> relationship memory.

**Reading paths**

- Use this file on GitHub.
- Use [PROJECT_NUR_OVERVIEW.html](PROJECT_NUR_OVERVIEW.html) when you want a
  single downloadable file with everything embedded directly.

**Scope**

- Research prototype
- Persistent emotional state, memory, and relationship continuity
- No human-likeness claim
- No claim of faithful implementation of PSI, ACT-R, or CLARION

## Contents

1. [The idea](#1-the-idea)
2. [Theory stance](#2-theory-stance)
3. [Architecture](#3-architecture)
4. [Single-turn flow](#4-single-turn-flow)
5. [Memory and persistence](#5-memory-and-persistence)
6. [Auth and tool safety](#6-auth-and-tool-safety)
7. [Evaluation](#7-evaluation)
8. [What the evidence supports](#8-what-the-evidence-supports)
9. [How to run it](#9-how-to-run-it)
10. [Limitations](#10-limitations)

## 1. The idea

Nūr started from a practical observation: many LLM assistants can sound
emotionally responsive, especially when a prompt gives them a personality
and memory, but the response often still feels like styling layered on top
of the current conversation. The state does not always feel like it has
depth, history, tension, or consequence.

The design question is: what if emotional behavior is not only prompted,
but carried by explicit state? Nūr treats emotion as persistent internal
variables, relationship history, unresolved loops, and self-observation.
The LLM still writes the final language, but it receives context from a
cognitive layer that changes over time.

**Core thesis:** state should persist, decay, and remain inspectable. A
response after history should be shaped by accumulated context, not only by
a fresh prompt.

**Honest scope:** this is a research prototype. It does not prove
human-likeness, consciousness, therapeutic value, or psychological
validity.

## 2. Theory stance

Nūr is inspired by PSI Theory, ACT-R, and CLARION, but it is not a faithful
implementation of any of them.

What the project takes from those traditions is the engineering stance:

- continuous internal state
- explicit decay over time
- memory weighted by recency, frequency, and context
- bounded fast/slow deliberation
- inspectable internal traces

What it does **not** claim:

- formal PSI drive systems
- ACT-R production rules
- CLARION's full implicit/explicit learning architecture

The point is not to claim psychological correctness. The point is to build
an inspectable architecture where emotional continuity can be tested,
ablated, and criticized.

## 3. Architecture

There are two runtime hosts:

- `nur` for console, Telegram, and debug runtime
- `nur-web` for FastAPI, web UI, legacy endpoints, and the versioned
  `/v1` API

Both feed into the same session manager and cognitive pipeline.

![Runtime architecture](docs/diagrams/runtime-architecture.png)

At a high level:

- `SessionManager` owns live sessions and per-user serialization
- `UserSession` bridges async runtime behavior to a synchronous pipeline
- `CognitivePipeline` performs deterministic appraisal, memory retrieval,
  deliberation gates, and generation orchestration
- the LLM backend writes language, not the whole cognitive policy
- persistence is split across per-user state and a shared self-model store

## 4. Single-turn flow

The stage order matches `pipeline.py::CognitivePipeline.process`.

![Single-turn cognitive flow](docs/diagrams/single-turn-cognitive-flow.png)

Typical per-turn LLM budget is **1** call. Optional paths add:

- inner dialogue
- LLM self-check
- one possible regeneration

The common case is intentionally cheap. The more complex paths are gated,
not always-on.

The emotional state itself is a six-modulator vector:

- arousal
- valence
- certainty
- bonding
- energy
- resolution

Those values persist, decay, and are updated from multiple sources per
turn: bounded contagion, person-specific baseline shift, event-driven
impulses, and time-based drift.

## 5. Memory and persistence

Nūr uses multiple memory layers rather than one generic conversation log:

- short-term memory for the active session
- long-term emotional memory in SQLite
- relationship memory for rupture, repair, commitments, and recurring
  tension
- semantic memory for stable facts/preferences
- self/other/topic profiling through one shared profiling mechanism

![Memory and persistence model](docs/diagrams/memory-persistence-model.png)

Important persistence choices:

- each user gets their own directory under `data/`
- each user gets their own `nur.db`
- session state is stored separately as JSON
- the assistant's self-model lives in `data/shared/self_model.db`
- deleting a user removes per-user data but deliberately preserves the
  shared self-model

This is the architectural point: Nūr does not only remember facts. It also
tracks how interactions landed, what remains unresolved, and how the
relationship arc evolved.

## 6. Auth and tool safety

Two independent safety boundaries exist:

- `api_key` controls HTTP access
- `tools_enabled`, `tools_workspace`, and `shell_tool_enabled` control
  whether chat can produce side effects on the host

![Auth and tool-safety boundary](docs/diagrams/auth-tool-safety-boundary.png)

Current tracked defaults:

```yaml
api_key: ''
tools_enabled: false
tools_workspace: ''
shell_tool_enabled: false
```

Meaning:

- local starter mode is open by default for convenience
- tool execution is off by default
- shell execution requires a second explicit opt-in
- filesystem tools are sandboxed to the configured workspace when enabled

This is a local research prototype posture, not a production deployment
posture.

## 7. Evaluation

The current evaluation is a structural ablation harness, not a human
judgment study.

![Evaluation and ablation harness](docs/diagrams/evaluation-ablation-harness.png)

The harness runs scripted scenarios against a real pipeline, stamps each run
with provenance, and emits JSON reports.

Headline ablation result on the current Phase 11 scenario set:

| Variant | Pass | Expected fail | Unexpected fail | Interpretation |
|---|---:|---:|---:|---|
| baseline | 6/6 | - | - | reference run |
| no relationship memory | 4/6 | 2 | 0 | load-bearing on cross-turn continuity |
| no inner dialogue | 6/6 | 0 | 0 | not falsifiable by this suite |
| no defense | 6/6 | 0 | 0 | not falsifiable by this suite |
| no semantic memory | 6/6 | 0 | 0 | negative control behaved as expected |

What this does show:

- at least one component, relationship memory, is structurally load-bearing
- the evaluation harness can reproduce pre-registered ablation hypotheses

What this does **not** show:

- human-likeness
- wording quality superiority over a prompt-only baseline
- psychological validity

## 8. What the evidence supports

The current evidence is asymmetric by design.

![Component claim map](docs/diagrams/component-claim-map.png)

The honest reading is:

- **Relationship memory is load-bearing on the present suite.**
- **Semantic memory is a negative control here.** Zero effect is the
  expected answer because the current scenarios do not exercise it.
- **Inner dialogue and defense are present but not falsifiable by this
  suite.**
- **Unified self/other profiling is an architectural claim, not a current
  benchmark win.**

That is a legitimate result for an architecture paper or research prototype.
It is not a substitute for human evaluation.

## 9. How to run it

Install:

```bash
git clone https://github.com/balfiky/nur.git
cd nur
python3 -m pip install -e ".[dev]"
```

Start the web UI:

```bash
nur-web
```

Start the console runtime:

```bash
nur
```

Run the tests:

```bash
python3 -m pytest
```

Run the behavioral eval pack offline:

```bash
python3 -m evals --backend mock --tag phase11
```

Backend modes:

- `mock` for local/offline testing
- `provider` for a hosted provider or gateway configured by `llm_base_url`, `llm_model`, and `llm_api_key`
- `openai_compatible` for local or remote OpenAI-compatible endpoints such as Ollama, vLLM, LM Studio, or a hosted compatible gateway
- `minimax` as a legacy provider-specific path kept for backward compatibility

For operational details, see:

- [README.md](README.md)
- [TECHNICAL_NOTE.md](TECHNICAL_NOTE.md)
- [PRIVACY.md](PRIVACY.md)
- [SECURITY.md](SECURITY.md)

## 10. Limitations

Current limits are explicit:

- no human study
- no prompt-only comparison on a fair open-ended scenario set
- no claim that the cognitive architecture is psychologically faithful
- real-world channel validation is narrower than the unit/integration suite
- some architectural components need different scenarios to be falsified

The right reading is: this project is an inspectable cognitive architecture
prototype with one demonstrated load-bearing component and a clear path for
future evaluation, not a finished scientific claim.
