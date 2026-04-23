# LinkedIn Post

> Draft copy — rewrite in your own voice before posting.
> The goal here is "engineer sharing what was built and what was learned,"
> not "paper abstract pasted into LinkedIn."

---

## Short version (~200 words — LinkedIn-native length)

In telecom and automation, I’m used to thinking in terms of explicit state, inspectable behavior, and measurable tradeoffs.

I’ve been applying the same mindset to AI assistants through an open-source project called **Project Nūr**.

The idea is simple: instead of treating emotion as prompt styling, treat it as persistent internal state.

The system keeps:
- six continuous emotional modulators
- a relationship memory that tracks ruptures, repairs, and open loops
- a self-model earned from observation rather than declared in config

The most useful result so far came from an ablation harness on a real LLM backend:

When **relationship memory** is disabled, the system fails exactly the two scenarios that depend on cross-turn social continuity, with no unexpected failures elsewhere.

That is the claim I’m comfortable making today: one component is clearly load-bearing on the current structural eval suite.

What I am **not** claiming:
- validated human-likeness
- a fair prompt-only comparison
- that the other components are "proven ineffective"

For the three zero-effect ablations, the honest reading is that they are **not falsifiable by the present suite yet**.

I’m sharing it as a research/engineering prototype because the architecture, ablation harness, privacy/deletion surface, and debugging hooks may be useful to others working on stateful AI systems.

Repo: https://github.com/balfiky/nur  
Technical note: https://github.com/balfiky/nur/blob/main/TECHNICAL_NOTE.md

---

## Longer version (~400 words — for a fuller LinkedIn article)

Most AI assistants today can remember facts.
Very few can remember whether a relationship is warm, tense, repaired, or unresolved.

That gap is what I’ve been exploring in **Project Nūr**.

My day job is large-scale network automation and operational systems, so I naturally approach AI the same way: explicit state, inspectable behavior, and measurable tradeoffs.

The core idea in this project is:
**emotion should be modeled as persistent internal state, not as prompt-level style.**

So instead of relying on one large system prompt, I built a cognitive layer with inspectable parts:

- six continuous modulators: arousal, valence, certainty, bonding, energy, resolution
- dual memory: short-term plus long-term emotional memory
- unified self/other profiling: the same mechanism profiles users, topics, and the assistant itself
- relationship memory: ruptures, repairs, commitments, and open loops tracked as their own layer
- deterministic social appraisal and explicit strategy selection
- bounded dual-process deliberation
- defense mechanisms that weaken as the self-model matures
- semantic memory for explicit facts and preferences

The architecture is **inspired by** PSI, ACT-R, and CLARION, but it is not a faithful implementation of any of them.

I also built a provenance-stamped ablation harness and ran it on a real LLM backend.

The clearest result is narrow, but useful:
when **relationship memory** is disabled, the system fails exactly the two scenarios that depend on cross-turn open-loop state, with no unexpected failures elsewhere.

That gives me one component that is clearly load-bearing on the current structural eval suite.

Three other ablations showed no structural failures. I’m being careful with that result. It does **not** mean those components do nothing. It means the current scenarios test structural pipeline behavior, not wording quality, hesitation, or emotional expression. Those components need a different evaluation design.

So this is not a product launch and not a "we solved human-like emotion" claim.
It is an inspectable engineering/research prototype with:

- one reproducible positive ablation result
- a clear privacy/deletion surface
- full test coverage for the architecture as implemented

What it does **not** show:
- validated human-likeness
- a fair prompt-only baseline comparison
- a faithful PSI / ACT-R / CLARION implementation

If you work on affective agents, HCI, cognitive architectures, AI infrastructure, or stateful LLM systems, I’d be interested in your criticism.

Repo: https://github.com/balfiky/nur  
Technical note: https://github.com/balfiky/nur/blob/main/TECHNICAL_NOTE.md
