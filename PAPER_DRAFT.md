# Paper Draft

This file accumulates prose drafts as outline sections become stable.
`PAPER_OUTLINE.md` remains the navigable skeleton; this file is the
running text. Sections are drafted out of order — §6 first to lock the
scope language before the rest of the paper leans on it.

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
