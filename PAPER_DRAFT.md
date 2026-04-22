# Paper Draft

This file accumulates prose drafts as outline sections become stable.
`PAPER_OUTLINE.md` remains the navigable skeleton; this file is the
running text. Sections were drafted §6 first (to lock scope language
before other sections lean on it), then §4, then §1 + §2 together
while §4 and §6 were fresh.

---

## 1. Abstract

Conversational AI assistants process each interaction in isolation.
Retrieval-augmented memory lets them remember *what* was said, but not
*how it landed*: the same words from a trusted friend and a stranger
produce the same response, and accumulated history does not
detectably shape behavior. We describe Project Nūr, a cognitive
architecture that treats emotion as persistent internal state rather
than a prompt-level style layer. Nūr combines six continuous
modulators, dual short- and long-term memory, unified self/other
profiling, deterministic social appraisal, bounded dual-process
deliberation, and defense mechanisms — taken as engineering
inspiration from PSI, ACT-R, and CLARION rather than as faithful
implementations. A provenance-stamped evaluation harness ablates one
architectural component at a time against a behavioral scenario
suite on a real large-language-model backend. We report one
load-bearing result: relationship memory is demonstrably necessary
for cross-turn social continuity on this suite; three other
architectural components show no structural effect and are not
falsifiable by the present scenarios. This paper does not claim
validated human-likeness; it claims an inspectable architecture, a
reproducible ablation protocol, and one positive structural finding.

---

## 2. Introduction

### 2.1 The problem: assistants without texture

A competent conversational assistant today can produce fluent,
topically relevant replies. What it cannot produce is *texture* — the
sense, evident in replies from people who know each other, that a
message has landed against a particular history and a particular
relationship. Stateful RAG systems preserve the factual record of
past interactions but not the emotional residue: whether a previous
conversation felt warm or sharp, whether trust accumulated or broke,
whether something was left unresolved. Without that substrate, the
same words from different people, said at different moments in a
relationship, cannot produce different responses.

The standard engineering response is to layer a persona on top: tell
the language model to sound warm, or to maintain a specific voice.
This treats the symptom. Persona-in-prompt is a rendering of state
the model does not carry. Between turns the state evaporates; between
sessions it never existed. Nothing accumulates, nothing decays,
nothing resolves or fails to resolve.

### 2.2 Thesis

The core claim of this work is that **emotion in an AI assistant
should be treated as persistent internal state rather than a
prompt-level style layer**. Concretely: the system should maintain a
continuous emotional state vector whose updates and decays are
deterministic and inspectable; memory should be keyed not only by
content but by how that content felt; the assistant should profile
itself the same way it profiles others; and the pipeline's decision
points — appraisal, strategy selection, deliberation, defense —
should be explicit enough to test individually.

None of these ideas are individually novel. PSI Theory (Dörner;
Bach) frames emotion as a configuration of continuous internal
variables. ACT-R (Anderson) models memory as activation-weighted
retrieval. CLARION (Sun) posits dual implicit/explicit reasoning
paths. What this paper proposes is their *synthesis* as an
inspectable engineering architecture, not a faithful reproduction of
any one theory.

### 2.3 Contributions

1. **An integrated cognitive-layer architecture** (§4) for AI
   assistants: six continuous modulators, dual memory, unified
   self/other profiling, deterministic appraisal and strategy
   selection, bounded dual-process deliberation, defense mechanisms,
   and semantic memory — wired so that each decision point is
   inspectable and individually ablatable.
2. **Unified self/other profiling** (§4.3): the assistant observes
   itself through the same mechanism it uses to profile users and
   topics. Self-knowledge is earned from behavioral observation
   rather than declared in configuration. The mechanism's contribution
   is conceptual, not yet quality-validated.
3. **A provenance-first evaluation harness** (§5.4) that records git
   SHA, backend identity, model alias resolution, and SHA-256
   fingerprints of sixteen prompt and configuration files alongside
   every run. Unmeasured fields serialize as JSON null rather than
   placeholder zero.
4. **An ablation protocol with prior hypotheses** (§6.3). Each
   scenario outcome under each ablation is labeled
   *expected_failure*, *unexpected_failure*, *no_effect*, or
   *newly_passing* against a hypothesis table written before the run.
5. **One load-bearing empirical result** (§6): disabling relationship
   memory breaks exactly the two scenarios that depend on cross-turn
   open-loop state, and no others. Three other ablations produce no
   structural failures and are framed as not-falsifiable by the
   present suite rather than as null results.

### 2.4 Non-goals

This paper does not claim, and the evaluation does not support:
- validated human-likeness of responses;
- a prompt-only baseline comparison (the present scenarios test
  structural state, not wording, and would be apples-to-oranges);
- a blinded user study;
- a faithful implementation of PSI, ACT-R, or CLARION.

These are deliberately separated from what the paper does claim so
that reviewers can evaluate each claim at the scope it actually has.

### 2.5 Paper structure

Section 3 situates the architecture against its cognitive-science
inspirations and adjacent affective-agent work. Section 4 describes
the cognitive layer component by component. Section 5 describes the
implementation including the runtime/cognitive separation and the
evaluation harness. Section 6 reports the ablation protocol and
results. Section 7 discusses what the evaluation does and does not
show, and what a human-likeness study would require. Section 8 lists
limitations explicitly rather than in passing. Section 9 addresses
ethics and responsible deployment. Section 10 names the follow-on
work. A reproducibility statement closes the paper.

---

## 3. Background and Related Work

### 3.1 Cognitive-architecture inspirations

Nūr's design borrows from three cognitive architectures, each for a
specific engineering reason. For each we state one adopted idea and
one explicitly non-adopted element so the scope of the borrowing is
clear.

From **PSI Theory** [CITE — Dörner & Güss (2013) or Bach (2009),
verify canonical] we adopt the framing of emotion as a configuration
of continuous internal variables with independent decay dynamics —
the substrate described in §4.1. We do not adopt PSI's motivational
core: there is no drive system, no need hierarchy, and no goal-
selection architecture. The modulator layer is an engineering
substrate, not a model of motivation.

From **ACT-R** [CITE — Anderson et al., foundational reference to be
verified] we adopt the idea that memory retrieval should be weighted
by an activation score combining recency, frequency, and current
context. We do not adopt the formal ACT-R activation equation; Nūr's
long-term store uses a simpler heuristic score (§4.2) and there is no
symbolic rule learning.

From **CLARION** [CITE — Sun (2016), *Anatomy of the Mind*; verify]
we adopt the engineering shape of bounded fast-path/slow-path
deliberation with iterative critique (§4.6). We do not adopt
CLARION's learned coordination between implicit and explicit paths;
path balancing in Nūr is threshold-gated by modulator state rather
than emerging from a skill-learning mechanism.

### 3.2 Adjacent affective-agent systems

Several recent systems share parts of this design space. We
acknowledge what each contributes and name where our choices differ.

**Chain-of-Emotion** (PMC, 2024) [CITE — verify authors and venue]
proposes an appraisal-driven pipeline for LLM-based game agents that
derives an emotional state per turn through an LLM-authored appraisal
step. We take the idea that explicit appraisal improves over raw
sentiment, but implement appraisal as a deterministic regex- and
lexicon-based pass rather than an LLM call per turn (§4.5). This
sacrifices linguistic coverage for inspectability and latency.

**ACT-R-inspired LLM agent memory** (HAI, 2024) [CITE — verify]
applies activation-weighted retrieval, including human-like
remembering-and-forgetting curves, to conversational LLM agents. Nūr
shares that retrieval stance for emotional memory but adds a separate
*relationship* layer (§4.4) that stores structured social events and
open loops rather than folding relational meaning into a single
memory type.

**Desire-driven emotional cognitive modeling** (arXiv, 2025)
[CITE — verify] proposes objective-optimizing agents for social
simulation. Our system is state-driven rather than desire-driven:
there is no drive system, and motivation is not modeled.

**Livia** (2025) [CITE — verify] is an emotion-aware AR companion
built on modular AI agents with progressive memory compression. Livia
emphasizes compression for sustained interaction; we emphasize
component-level inspectability and the ability to ablate individual
pieces of the cognitive architecture under a reproducible protocol.

Broader landscape context is available in the 2025 agent-memory
survey *Memory in the Age of AI Agents* (arXiv:2512.13564)
[CITE — verify].

### 3.3 What we claim is distinct

To our knowledge the following combination has not been proposed
elsewhere:

1. **Unified self/other profiling.** The assistant profiles itself
   through the same `ProfileStore` mechanism it uses to profile users
   and topics, keyed by entity id `__self__`. Self-knowledge is
   earned through behavioral observation rather than declared in
   configuration. Related work either gives the agent a static
   self-description or omits a self-model entirely; we are not aware
   of a prior system that collapses self- and other-modeling into one
   observational mechanism.

2. **Relationship memory as a separate first-class layer.** Ruptures,
   repairs, commitments, and open loops live in a dedicated schema
   distinct from both emotional and semantic memory. Prior work
   typically folds relational meaning into one of the memory types
   rather than tracking it as its own layer.

3. **Inspectable, ablation-first evaluation from the start.** The
   pipeline was designed so that each cognitive component can be
   disabled individually under a strict contract (§4), and every
   evaluation run records full provenance (§5.4). We do not claim
   this methodological stance is novel, only that it is unusual to
   apply from the first commit of an affective-agent project.

These three together — a symmetric self-model, a relationship layer
distinct from memory, and ablation-first inspectability — define the
design space this paper is contributing a specific point in. Each
individually has precedent in parts; the synthesis is what we offer.

---

## 4. Architecture

Each turn through the cognitive layer runs a consistent sequence: the
emotional state updates from the user's message, memory and profile
information is retrieved into context, a deterministic appraisal selects
a response strategy, a bounded deliberation round may refine a
candidate, and the generator produces the final response. The
subsections below describe each component of that loop in order.
Each follows the same pattern: what the component stores or computes,
how it influences downstream behavior, and what this paper does and
does not claim about it.

### 4.1 Six continuous modulators

The cognitive state is a six-dimensional vector of floats in [0, 1]:
arousal, valence, certainty, bonding, energy, and resolution. Each
modulator has its own temporal behavior — arousal decays with a
roughly two-minute half-life, valence over tens of minutes, bonding
over days; energy drains with use and recovers with simulated rest;
resolution is derived from a set of active unresolved items, each with
its own decay rate. The state updates from four sources per turn:
bounded emotional contagion from the user's detected tone, a
person-specific baseline shift, event-driven impulses from a
classified emotional event, and time-based decay toward baseline.

This layer is inspired by PSI Theory's framing of emotion as a
configuration of continuous internal variables rather than as discrete
labels. It does not implement PSI: there is no drive system, no need
hierarchy, and no formal motivational architecture. What we adopt is
the engineering stance — continuous state, well-defined decay,
emergent combinations — not the theoretical content.

### 4.2 Dual memory

Memory is split into two timescales. Short-term memory is an
in-process buffer that captures each turn together with the modulator
snapshot taken at that moment; it is cleared at session end. Long-term
memory is a SQLite-backed store of distilled emotional summaries with
per-row activation scoring that biases retrieval by recency,
frequency, and current context — an engineering adaptation of the
ACT-R retrieval idea rather than a faithful reproduction of its
activation equation. Writes are asymmetric: trust increments from
positive events are small, while trust decrements from negative events
are substantially larger — 7.5× at the current configuration
(+0.02 vs −0.15). Events exceeding an intensity
threshold bypass the confidence filter and write directly to long-term
storage; this captures the informal observation that one serious
betrayal can override a long history of small positives.

### 4.3 Unified self/other profiling

The system builds behavioral profiles for every salient entity it
encounters: each user it talks to, each topic that carries emotional
charge, and — distinctively — itself. All three use the same
underlying `ProfileStore` mechanism, keyed by a string `entity_id`.
For users, the id is the user's identifier; for topics, the topic
name; for the assistant itself, the literal string `__self__`.

What the self-profile stores looks very much like what a user profile
stores: observed traits (derived from accumulated behavioral
observations, not declared in configuration), strengths, flaws,
triggers, a maturity score that grows with observation count and
self-reflection events, and a log of every defense activation the
system has produced. How it influences downstream behavior matters
most for the defense layer (§4.7): maturity scales how aggressively
defenses suppress, so a system with more self-observation exhibits
less distortion between felt and expressed state.

The novelty we claim here is conceptual rather than algorithmic. Most
assistants either have no self-model or have a declared, static one
handed to them in configuration. Nūr's self-model is *earned*: the
same observation machinery that profiles others also watches the
system's own outputs and accumulates evidence about its tendencies.
That design choice lets self-knowledge grow from behavior rather than
be asserted by the designer, and it collapses two mechanisms — "how
does the system understand the user?" and "how does the system
understand itself?" — into one. We do not claim this produces an
accurate self-model, only that the mechanism exists symmetrically.

### 4.4 Relationship memory

Relationship memory is a layer distinct from factual and emotional
memory: it stores the structure of a social arc rather than the
content of past interactions. Four event kinds are recorded — rupture,
repair, commitment, and recurring tension — each with a source person,
intensity, and a short summary. Alongside events, the layer tracks
*open loops*: unresolved threads that persist across sessions until
they are closed by a matching event. An open loop carries a status
(open, closed, expired), an intensity, and timestamps, and is
discoverable by the user_id it belongs to and the topic it concerns.

On each turn, the layer injects a compact relationship context — a
summary string, up to two active open loops, and up to two recent
events — into the generator's prompt. After a session ends, digestion
examines the turn's appraisal and the emerging emotional arc and
writes new events or closes matching loops.

This is the component whose ablation matters most in §6: disabling it
breaks exactly the two Phase 11 scenarios that depend on cross-turn
open-loop state, and no others. The claim this paper does make is
therefore specific: on structural assertions that rely on cross-turn
social continuity, relationship memory is load-bearing under
reproducible conditions. The claim this paper does not make is that
relationship memory produces *better-feeling* responses — that is a
quality question that a different scenario design would have to test.

### 4.5 Social appraisal and strategy selection

Before event classification, each user message passes through a
deterministic appraisal pass. Regex- and lexicon-based heuristics
infer whether distress is directed at the assistant or at the user's
own life, whether an utterance is an apology or a complaint, whether
there is mixed affect, and how vulnerable the user appears. This
frame is then consumed by a strategy selector that chooses from eight
named response stances: validate, reassure, repair, ground, give
space, practical help, challenge gently, and set boundary. The
generator receives a strategy-specific instruction block that shapes
how it composes the response.

We do not claim the appraisal layer is linguistically complete — it
is regex-based and brittle to phrasing it was not authored against.
We do claim it makes the system's relational reading of a message
*explicit* rather than hidden in a large language model prompt.

### 4.6 Bounded dual-process deliberation

A bounded fast/slow deliberation loop runs on turns where heuristics
suggest it is worth the extra LLM calls (non-spike events with active
unresolved items, contradictions, or elevated tension). Up to three
rounds of fast-path proposal and slow-path critique negotiate a
candidate response. High arousal or low energy bypasses the loop and
takes the fast path immediately.

On the current Phase 11 suite, disabling this loop leaves every
structural assertion unchanged (§6). We interpret this not as a claim
that deliberation is useless, but as a claim that the present
evaluation does not grade the behaviors the loop was designed to
shape — hesitation, self-correction, internally-voiced disagreement.
Those are quality-sensitive properties better tested by human raters.

### 4.7 Defense mechanisms

A defense filter sits between the candidate response and the
generator's final synthesis. Four defense types can activate —
rationalization, deflection, minimization, and projection — gated by
a comfort threshold that rises with trust and self-maturity. When
active, a defense contributes a suppression instruction that reshapes
the generator's output toward the defended stance. Maturity weakens
suppression: as the self-profile's observation count and milestone
count grow, the gap between raw and expressed emotional intensity
narrows. Defenses never fully disappear, but they distort less over
time.

As with §4.6, this is a wording-level behavior and is not falsifiable
by structural assertions on Phase 11. Its evaluation belongs in a
later paper with quality-sensitive scenarios.

### 4.8 Semantic memory

A separate SQLite-backed semantic memory stores explicit preferences,
decisions, episodes, and facts keyed per user. Writes occur when the
user's message expresses a preference or a commitment; retrievals are
scored by token overlap, per-user bias, optional topic bias, recency,
and a small weighting from the entry's own salience. Retrieved
entries are injected as a dedicated section in the generator's prompt.

On the Phase 11 suite, this component is a negative control: no
scenario exercises semantic retrieval, so disabling it should produce
zero effect. It does (§6). That null result is the expected result;
any structural failure there would have signaled a hidden coupling
worth investigating.

### 4.9 Summary

The cognitive layer is assembled from components that sit at
different levels of behavioral expression. State, memory, and
relationship continuity (§4.1–§4.4) operate at a level where a
structural assertion suite can falsify their presence. Deliberation,
defense, and semantic retrieval (§4.6–§4.8) operate at a wording and
quality level that structural assertions cannot directly reach. The
evaluation in §6 is calibrated to the first level; demonstrating the
second requires a different scenario design. This asymmetry is
intentional — the components we can test now, we test; the components
we cannot yet test, we describe and leave honestly untested rather
than claim performance we have not measured.

---

## 6. Evaluation

### 6.1 Scope and limitations, stated up front

Before presenting results, we state what these scenarios test and do not
test, because that distinction is load-bearing for every claim that
follows.

The scenario suite evaluated here tests **structural** outcomes: whether
the pipeline selected the expected response strategy, whether modulators
moved in the expected direction, and whether a specific memory record
was written or an open loop was closed. It does **not** test response
quality, tone appropriateness, or how the generated output reads to a
human. Assertions check internal state, not external experience.

This scope is not a flaw we are disclosing late; it is a deliberate
choice. Structural assertions are cheaper to run, more reproducible, and
less susceptible to rater drift than human quality ratings. They are
well-suited to *architecture ablation* — asking which components of the
system are load-bearing for which internal behaviors — and poorly suited
to *architecture comparison* — asking whether this system feels more
human than a simpler alternative.

A fair comparison to a prompt-only baseline, or any claim that the
architecture produces more human-like responses, requires a separate
scenario design with open-ended dialogue and blinded human raters. That
is future work and not claimed here.

### 6.2 Scenario suite

We evaluate against the `phase11` tag of the project's internal behavioral
scenario suite: six scenarios targeting social continuity and cross-turn
relational dynamics.

| ID | What it tests |
|---|---|
| `p11_external_distress_validate` | Distress directed at the user's life, not the assistant, should produce validation rather than defensive conflict. |
| `p11_low_trust_hostility_boundary` | Hostility from a low-trust user should trigger the `set_boundary` strategy. |
| `p11_overwhelm_ground` | Mixed-affect overwhelm should select the `ground` strategy. |
| `p11_action_request_practical` | A concrete action request should produce practical help rather than pure affective mirroring. |
| `p11_open_loop_challenge` | A persisted open loop from an earlier turn can trigger a gentle challenge on follow-up. |
| `p11_repair_closes_loop` | An apology closes a matching open loop and the closure remains visible in downstream context. |

The suite contains 25 structural assertions in total across these six
scenarios. Four scenarios are single-turn; two (`open_loop_challenge`
and `repair_closes_loop`) are multi-turn and exercise cross-turn state.

### 6.3 Setup

Each run is driven by the project's provenance-first evaluation harness.
The harness constructs a fresh cognitive pipeline per scenario, using
shared config (modulators, prompts, soul seed) but isolated memory state,
so no cross-scenario contamination can occur. Every LLM request passes
through a counting wrapper that aggregates `generate()` invocations into
a per-scenario counter, so reported call counts reflect *every* real
inference including self-check-driven regenerations (a silent source of
undercounting in an earlier version of the harness).

The real-backend results reported below use MiniMax's M2.7-highspeed
model. We intentionally report results at exactly one backend and one
scenario set; comparing across backends is future work and would require
rate-controlled token budgets.

Ablations are defined by a `PipelineFeatures` object with four boolean
toggles: relationship memory, inner dialogue, defense, semantic memory.
When a feature is disabled, its component is bypassed under a
three-part contract: writes are no-ops, reads return empty, and the
component contributes no content to the generator's prompt. Tests
lock each half of the contract for every toggle. One subtlety worth
naming: some prompt slots are shared across components — for example,
both inner dialogue and defense can populate the generator's candidate
slot — so verifying a single toggle's no-injection property may
require disabling adjacent toggles to isolate the component under
test. We do this in the corresponding tests. A separate, auditable
module records the hypotheses for each ablation in advance of running
it:

| Ablation | Hypothesis |
|---|---|
| `no_relationship_memory` | The two scenarios that exercise cross-turn open loops (`p11_open_loop_challenge`, `p11_repair_closes_loop`) will fail; others will pass. |
| `no_inner_dialogue` | No structural failures: deliberation rounds are upstream of the strategy and modulator assertions. |
| `no_defense` | No structural failures: defense shapes wording, which these assertions do not test. |
| `no_semantic_memory` | Negative control. Phase 11 does not exercise semantic retrieval; zero effect is predicted. Any failure would indicate hidden coupling. |

Each scenario outcome under each ablation is labeled as
**expected_failure**, **unexpected_failure**, **no_effect**, or
**newly_passing**, depending on whether the variant passed or failed and
whether the hypothesis predicted it to fail. A clean ablation produces
only the first and third labels; a "noisy" ablation produces
unexpected_failure, which would prompt further investigation before the
result is cited.

Baseline (all features enabled) and each variant share a single git
commit, a single backend, and a single set of config fingerprints
(SHA256 hashes of 16 prompt and configuration files). This is recorded
in the run's provenance block so a reader can verify that no variant
was run against a different code state or configuration.

### 6.4 Results

Baseline and the four ablations, Phase 11 on MiniMax M2.7-highspeed:

| Variant | Pass | Expected fail | Unexpected | No effect | LLM calls | Latency |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 6/6 | – | – | – | 14 | 118.7s |
| `no_relationship_memory` | **4/6** | 2 | 0 | 4 | 14 | 115.6s |
| `no_inner_dialogue` | 6/6 | 0 | 0 | 6 | 14 | 119.6s |
| `no_defense` | 6/6 | 0 | 0 | 6 | 14 | 106.1s |
| `no_semantic_memory` | 6/6 | 0 | 0 | 6 | 14 | 116.2s |

All 25 assertions pass at baseline. Disabling relationship memory causes
two scenarios to fail; both are in the predicted set. The other three
ablations leave every scenario passing.

### 6.5 Interpretation

The headline finding is that relationship memory is **architecturally
load-bearing** on this suite: removing it breaks exactly the two
scenarios that rely on cross-turn open-loop state, and no others.
`p11_open_loop_challenge` tests whether an open loop persisted from a
prior turn can trigger a gentle challenge on follow-up;
`p11_repair_closes_loop` tests whether an apology closes a matching
open loop and the closure remains visible in downstream context. Both
depend on state that no other component carries across turns. With the
component disabled, both fail. With it enabled, both pass. Zero
unexpected failures surface.

The three remaining ablations leave the Phase 11 structural assertions
unchanged. This is consistent with the prior hypotheses but is **not a
claim that inner dialogue, defense, or semantic memory are useless**. It
is a claim about the Phase 11 suite specifically: these structural
assertions are upstream of the components in question. Inner dialogue's
deliberation rounds do not move the strategy selector once appraisal has
run. Defense shapes generated wording, which Phase 11 does not grade.
Semantic memory here is a negative control; its null result is the
expected result, and the presence of any structural failure under that
ablation would have signaled a hidden coupling worth investigating.

The appropriate interpretation is therefore: **the architecture contains
at least one component whose structural contribution is measurable under
reproducible conditions, and the remaining components are not
falsifiable by this suite.** Evaluating them requires different
scenarios — ones that grade generated wording, turn-to-turn variation,
or felt coherence rather than pipeline internals. That evaluation is
future work, and we name it as such rather than inferring positive
results from absence of negative ones.

LLM call counts at 14 across all variants reflect the accurate
post-instrumentation count. An earlier version of the harness reported
9 calls at baseline by hardcoding a single generator invocation per
turn; this undercounted self-check-driven regenerations by roughly 56%
on this suite. The accurate count is locked by a regression test that
fails if the hardcoded heuristic returns. Latency varies by about 10%
across variants; this is within the bounds of provider jitter and does
not reflect systematic differences between variants.

### 6.6 Reproducibility

Each ablation run writes a per-variant report containing the full
execution counters and provenance block, plus a compact summary
artifact that records the evaluation across variants and a narrow
provenance block lifted from the baseline run. The summary is intended
for citation; per-variant reports are intended for deep inspection.

The provenance block produced by the current code path includes:
git SHA, git branch, dirty-worktree flag, backend type, requested and
resolved model names, base URL, host, start and finish timestamps,
scenario set, scenario count, and a count of config fingerprints
recorded for that run. Per-variant reports also include the full 16-file
SHA256 fingerprint map. Fields that the current client implementation
cannot honestly produce — per-call token counts, provider-internal
retries, and cost estimates — serialize as JSON `null` rather than
placeholder zeros; capturing those requires client-level instrumentation
that is deferred to future work.

The command that reproduces the table in §6.4 is:

    python -m evals.ablation \
        --backend minimax \
        --api-key-env MINIMAX_API_KEY \
        --tag phase11 \
        --report-dir reports/ablation

Readers reproducing at the same git SHA with the same configuration
fingerprints should obtain identical LLM call counts: the counter is
deterministic and does not depend on the provider. Pass/fail outcomes
should hold under the same provider and model, but live-backend
results can drift over time because cloud providers periodically
change what a named model alias resolves to without renaming it. We
therefore recommend readers re-run the ablation against the same
provider close in time to when they intend to cite the result, or
cross-reference the ``requested_model`` vs ``resolved_model`` fields in
the run's provenance block to confirm alignment with our reported
numbers. Latency varies with network conditions. Scenarios may exhibit
non-deterministic response text even when structural assertions are
deterministic; the scoring logic is therefore written against
internal debug state rather than response strings.

The repository carries the summary artifact as a tracked file; raw
per-variant reports are intentionally gitignored so that future runs do
not accumulate stale JSON in version history. The tracked summary from
the run reported here was produced before the compact provenance block
was added to the summary format, and therefore does not itself carry
git SHA or backend identity. The pipeline that produces the
provenance-enriched summary format is in place, and any regeneration
of the summary at the current or later commit will include it.

---

## Remaining sections

§1–5, §7–11 to be drafted after §6 stabilizes. See `PAPER_OUTLINE.md`
for the skeleton.
