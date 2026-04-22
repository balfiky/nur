# Project Nūr — Technical Note

> An inspectable hybrid architecture for AI assistants that treats emotion
> as persistent internal state rather than a prompt-level style layer.
> Written as a public technical note, not an academic submission.

---

## The problem

Conversational AI assistants produce fluent, topically relevant replies.
What they cannot produce is *texture* — the sense, evident in replies
between people who know each other, that a message has landed against a
particular history and a particular relationship.

Retrieval-augmented memory remembers *what* was said. It does not remember
*how it landed*: whether a previous conversation felt warm or sharp,
whether trust accumulated or broke, whether something was left unresolved.
The same words from different people, said at different moments in a
relationship, may retrieve different text, but the assistant's stance
toward the speaker does not shift. Accumulated history does not
detectably change behavior.

The standard engineering response is to layer a persona on top: tell the
language model to sound warm, or to maintain a specific voice. This
treats the symptom. Persona-in-prompt is a rendering of state the model
does not carry. Between turns the state evaporates; between sessions it
never existed. Nothing accumulates, nothing decays, nothing resolves or
fails to resolve.

## The thesis

**Emotion in an AI assistant should be treated as persistent internal
state, not a prompt-level style layer.**

Concretely: the system should maintain a continuous emotional state
vector whose updates and decays are deterministic and inspectable;
memory should be keyed not only by content but by how that content felt;
the assistant should profile itself the same way it profiles others; and
the pipeline's decision points — appraisal, strategy, deliberation,
defense — should be explicit enough to test individually.

---

## The architecture

Eight cognitive components, each individually inspectable and
ablatable. Inspired by PSI Theory, ACT-R, and CLARION, but not a
faithful implementation of any of them — the engineering stance
("continuous state, well-defined decay, emergent combinations,
memory weighted by recency/frequency/context, bounded fast/slow
deliberation") is what we take from the cognitive-science literature;
the theoretical content (drive systems, formal activation equations,
learned implicit/explicit coordination) is not.

### Six continuous modulators

The cognitive state is a six-dimensional vector in [0, 1]: arousal,
valence, certainty, bonding, energy, and resolution. Each modulator has
its own temporal behavior — arousal decays in roughly two minutes,
valence over tens of minutes, bonding over days; energy drains with use
and recovers with rest. State updates from several sources per turn:
bounded emotional contagion from the user's detected tone, a
person-specific baseline shift, event-driven impulses from a classified
emotional event, and time-based decay toward baseline.

### Dual memory

Short-term (in-process, cleared at session end) and long-term (SQLite,
distilled emotional summaries). Retrieval is weighted by recency,
frequency, and current context — an engineering adaptation of the ACT-R
retrieval idea. Writes are asymmetric: trust increments from positive
events are small, while decrements from negative events are 7.5× larger
at the current configuration (+0.02 vs −0.15). Events above an intensity
threshold bypass confidence filtering and write directly to long-term
storage — one serious betrayal can override a long history of small
positives.

### Unified self/other profiling

The system builds behavioral profiles for every salient entity it
encounters: each user it talks to, each topic that carries emotional
charge, and — distinctively — itself. All three use the same
`ProfileStore` mechanism, keyed by entity id. For users, the id is the
user identifier; for topics, the topic name; for the assistant, the
literal string `__self__`.

The self-profile stores observed traits (derived from behavioral
observations, not declared in config), strengths, flaws, triggers, a
maturity score, and a log of every defense activation. It is *earned*
through observation rather than asserted by the designer. This collapses
"how does the system understand the user?" and "how does the system
understand itself?" into one mechanism.

### Relationship memory

A layer distinct from factual and emotional memory: it stores the
structure of a social arc rather than the content of past interactions.
Four event kinds are recorded — rupture, repair, commitment, and
recurring tension. Alongside events, the layer tracks *open loops*:
unresolved threads that persist across sessions until closed by a
matching event.

On each turn, the layer injects a compact relationship context — a
summary, up to two active open loops, and up to two recent events —
into the generator's prompt.

### Social appraisal and response strategy

Before event classification, each user message passes through a
deterministic appraisal pass. Regex- and lexicon-based heuristics infer
whether distress is directed at the assistant or at the user's own life,
whether an utterance is apology or complaint, vulnerability level, and
mixed affect. A strategy selector then chooses from eight named response
stances: validate, reassure, repair, ground, give space, practical help,
challenge gently, set boundary.

### Bounded dual-process deliberation

A fast/slow negotiation loop runs on turns where heuristics suggest it
is worth the extra LLM calls. Up to three rounds of fast-path proposal
and slow-path critique negotiate a candidate. High arousal or low energy
bypasses the loop.

### Defense mechanisms

Four defense types can activate — rationalization, deflection,
minimization, projection — gated by a comfort threshold that rises with
trust and self-maturity. When active, a defense contributes a
suppression instruction that shapes the generator's output. Maturity
weakens suppression: more self-observation narrows the gap between felt
and expressed state.

### Semantic memory

A separate SQLite-backed store for explicit preferences, decisions,
episodes, and facts. Writes on detected preference statements;
retrievals scored by token overlap, per-user bias, recency.

---

## The evaluation

A provenance-stamped harness ablates one component at a time against a
behavioral scenario suite on a real LLM backend. Every run records git
SHA, backend identity, config fingerprints (SHA-256 of 16 prompt and
configuration files), scenario set, and execution counters.

**Important framing:** the scenario suite tests *structural* outcomes —
whether the right strategy was selected, whether modulators moved the
right direction, whether a memory record was written. It does **not**
test response quality or how the output reads to a human. Structural
assertions are well-suited to *architecture ablation* (which components
are load-bearing?), and poorly suited to *architecture comparison* (does
this feel more human than a simpler alternative?). A fair comparison to
a prompt-only baseline requires a different scenario design and is not
attempted here.

### Results — Phase 11 on MiniMax M2.7-highspeed

| Variant | Pass | Expected fail | Unexpected | No effect |
|---|---:|---:|---:|---:|
| baseline | 6/6 | – | – | – |
| no_relationship_memory | **4/6** | 2 | 0 | 4 |
| no_inner_dialogue | 6/6 | 0 | 0 | 6 |
| no_defense | 6/6 | 0 | 0 | 6 |
| no_semantic_memory | 6/6 | 0 | 0 | 6 |

Each scenario outcome under each ablation is labeled against a
hypothesis table authored before the run (`expected_failure`,
`unexpected_failure`, `no_effect`, `newly_passing`). Every prediction
held.

### What the table shows

- **Relationship memory is load-bearing** on this suite: disabling it
  breaks exactly the two scenarios that depend on cross-turn open-loop
  state (`p11_open_loop_challenge`, `p11_repair_closes_loop`), and no
  others. Zero unexpected failures.
- **Three other component ablations show no structural failures.** This
  is *not* a claim that inner dialogue, defense, or semantic memory are
  useless — it is a claim about this suite specifically: the structural
  assertions are upstream of those components. Inner dialogue's
  deliberation rounds don't move the strategy selector. Defense shapes
  wording, which Phase 11 doesn't grade. Semantic memory here is a
  negative control (no scenario exercises it) — zero effect is the
  expected result, and any failure would have indicated hidden coupling.

The honest summary: the architecture contains at least one component
whose structural contribution is measurable under reproducible
conditions, and the remaining components are **not falsifiable by this
suite** rather than shown inert.

---

## What this does *not* show

Stated up front so no reader has to infer it:

- No validated human-likeness claim. We do not show that Nūr's responses
  read as more human or more emotionally coherent to people. That
  requires a blinded user study with matched baselines — explicitly
  deferred future work.
- No prompt-only baseline comparison. These scenarios test pipeline
  internals; a prompt-only system has no internals to assert on, so the
  comparison is apples-to-oranges. A fair comparison needs different
  scenarios, blinded human raters, matched token budget, and matched
  memory affordance — a separate experimental program.
- No faithful PSI / ACT-R / CLARION implementation. No drive system, no
  formal activation equation, no learned dual-process coordination.
  Engineering inspiration, not theoretical reproduction.
- No provider-level telemetry (tokens, retries, cost). The eval reports
  serialize those fields as JSON `null` because the current LLM clients
  do not extract them from responses. Capturing them is a straightforward
  follow-on that has not yet been done.
- No multi-party conversation support. Single-user scoping is
  architectural, not incidental.

---

## Privacy and deletion

The system persists relational state about users. `PRIVACY.md`
documents exactly what is stored, where, and how to inspect or remove
it. A dedicated `DELETE /v1/users/{platform}/{user_id}` endpoint wipes
per-user data in one call: evicts live sessions, removes the per-user
SQLite database and all session JSON, and by design does not touch the
shared self-model. Best-effort row counts are returned.

Deployers of this system to anyone other than themselves are
responsible for obtaining informed consent. Persistent relational state
can invite attachment beyond what is appropriate for an assistant.

---

## How to try it

```bash
git clone https://github.com/balfiky/nur.git
cd nur
python3 -m pip install -e ".[dev]"

# Console chat (mock backend — no API key needed)
python3 main.py

# Web UI at http://localhost:8000
nur-web

# Re-run the ablation protocol above
python3 -m evals.ablation \
    --backend minimax \
    --api-key-env MINIMAX_API_KEY \
    --tag phase11 \
    --report-dir reports/ablation
```

Full test suite: `python3 -m pytest` (1,319 tests).

---

## Further reading

- [`PAPER_DRAFT.md`](PAPER_DRAFT.md) — long-form 7,600-word version of
  this note with fuller background, related-work positioning, and
  section-by-section detail.
- [`PROJECT_NUR_ARCHITECTURE.md`](PROJECT_NUR_ARCHITECTURE.md) — the
  original architectural vision document.
- [`PROJECT_NUR_EXPERT_BRIEF.md`](PROJECT_NUR_EXPERT_BRIEF.md) — an
  earlier expert-facing briefing.
- [`PRIVACY.md`](PRIVACY.md), [`SECURITY.md`](SECURITY.md),
  [`CONTRIBUTING.md`](CONTRIBUTING.md) — release and deployment context.
- [`reports/ablation/summary.json`](reports/ablation/summary.json) —
  the tracked ablation artifact.

## Caveats

- The tracked `reports/ablation/summary.json` was produced before the
  compact provenance block was added to the summary format (the code
  path is in place since commit `a978477`). Pass/fail, LLM-call, and
  latency numbers are accurate for the run they document; regenerating
  the summary at a current commit will additionally include git SHA and
  backend identity inside the artifact itself.
- Live-backend pass/fail can drift over time as cloud providers change
  what a model alias resolves to. Cross-reference the `requested_model`
  and `resolved_model` fields in the per-variant report provenance
  before citing any specific number.

## License

MIT. See [LICENSE](LICENSE).
