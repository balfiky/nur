# Project Nūr — Overview

> What the project is, why it exists, what the evidence supports today,
> and what it does not claim. The public reader path.

**Honest scope up front.** Nūr is a research prototype. It does not claim
validated human-likeness, consciousness, therapeutic value, or
psychological validity.

## Contents

1. [The idea](#1-the-idea)
2. [Theory stance](#2-theory-stance)
3. [Architecture at a glance](#3-architecture-at-a-glance)
4. [Evaluation](#4-evaluation)
5. [What the evidence supports](#5-what-the-evidence-supports)
6. [What this does *not* show](#6-what-this-does-not-show)
7. [Running it](#7-running-it)
8. [Further reading](#8-further-reading)

## 1. The idea

Many LLM assistants sound emotionally responsive, especially once a
prompt gives them a persona and access to prior conversation. The
response still often feels like styling layered on top of the current
turn rather than the product of accumulated history, tension, and
consequence. Persona-in-prompt is a rendering of state the model does
not carry. Between turns the state evaporates; between sessions it
never existed. Nothing accumulates, nothing decays, nothing resolves
or fails to resolve.

**Core thesis:** emotion in an AI assistant should be treated as
persistent internal state, not a prompt-level style layer. The state
should persist, decay, and remain inspectable. A response after history
should be shaped by accumulated context, not only by a fresh prompt.

Concretely, Nūr maintains:

- a continuous six-modulator emotional state with deterministic decay
- memory keyed both by content and by how content landed
- a self-profile earned through behavioral observation, using the same
  mechanism the system uses to profile users
- a relationship layer that tracks rupture, repair, commitments, and
  unresolved loops across sessions
- explicit decision points — appraisal, strategy, deliberation,
  defense — each testable in isolation

The LLM still writes the final language. The cognitive layer changes
what the LLM is told to write against.

## 2. Theory stance

Nūr takes engineering inspiration from PSI Theory (continuous
modulators, energy as a resource, emergent behavior from variable
configurations), ACT-R (memory weighted by recency, frequency, and
context), and CLARION (bounded fast/slow deliberation).

It is explicitly **not** a faithful implementation of any of them. No
formal PSI drive system, no ACT-R production rules, no learned implicit/
explicit coordination. The goal is inspectable engineering, not
theoretical reproduction.

## 3. Architecture at a glance

Two runtime hosts (`nur` for console/Telegram/debug, `nur-web` for the
FastAPI service) share one session manager and one cognitive pipeline.
Persistence, LLM, and tool execution sit at the edges.

![Runtime architecture](diagrams/runtime-architecture.png)

The emotional state is a six-dimensional vector in [0, 1]: arousal,
valence, certainty, bonding, energy, resolution. Each modulator has its
own temporal behavior — arousal decays in roughly two minutes, valence
over tens of minutes, bonding over days; energy drains with use and
recovers with rest. State updates from several sources per turn:
bounded contagion, a person-specific baseline shift, event-driven
impulses from classified emotional events, and time-based decay toward
baseline.

Memory is split across short-term (in-process, cleared at session end),
long-term (SQLite, distilled emotional summaries), relationship (social
arc: rupture / repair / commitment / recurring tension / open loops),
and semantic (preferences, decisions, facts). Writes are asymmetric:
trust increments from positive events are small; decrements from
negative events are 7.5× larger at current configuration. Events above
an intensity threshold bypass confidence filtering and write directly
to long-term storage.

For structural detail — per-turn stage order, LLM call accounting,
persistence layout, auth/tool gates, and the full component claim map —
see [ARCHITECTURE.md](ARCHITECTURE.md). The structural reference doc is
where every diagram and module map lives.

## 4. Evaluation

The current evaluation is a structural ablation harness, not a human
judgment study.

![Evaluation and ablation harness](diagrams/evaluation-ablation-harness.png)

The harness runs scripted scenarios against a real pipeline, stamps
each run with provenance (git SHA, resolved model, config fingerprints
over 16 prompt and config files), and emits JSON reports.

Headline ablation on the current Phase 11 scenario set:

| Variant | Pass | Expected fail | Unexpected | Interpretation |
|---|---:|---:|---:|---|
| baseline | 6/6 | – | – | reference run |
| no_relationship_memory | **4/6** | 2 | 0 | load-bearing on cross-turn continuity |
| no_inner_dialogue | 6/6 | 0 | 0 | not falsifiable by this suite |
| no_defense | 6/6 | 0 | 0 | not falsifiable by this suite |
| no_semantic_memory | 6/6 | 0 | 0 | negative control behaved as expected |

Each outcome is labeled against a hypothesis table authored before the
run (`expected_failure`, `unexpected_failure`, `no_effect`,
`newly_passing`). Every prediction held.

## 5. What the evidence supports

![Component claim map](diagrams/component-claim-map.png)

The honest reading:

- **Relationship memory is load-bearing on the present suite.** Disabling
  it breaks exactly the two scenarios that depend on cross-turn open-loop
  state and no others. Zero unexpected failures.
- **Semantic memory is a negative control here.** Zero effect is the
  expected answer because the current scenarios do not exercise it.
- **Inner dialogue and defense are present but not falsifiable by this
  suite.** Phase 11 scenarios grade structural assertions (strategy
  selection, modulator direction, memory writes); these components
  primarily affect wording or deliberation rounds that those assertions
  do not see.
- **Unified self/other profiling is an architectural claim, not a
  current benchmark win.**
- **Emotional modulators have unit and integration coverage**, not an
  ablation result — their correctness is verified directly through the
  test suite.

## 6. What this does *not* show

Stated up front so no reader has to infer it:

- **No validated human-likeness claim.** We do not show that Nūr's
  responses read as more human or more emotionally coherent to people.
  That requires a blinded user study with matched baselines —
  explicitly deferred future work.
- **No prompt-only baseline comparison.** These scenarios test pipeline
  internals; a prompt-only system has no internals to assert on, so a
  fair comparison is apples-to-oranges. It needs different scenarios,
  blinded raters, matched token budget, and matched memory affordance —
  a separate experimental program.
- **No faithful PSI / ACT-R / CLARION implementation.** Engineering
  inspiration, not theoretical reproduction.
- **No provider-level telemetry (tokens, retries, cost).** Eval reports
  serialize those fields as JSON `null`; the current LLM clients do not
  extract them from responses. Capturing them is a straightforward
  follow-on that has not yet been done.
- **No multi-party conversation support.** Single-user scoping is
  architectural, not incidental.

## 7. Running it

See [README.md](../README.md) for install, first-run wizard, and quick
commands. See [DEPLOYMENT_AND_ADMIN.md](DEPLOYMENT_AND_ADMIN.md) for
production posture, `NUR_CONFIG_DIR`, and the admin console.

## 8. Further reading

- [ARCHITECTURE.md](ARCHITECTURE.md) — canonical structural reference
  (diagrams, module map, persistence layout, auth/tool gates,
  maintenance matrix).
- [research/PAPER_DRAFT.md](research/PAPER_DRAFT.md) — long-form
  version of this narrative with fuller background and related-work
  positioning.
- [PRIVACY.md](../PRIVACY.md), [SECURITY.md](../SECURITY.md) — what the
  system persists and how it is protected.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — how to add eval scenarios,
  regression tests, and architectural changes.
