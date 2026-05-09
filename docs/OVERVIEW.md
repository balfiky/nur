# Project Nūr — Overview

> What the project is, why it exists, what the evidence supports today,
> and what it does not claim. The public reader path.

**Honest scope up front.** Nūr is a research prototype — a cognitive
runtime, not an AGI claim and not a clinical tool. It does not claim
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

LLM agents are stateless by default. A prompt-and-persona may render the
*appearance* of continuity, but between turns the state evaporates;
between sessions it never existed. Nothing accumulates, nothing decays,
nothing resolves or fails to resolve. The agent-memory work of the last
two years (MemGPT, Letta, mem0, LangGraph state) addresses one slice of
that: it lets an LLM remember *facts* and *preferences*. It does not
typically address *who the agent is becoming*, *how it relates to a
specific person over time*, or *what it has learned that should now
shape its judgment*.

**Core thesis:** an LLM agent benefits from an explicit cognitive
runtime sitting alongside the model — engineered scaffolding that
persists state, lets that state decay, revises it against evidence, and
makes the whole structure inspectable. The LLM writes language; the
runtime holds *what the LLM writes against*.

Concretely, Nūr maintains five categories of persistent state outside
the model:

- **Memory** — short-term, long-term (valence-weighted), relational arc
  (rupture, repair, commitments, open loops), semantic (preferences,
  decisions, facts).
- **Identity** — operator-set constitution, belief ledger with
  confidence and decay, motivational drives that drift, self-traits
  observed through behavior. Seeded by `soul.yaml`, shifted by
  experiences operators ingest.
- **Self-evolution** — wall-clock metabolism that decays weak beliefs,
  promotes recurring themes to beliefs, and emits open-question rows
  for unresolved gaps; a daily LearningBudget governs how often the
  runtime can ask the user.
- **Skills** — imported via paste / file / zip, loaded only when
  `applies_when` chat hints match, with a `migrate_skill_to_life`
  helper for skills that turn out to be disposition.
- **Affective state** — six emotion modulators (arousal, valence,
  certainty, bonding, energy, resolution) with deterministic decay,
  driving appraisal and weighting memory retrieval.

Plus explicit decision points — appraisal, strategy, deliberation,
defense, self-check — each testable in isolation.

This is not "more memory features piled on top of a chatbot." It is an
attempt to define what stateful cognitive context for an LLM agent
looks like, in code that runs and that you can read. Relationship arc
asks how the runtime changes *toward a specific person*. Life history
asks how the runtime changes *its own judgment* in response to
experiences. Both are inspectable; neither is evidence of
consciousness.

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
The web debug panel consumes serialized debug state to show the
persona view, relationship view, deterministic "Why this response?"
explanation, known state deltas, and compact current-turn memory summaries.
These are presentation surfaces over the pipeline's state, not separate
cognition.
The `/settings#observability` page uses the same runtime-owned state outside
the chat interface: it lists active sessions from Web, Telegram, console, or
future channels and renders the selected session's emotional, perception,
relationship, Life, memory, skills, tools, and explanation state. Legacy
`/persona` and `/dashboard` browser routes redirect there.
Telegram mirrors that principle with non-mutating introspection commands
(`/state`, `/why`, `/memory`, `/loops`, `/repair`, `/persona`) that read
active-session debug state without creating sessions or changing cognition.

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

Life history is a separate identity-level store under
`data/shared/life_history.db`. It is append-oriented: an experience event
records what Nūr encountered, and evolution events record what changed
afterward. Supported inputs include pasted text, uploaded text/Markdown files,
advanced local files, and explicit conversation learning requests such as
"learn from this project: <url>". Current outputs include belief revisions, drive
changes, self-trait observations, and future-behavior tendencies. This is
observable in `/admin` → **Life**. Normal runtime sessions now retrieve a
compact version of those revisions for generation, so the layer can influence
chat behavior. This still is not a claim that the assistant is independently
alive or scientifically validated.

A small set of self-evolution mechanics turn the ledger from a write-only
record into an evolving one. A wall-clock metabolism tick, rate-limited to
≥1 day elapsed, decays belief confidences, revokes weak beliefs, decays
theme weights, promotes strong recurring themes to beliefs, and emits
open-question rows for unresolved gaps (contradictions, low-confidence
themes, drive-gap signals). An operator-authored **constitution** sits as
a stable orientation above evolving beliefs in every prompt. An **ask-user
autonomy** layer (Sprint 5) lets the runtime surface an open question to
the user during chat when budget and drives align, then mark it
`pursuing`; the default `LearningBudget` allows three questions per day.
**Trigger-time skill retrieval** filters which imported skills load based
on chat-message hint tokens, so always-on skills don't bloat every prompt.
Operators can move a "skill" that's actually disposition into Life History
with `migrate_skill_to_life`. See
[ARCHITECTURE.md § Self-Evolution Model](ARCHITECTURE.md#self-evolution-model)
for the full mechanic and endpoint inventory. None of this constitutes
agency or consciousness; it makes character drift visible, decayable, and
operator-correctable.

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

Additional structural suites now cover the newer cognitive layers:

| Suite | Baseline | Diagnostic ablation result | Report |
|---|---:|---|---|
| Phase 12 relationship | 7/7 | `no_relationship_memory` fails 5 expected cross-session/open-loop cases | `reports/ablation/relationship_phase12_summary.json` |
| Phase 13 Life History | 6/6 | `no_life_history_context` fails 3 expected context/influence cases | `reports/ablation/life_history_summary.json` |
| Semantic memory | 6/6 | `no_semantic_memory` fails 5 expected preference/decision/retrieval cases | `reports/ablation/semantic_memory_summary.json` |

These are still structural assertions over debug state, stored records,
strategy traces, and ablation labels. They are not user-perceived
human-likeness measurements.

## 5. What the evidence supports

![Component claim map](diagrams/component-claim-map.png)

The honest reading:

- **Relationship memory is load-bearing on the present suite.** Disabling
  it breaks exactly the two scenarios that depend on cross-turn open-loop
  state and no others. Zero unexpected failures.
- **Relationship memory remains load-bearing under the expanded Phase 12
  suite.** The dedicated report shows expected failures only for the
  relationship/open-loop/commitment cases.
- **Life History now has bounded structural influence.** The Phase 13 suite
  verifies that Life History context can produce neutral, bounded,
  inspectable `LifeInfluence` pressure on strategy tie-breaks, action
  variables, proactive scoring, and semantic salience. This proves a
  deterministic structural path, not human-like agency.
- **Semantic memory has its own structural suite.** Preference, decision,
  per-user isolation, topic ranking, and salience/recency behavior are now
  tested directly; it remains a negative control for pure relationship suites.
- **Longitudinal arcs have focused regression coverage.** Warm rupture/repair,
  recurring tension, commitment resolution, LifeInfluence relationship
  tie-breaks, and preference-plus-loop retrieval are tested over multiple turns.
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
- **No completed "self-independent character" loop.** Nūr can now record
  formative experiences from admin intake and explicit chat learning requests,
  but self-directed goal selection and strong runtime behavior changes from the
  life-history layer are still early.

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
