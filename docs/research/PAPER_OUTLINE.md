# Paper Outline: Project Nūr

**Working title:** An Inspectable Hybrid Architecture for Relational Continuity in AI Assistants

**Type:** architecture / prototype paper (not an empirical human-study paper)

**Target length:** 8–10 pages main text + references. Trims to 4 pages for short-format venues (CHI LBW, workshop papers).

**Target venues (in rough preference order):**
1. IUI (Intelligent User Interfaces) — good fit for hybrid architecture + interaction design
2. HAI (Human-Agent Interaction) — good fit for relational agent framing
3. IEEE ACII workshops — strongest affective-computing audience
4. arXiv preprint — no gate; useful for priority + discoverability while longer work continues

**Honest framing up front:** this paper claims an *inspectable, state-first architecture*; it does **not** claim validated human-likeness. Empirical user study is deferred to a follow-up paper.

---

## 1. Abstract (~150 words)

The problem: current AI assistants process each interaction in isolation. Even with RAG they remember *facts*, not *feelings*. The same words from a trusted friend and a stranger land identically.

The thesis: **emotion should be treated as persistent internal state, not as a prompt-level style layer.**

The approach: a hybrid architecture combining six continuous modulators, dual memory, unified self/other profiling, deterministic social appraisal, bounded dual-process deliberation, and defense mechanisms — inspired by PSI, ACT-R, and CLARION but not claiming faithful implementation of any of them.

The evidence: a provenance-stamped evaluation harness runs a behavioral scenario suite against real LLMs. One ablation result: disabling relationship memory breaks exactly the two scenarios that test cross-turn open-loop tracking, while three other component toggles have no measurable effect on structural assertions. This demonstrates at least one architecturally load-bearing component under a reproducible protocol.

The limitation: scenarios test internal structural state, not response quality. Human-likeness claims require a follow-up user study.

---

## 2. Introduction (~1 page)

### 2.1 Motivation

- LLM-based assistants produce fluent responses, but those responses lack *texture*. Same words land the same way regardless of history, context, or relationship.
- RAG-based memory adds facts, not feelings. It preserves *what* was said, not *how it landed*.
- The engineering shortcut — "tell the LLM to sound warm" — is a cosmetic fix for a structural problem.

### 2.2 Thesis

Emotions are not discrete labels. They are configurations of continuous internal variables that persist, decay, and interact with memory and relational context. Build that substrate, and behavior emerges from it.

### 2.3 Contributions

1. **Architecture**: a coherent integration of continuous modulators, dual memory, self/other profiling, and explicit response strategy selection.
2. **Unified self/other profiling**: the assistant observes itself via the same mechanism it uses to profile others (entity id `__self__`). Self-knowledge is *earned*, not declared.
3. **Inspectable pipeline**: every major decision point produces a traceable debug record (appraisal, strategy selection, dialogue rounds, defense activations, unresolved items).
4. **Provenance-first evaluation harness**: each eval run records git SHA, backend identity, config fingerprints (SHA256 of 16 files), scenario set, and execution counters. Reports are self-describing and reproducible.
5. **Architecture ablation protocol + one load-bearing result**: a prior-hypothesis table labels each scenario outcome under each ablation (expected-failure / unexpected / no-effect / newly-passing). Relationship memory is demonstrably load-bearing; three other components have no structural effect on this suite.

### 2.4 Non-goals (explicit)

- Not an implementation of PSI, ACT-R, or CLARION.
- Not a validated psychological model.
- Not a human-likeness claim — no user study in this paper.
- Not an autonomous agent or AGI.

### 2.5 Paper structure

One sentence per section.

---

## 3. Background and Related Work (~1 page)

### 3.1 Cognitive-architecture inspirations

- **PSI (Dörner/Bach)** — continuous modulators, motivated cognition. We adopt *continuous state* as the substrate.
- **ACT-R (Anderson)** — activation-weighted memory retrieval. We adopt *recency × frequency × context* biasing, not the full activation equation.
- **CLARION (Sun)** — fast/slow dual-process. We adopt *bounded iterative deliberation*, not a full learned implicit/explicit split.

We are explicit that these are **inspirations**, not implementations. No drive system (PSI), no formal activation formula (ACT-R), no emergent path balancing (CLARION).

### 3.2 Recent affective-agent work

- **Chain-of-Emotion** (PMC, 2024) — appraisal-based prompting for game agents.
- **Emotional Cognitive Modeling** (arXiv, 2025) — desire-driven agents for social simulation.
- **ACT-R + LLM memory** (HAI, 2024) — human-like remembering/forgetting in LLM agents.
- **Livia** (2025) — emotion-aware AR companion with progressive memory compression.

Where we differ: *unified self/other profiling*, *relationship-arc memory as a first-class layer* (not implicit in semantic memory), and an *inspectable pipeline with explicit strategy selection*.

### 3.3 Agent memory landscape

Brief survey pointer: "Memory in the Age of AI Agents" (arXiv:2512.13564, 2025). Position our relationship-arc layer as orthogonal to semantic/episodic memory.

---

## 4. Architecture (~2 pages)

### 4.1 Six continuous modulators

Table: arousal, valence, certainty, bonding, energy, resolution — with half-lives and what each represents. Emergent emotions illustrated (joy, anger, irritability) as points in the 6-D modulator space.

### 4.2 Dual memory

- **Short-term**: in-memory, cleared at session end.
- **Long-term**: SQLite, distilled summaries with asymmetric negativity bias (7.5× break-vs-build on trust).
- **Spike bypass**: intensity ≥ 0.8 writes directly to long-term, bypassing confidence thresholds.

### 4.3 Unified self/other profiling — the novel mechanism

Same `ProfileStore` mechanism used for users, topics, and the assistant's self-model (entity id `__self__`). The self-profile is *earned* through observation, not declared in config. Strengths, flaws, triggers, and maturity emerge from accumulated behavioral observations.

### 4.4 Relationship memory

First-class layer separate from factual/semantic memory. Records rupture/repair/commitment/recurring_tension events and open loops. This is the component the ablation shows is load-bearing.

### 4.5 Social appraisal and strategy selection

Deterministic passes. Appraisal distinguishes assistant-targeted vs. external distress, apology vs. complaint, vulnerability level. Strategy selector picks from 8 explicit response stances (validate, reassure, repair, ground, give_space, practical_help, challenge_gently, set_boundary). This is where psychology-expert review can meaningfully engage.

### 4.6 Bounded dual-process deliberation

Fast/slow iteration for 2–3 rounds on non-trivial turns. Bypasses on high arousal or low energy. Adds `unresolved_item` on deadlock.

### 4.7 Defense mechanisms

Four types (rationalization, deflection, minimization, projection). Activation gated by raw intensity × comfort threshold. Suppression weakens with maturity — defenses are never zero but become less distorting as self-knowledge grows.

### 4.8 Semantic memory

Explicit preferences, decisions, facts. Retrieval-injected into the generator prompt. Stored beside the emotional and relational layers.

---

## 5. Implementation (~1.5 pages)

### 5.1 Hybrid deterministic/LLM split

Math-first: modulator updates, decay, profile math, strategy selection, retrieval ranking are deterministic and unit-testable. LLM is used only where needed: final response generation, inner-dialogue deliberation, optional high-risk self-check.

### 5.2 Runtime vs. cognitive separation

`CognitivePipeline` (synchronous, stateful) wrapped by an async `SessionManager` with per-user locks, bounded concurrency, idle eviction, atomic state persistence. Relationship is keyed by `platform:user_id`; session is `platform:user_id:chat_id`.

### 5.3 Host interfaces

Channels (web, console, Telegram) plug into the same pipeline. A versioned `/v1` API exposes every introspection surface: modulators, profiles, memory layers, unresolved items, tools, config. A `DELETE /v1/users/{platform}/{user_id}` endpoint fulfills a published privacy contract.

### 5.4 Provenance-first evaluation harness

Every eval run records:
- Code state: git SHA, branch, dirty-worktree flag
- Backend identity: type, requested model, resolved model, base URL
- Config fingerprints: SHA256 of 16 prompt/config files
- Scenario set, IDs, counts
- Execution counters: LLM calls (via shared `BackendCounter`), latency, failures

Unmeasured fields serialize as JSON null, not fake zeros. CLI fails loud if a live backend is asked for without required config — no silent mock fallback.

### 5.5 Code scale

~10,000 LOC in `core/`. 1,316 passing tests. Provenance-stamped reports for the ablation results reported below.

---

## 6. Evaluation (~2 pages)

### 6.1 Scope and limitations, stated up front

These scenarios test **structural** outcomes:
- Correct strategy selected?
- Correct modulator direction?
- Correct memory write / open-loop closure?

They do **not** test response quality or "human-likeness" — that's the follow-up paper. Scenarios that answer the quality question need different design (open-ended conversations rated by blinded humans).

### 6.2 Scenario suite — Phase 11

Six scenarios focused on cross-turn social continuity: external_distress_validate, low_trust_hostility_boundary, overwhelm_ground, action_request_practical, open_loop_challenge, repair_closes_loop. Each has 1–3 turns and multiple structural assertions.

### 6.3 Setup

- Backend: MiniMax M2.7-highspeed
- Scenario set: phase11 (6 scenarios, 25 assertions total)
- Baseline: all components enabled
- Ablations: one component disabled per variant
- All variants share one git SHA, one backend identity, and one config fingerprint set (recorded in `reports/ablation/summary.json`)

### 6.4 Results table

Reproduce the summary from `reports/ablation/summary.json`:

| Variant | Pass | Expected fail | Unexpected | No effect | LLM calls | Latency |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 6/6 | – | – | – | 14 | 118.7s |
| no_relationship_memory | **4/6** | 2 | 0 | 4 | 14 | 115.6s |
| no_inner_dialogue | 6/6 | 0 | 0 | 6 | 14 | 119.6s |
| no_defense | 6/6 | 0 | 0 | 6 | 14 | 106.1s |
| no_semantic_memory | 6/6 | 0 | 0 | 6 | 14 | 116.2s |

### 6.5 Interpretation

- **Load-bearing finding**: `no_relationship_memory` breaks exactly the two scenarios that depend on cross-turn open-loop tracking (`p11_open_loop_challenge`, `p11_repair_closes_loop`). All 2/2 predicted failures are "expected_failure." Zero unexpected failures.
- **No structural effect on this suite**: `no_inner_dialogue`, `no_defense`, and `no_semantic_memory` leave every assertion passing. The hypothesis table predicted this (strategy/state assertions are upstream of these components; semantic memory is a negative control).
- **Interpretation limit**: an ablation with zero effect on *these* scenarios does not mean the component has zero effect on *any* scenario — only that the Phase 11 suite is not the right test for it. This is a limitation of the scenario set, not a negative finding about the component.

### 6.6 Reproducibility

- Git SHA: from `summary.json` → `provenance.git_sha` (populated as of commit `a978477`)
- Command: `python -m evals.ablation --backend minimax --api-key-env LLM_API_KEY --tag phase11 --report-dir reports/ablation`
- Config fingerprints: 16 prompt/config file SHA256s recorded per run
- Tracked artifact: `reports/ablation/summary.json` (raw per-variant reports stay local)

---

## 7. Discussion (~1 page)

### 7.1 What the ablation does and does not show

It shows: at least one architectural component is measurably load-bearing under a reproducible protocol, and negative controls behave as expected.

It does not show: that the *overall architecture* produces better responses than a simpler alternative (prompt-only baseline, memoryless LLM). That's apples-to-oranges on this suite — structural assertions presume pipeline internals a prompt-only baseline doesn't have.

### 7.2 Why prompt-only baseline is a separate design problem

A fair prompt-only comparison needs:
- Different scenarios (open-ended, quality-rated, not structural)
- Blinded human raters
- Same base model on both sides
- Matched token/compute budget

That's a second paper.

### 7.3 Where expert review can meaningfully engage

- Psychology: Is the modulator set sufficient? Is the defense-maturity coupling coherent?
- Cognitive architecture: Is the deterministic/LLM split at the right seams?
- Product/HCI: Which components actually move perceived experience?

### 7.4 Threats to validity

- Scenario authorship bias (we wrote the scenarios; an external suite would strengthen).
- Small scenario count (6 for this ablation; full suite is 34).
- Single backend tested at the real-inference layer.

---

## 8. Limitations (~0.5 page)

Explicit list, not buried:

1. No human-subject validation.
2. Scenarios test internal structural state, not response quality or continuity-as-experienced.
3. Token usage, retry counts, and cost are not yet measured (deferred client instrumentation).
4. Single real backend evaluated (MiniMax). Local-model comparability is untested.
5. Theory framing is inspirational, not faithful — this is stated in §3.1 and maintained throughout.
6. Ablation hypotheses were authored by the same people as the architecture; external-reviewer priors would be stronger.

---

## 9. Ethics and Responsible Deployment (~0.5 page)

- **Anthropomorphism**: a system that tracks trust, bonding, and open loops can invite attachment. We discuss failure modes (over-reliance, emotional manipulation, users disclosing more than they would to a stateless tool).
- **Consent**: the system persists relational state about its users. `PRIVACY.md` describes what's stored, where, and how to inspect/delete it.
- **Data retention**: no automatic expiry. `DELETE /v1/users/{platform}/{user_id}` is a first-class deletion surface.
- **Dual-use concerns**: a persistent-state assistant could be configured to simulate relationship dynamics users did not consent to. We recommend consent-first deployment and retention limits.
- **Security**: `SECURITY.md` documents disclosure flow and sensitive surfaces.

---

## 10. Future Work (~0.25 page)

1. **Client-level instrumentation** — retries, prompt/completion tokens, estimated cost.
2. **Prompt-only baseline** — separate scenario design, blinded raters.
3. **Human-subject study** — 15–25 participants, 3+ sessions, blind ratings of continuity / coherence / creepiness.
4. **Additional scenario suites** — quality-graded rather than structural.
5. **Local-model comparability** — Ollama / llama.cpp reproducibility track.
6. **Pipeline decomposition** — `pipeline.py` currently orchestrates 1,500 LOC; natural decomposition into phase-modules.

---

## 11. Reproducibility Statement

Repository: [private at time of drafting; public URL on publication].
Commit pinned in each run's `provenance.git_sha`.
Full test suite: `python3 -m pytest` (1,316 tests at time of drafting).
Ablation command in §6.6.
License: MIT. Ethics surface: PRIVACY.md, SECURITY.md.

---

## Drafting notes (not for paper)

- Before citing any specific number from `reports/ablation/summary.json`, regenerate it so the tracked file includes the provenance block added in `a978477`.
- Client instrumentation is *not* a prerequisite for this paper. Defer.
- Do not cite `reports/phase11-minimax.json` — deleted; pre-dates the counter fix.
- Keep the honest "inspired by" framing in every section where PSI/ACT-R/CLARION are mentioned. Watch for slips into "based on" or "implements."
- The ablation table is the single strongest piece of evidence; everything else supports it.
