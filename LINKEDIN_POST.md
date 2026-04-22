# LinkedIn Post

> Draft copy — adjust tone/length for your audience before posting.
> LinkedIn strips most formatting; plain paragraphs and simple
> bullets work best. Emoji optional.

---

## Short version (~200 words — LinkedIn-native length)

Most AI assistants remember what was said. Very few remember how it
landed.

I've been building **Project Nūr** — a hybrid cognitive architecture
that treats emotion as persistent internal state rather than a
prompt-level style layer. Instead of telling a language model to "sound
warm," the system maintains six continuous emotional modulators, a
relationship memory that tracks ruptures and repairs, and a self-model
earned through behavioral observation rather than declared in config.

The honest finding so far, from a provenance-stamped ablation protocol
on a real LLM backend:

→ Disabling relationship memory breaks exactly the two scenarios that
test cross-turn open-loop state, and no others. Three other
architectural components leave the structural assertions unchanged — a
signal about the current evaluation suite, not a claim that those
components are useless.

What this does *not* show: validated human-likeness. That needs a
blinded user study, and it's the next experiment, not this one.

Open-sourced under MIT, fully reproducible ablation protocol, test
suite at 1,319. Framed as a research prototype, not a product.

Repo: https://github.com/balfiky/nur
Technical note: [TECHNICAL_NOTE.md]

---

## Longer version (~400 words — for a fuller LinkedIn article)

### The problem

Conversational assistants today produce fluent, topically relevant
replies. What they cannot produce is *texture* — the sense, evident in
replies between people who know each other, that a message has landed
against a particular history.

Retrieval-augmented memory remembers *what* was said. It does not
remember *how it landed* — whether a conversation felt warm or sharp,
whether trust accumulated or broke, whether something was left
unresolved. The standard fix is to layer a persona in the prompt. That
treats the symptom. Between turns the state evaporates.

### What I built

**Project Nūr** treats emotion as persistent internal state rather than
prompt-level style. The architecture has eight inspectable components:

- **Six continuous modulators** — arousal, valence, certainty, bonding,
  energy, resolution — each with its own decay dynamics
- **Dual memory** (short-term + long-term), with an asymmetric
  negativity bias (trust breaks 7.5× faster than it builds)
- **Unified self/other profiling** — the assistant profiles itself
  through the same mechanism it uses for users, with self-knowledge
  earned from behavior rather than declared in config
- **Relationship memory** — ruptures, repairs, open loops tracked as a
  separate layer
- **Deterministic social appraisal + explicit strategy selection**
- **Bounded dual-process deliberation**
- **Defense mechanisms that weaken with self-awareness**
- **Semantic memory** for preferences and facts

Inspired by PSI, ACT-R, and CLARION — engineering inspiration, not
faithful implementation.

### What the evaluation shows

A provenance-stamped ablation harness runs against a real LLM
(MiniMax-M2.7-highspeed). Disabling relationship memory breaks exactly
the two scenarios that test cross-turn open-loop state — the headline
positive finding. Three other ablations leave structural assertions
unchanged, which is a limit of the current scenario suite (they test
internals, not wording) rather than a claim that those components are
useless.

### What it does *not* show

Not a validated human-likeness claim. That needs a blinded user study,
and it's explicitly deferred. Not a prompt-only baseline comparison —
apples-to-oranges on these scenarios. Not a faithful PSI/ACT-R/CLARION
implementation.

### Why share now

Because the honest framing — *an inspectable prototype with one
reproducible load-bearing result* — is useful even without the user
study, and because the plumbing (provenance stamping, ablation
protocol, feature toggles, deletion endpoint for user data) is
reusable for other affective-agent projects.

MIT licensed. 1,319 tests. Privacy policy and deletion endpoint ship
with the repo.

→ Repo: https://github.com/balfiky/nur
→ Technical note: TECHNICAL_NOTE.md
→ Open to feedback from the affective-computing, HCI, and cognitive
  architecture communities.
