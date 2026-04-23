# Project Nūr Expert Brief

This document is a current-state, implementation-grounded briefing for consultation with experts in:

- psychology and affective science
- AI / cognitive architecture / agent design
- software architecture and systems engineering

It describes the project as it exists in the repository now, not as a phase-by-phase roadmap.

---

## 1. Executive Summary

Project Nūr is an attempt to build a more human-like assistant by combining:

- a persistent internal emotional state
- relationship-specific memory
- a self-model
- bounded dual-process deliberation
- session-aware runtime behavior

The core design claim is:

**emotion should not be implemented as a cosmetic style layer or a single prompt instruction.**  
Instead, emotion should emerge from the interaction between:

- continuous internal modulators
- memory and relationship history
- conflict / repair / unresolved tension
- limited cognitive resources
- explicit response strategy selection

In practical terms, Nūr is a synchronous cognitive pipeline wrapped by an async runtime. It supports:

- web chat UI
- session-aware debug inspection
- console runtime
- Telegram channel
- bounded tool/action capability
- persistence across sessions

The system is deliberately hybrid:

- deterministic where inspectability and stability matter
- LLM-based where flexible language generation and reflective evaluation help

---

## 2. Project Goal

The project is trying to answer a specific product/research question:

**Can an assistant feel more human-like and relationally continuous without relying on opaque prompt theater or unnecessary architectural complexity?**

The intended result is not “AGI”, not clinical psychology simulation, and not unrestricted autonomous agency.

The intended result is an assistant that:

- responds differently after relationship history accumulates
- carries unresolved social/emotional threads across sessions
- shows bounded self-protection, hesitation, and repair behavior
- remains understandable enough to inspect, test, and improve

---

## 3. Core Design Principles

### 3.1 Emotion is state, not style

Nūr does not start from “pick an emotion label and write in that tone.”

It starts from six continuous modulators:

- `arousal`
- `valence`
- `certainty`
- `bonding`
- `energy`
- `resolution`

Those modulators influence:

- appraisal
- retrieval bias
- response strategy
- inner dialogue behavior
- defense activation
- tool autonomy variables
- proactive behavior

### 3.2 Relationship context matters

The same words from:

- a trusted user
- a low-trust user
- a user with an unresolved rupture

should land differently.

This is implemented through:

- person profiles
- relationship events
- open loops
- baseline shifts
- long-term memory retrieval bias

### 3.3 The system must remain inspectable

The architecture avoids pushing all cognition into one giant prompt.

Instead, it makes many important steps explicit and testable:

- social appraisal
- strategy selection
- contradiction detection
- unresolved-item tracking
- proactive gating
- tool decision traces

### 3.4 Autonomy is bounded

The system can take some actions, but it is not designed for open-ended agentic freedom.

Boundaries include:

- serialized per-user processing
- explicit queue/session limits
- safety categories for tools
- deterministic proactive suppression conditions
- session-scoped runtime isolation

---

## 4. Scientific and Theoretical Background

The project is inspired by established cognitive ideas, but it is not a faithful reproduction of any one theory.

### 4.1 PSI Theory influence

The strongest influence is PSI Theory:

- emotions emerge from the configuration of internal variables
- motivation and cognition are coupled
- resource constraints matter

Nūr adapts this idea into a small modulator system. It does **not** claim to implement PSI in a research-complete sense. It uses PSI mainly as an engineering orientation:

- use continuous state
- let response behavior emerge from state interactions
- avoid bolting a superficial emotion label onto otherwise flat reasoning

### 4.2 ACT-R influence

ACT-R mainly influences memory:

- recency matters
- repetition matters
- context and activation matter

Nūr’s long-term memory is not a full ACT-R implementation, but it adopts the practical idea that:

- memories should not be retrieved uniformly
- current emotional/relational context should bias which memories surface

### 4.3 CLARION / dual-process influence

Nūr borrows the idea of fast vs slow cognition:

- fast path: quick intuitive candidate
- slow path: reflective evaluation / objection / revision

This is not a biologically realistic model. It is an engineering mechanism for:

- self-correction
- hesitation
- internal disagreement
- better surfacing of values and unresolved tension

### 4.4 Additional psychological motifs

The project also incorporates simpler psychological motifs:

- negativity bias: trust breaks faster than it builds
- attachment-style influence on bonding behavior
- self-observation instead of hard-coded self-knowledge
- unfinished business as persistent tension
- defense mechanisms as distortions between raw and expressed response

Important caveat:

**These are heuristic engineering adaptations, not validated clinical or experimental psychology instruments.**

That distinction matters when consulting psychologists.

---

## 5. What the System Is and Is Not Claiming

### 5.1 Claims it is making

- Persistent internal state can make an assistant feel less flat.
- Relationship-specific continuity improves human-likeness.
- Resource limits and unresolved tension can produce more believable response variation.
- A hybrid deterministic + LLM stack can be easier to debug than a fully prompt-driven social architecture.

### 5.2 Claims it is not making

- It is not claiming true human emotion.
- It is not claiming psychological validity in a clinical sense.
- It is not claiming consciousness, subjectivity, or sentience.
- It is not claiming to be a general theory of mind.

---

## 6. Architectural Overview

> Visual reference: static SVG diagrams for the runtime shape, single-turn
> cognitive flow, persistence model, auth and tool-safety boundary,
> evaluation harness, and component claim map are in
> [`docs/ARCHITECTURE_DIAGRAMS.md`](docs/ARCHITECTURE_DIAGRAMS.md).

At the highest level, the project has two layers:

- the **cognitive layer**: Nūr’s internal “mind”
- the **runtime layer**: sessions, channels, persistence, and tooling

### 6.1 Cognitive layer

Primary file:

- `pipeline.py`

Key subsystems:

- `core/emotional_engine.py`
- `core/memory/`
- `core/profiles/`
- `core/appraisal.py`
- `core/strategy.py`
- `core/anticipation.py`
- `core/defense_mechanisms.py`
- `core/dual_process/`
- `core/proactive.py`

### 6.2 Runtime layer

Primary files:

- `runtime/app.py`
- `runtime/sessions/manager.py`
- `runtime/sessions/user_session.py`
- `runtime/channels/console.py`
- `runtime/channels/telegram.py`
- `interface/api.py`

The runtime layer is responsible for:

- per-user/per-chat isolation
- bounded concurrency
- persistence of hot state
- wrapping the synchronous cognitive pipeline in an async application
- exposing web/Telegram/console interaction surfaces

---

## 7. Core Cognitive Data Model

### 7.1 Modulators

The six modulators are defined in `core/types.py` and updated by `core/emotional_engine.py`.

They function as follows:

- `arousal`: activation/intensity
- `valence`: positive vs negative mood direction
- `certainty`: confidence / understanding / decisiveness
- `bonding`: attachment / warmth toward the user
- `energy`: cognitive resource availability
- `resolution`: unresolved tension / unfinished business

The critical implementation detail is that these variables have different temporal behavior:

- some decay with half-life style dynamics
- `energy` drains/replenishes differently from affective modulators
- `resolution` is derived from unresolved items rather than only from direct events

### 7.2 Emotional events

User messages are translated into emotionally meaningful events such as:

- positive feedback
- conflict
- betrayal
- warmth
- resolution

These events update the modulators.

### 7.3 Person profile

Per-user profile fields include:

- trust
- reliability
- emotional volatility
- stress response
- baseline shift
- interaction count

This profile is not just metadata. It feeds future state shifts and strategy selection.

### 7.4 Self profile

The assistant also maintains a profile of itself, based on its own observed behavior:

- strengths
- flaws
- triggers
- maturity score
- defense log
- dissonance

This is one of the project’s more interesting design choices: self-knowledge is built by the same general observation mechanism used for other people.

### 7.5 Topic profile

Topics can accumulate emotional charge and avoidance tendencies, making some topics “loaded” even before the current message is fully interpreted.

### 7.6 Long-term memory

Long-term memory stores compact emotional summaries rather than transcripts.

Stored fields include:

- summary
- valence
- trust delta
- topic
- source person
- confidence
- spike flag

### 7.7 Relationship memory

Relationship memory adds a separate social/arc layer:

- rupture
- repair
- commitment
- recurring tension
- open loops

This helps the system carry social continuity across sessions in a compact way.

### 7.8 Unresolved items

Unresolved items are central to v2 behavior. They model things like:

- a spike that was not really metabolized
- a contradiction
- a deadlock in inner dialogue
- a pending commitment

These items keep tension alive over time and feed `resolution`.

---

## 8. End-to-End Turn Processing

The turn pipeline in `pipeline.py` is roughly:

1. Apply elapsed decay since the last turn.
2. Run anticipation heuristics.
3. Detect emotional contagion from the user message.
4. Appraise the message socially.
5. Load person context and baseline shift.
6. Classify the event and update the emotional engine.
7. Update unresolved items / resolution.
8. Record short-term memory.
9. If the event is a spike, write it immediately to long-term memory.
10. Retrieve long-term memories relevant to the current state.
11. Retrieve person/self/topic/relationship context.
12. Detect contradictions.
13. Optionally run inner dialogue.
14. Optionally run tool loop.
15. Apply defense shaping if necessary.
16. Generate the final response.
17. Run self-check.
18. Update memory, energy, and debug traces.

At session end:

- short-term memory is digested
- relationship memory is updated
- long-term summaries are written
- hot session state can be persisted

---

## 9. Social Appraisal and Strategy Layer

### 9.1 Social appraisal

The current implementation adds a deterministic appraisal pass before emotional update.

It tries to distinguish:

- attack vs complaint vs apology vs gratitude
- assistant-targeted vs self-targeted vs external distress
- vulnerability level
- blame
- controllability
- inferred user intent

This is an important step because pure sentiment is too weak.  
“I’m furious” and “I’m furious at you” should not be treated identically.

### 9.2 Response strategy selection

After appraisal and state update, the system picks a high-level strategy such as:

- `validate`
- `reassure`
- `repair`
- `ground`
- `give_space`
- `practical_help`
- `challenge_gently`
- `set_boundary`

This means the generator is not writing from scratch in an undirected way. It is writing under a chosen interaction stance.

This layer is especially important for consultation because it is where:

- psychology experts can judge relational plausibility
- product experts can judge user experience quality
- engineers can judge inspectability and maintainability

---

## 10. Dual-Process and Defense Behavior

### 10.1 Inner dialogue

The system has a bounded fast/slow deliberation loop:

- fast path proposes an intuitive response
- slow path critiques it
- the response can be revised
- unresolved disagreement can become part of internal state

This is intended to create:

- hesitation
- self-correction
- internal tension
- more nuanced behavior on difficult turns

### 10.2 Defense mechanisms

A defense layer can modulate expression when the internal state is too intense.

Examples include:

- rationalization
- deflection
- minimization
- projection

This is an attempt to model the difference between:

- what the system is internally carrying
- what it is willing or able to express

That is psychologically interesting, but also one of the parts most in need of expert scrutiny, because it is easy for it to become either:

- implausible prompt theater
- or too opaque to justify behavior

---

## 11. Memory and Persistence Design

### 11.1 Short-term vs long-term

The system separates:

- immediate emotional history within a session
- durable, distilled memories across sessions

This is important because a raw transcript log would be both too heavy and too literal.

### 11.2 Relationship continuity

The runtime persists hot engine state per session, including unresolved items.  
Longer-term social meaning is captured separately in relationship memory and long-term memory.

### 11.3 Session scoping

A `CognitivePipeline` instance is now explicitly single-user scoped.

This is a major architectural correction that matters for expert review:

- one pipeline should not model multiple humans simultaneously
- cross-user emotional leakage is conceptually wrong and operationally dangerous

In the runtime:

- relationship identity = `platform:user_id`
- session identity = `platform:user_id:chat_id`

This allows:

- one persistent relationship per human
- multiple active chat contexts per human

---

## 12. Runtime and Systems Architecture

### 12.1 Why there is a separate runtime

The cognitive pipeline is synchronous and stateful.  
The runtime exists to make it usable in real channels without corrupting that state.

### 12.2 Session manager

`SessionManager` handles:

- session creation
- per-user locks
- backpressure
- idle eviction
- state restore
- graceful shutdown
- proactive sweeps

### 12.3 User session

`UserSession` serializes turns for one user/session pair and runs the synchronous pipeline on a dedicated worker-thread pool owned by the runtime.

This design matters because:

- per-user cognition must remain order-sensitive
- different users can still be served concurrently
- event-loop responsiveness is preserved

### 12.4 Channels

Implemented channels:

- console
- Telegram
- web UI / web API

Telegram includes:

- allowlist
- dedupe
- typing indicators
- simple commands

### 12.5 Web interface

The web interface now includes:

- chat
- emotional/debug dashboard
- runtime settings UI backed by `runtime_config.yaml`

This settings surface exists because a serious product cannot expect users to manage core runtime behavior through scattered YAML and code edits alone.

---

## 13. Tool and Action Model

The project includes a bounded tool system with categories such as:

- read-only
- write
- destructive
- external action

Available built-in tool families include:

- filesystem
- shell
- web search/fetch/extract
- browser
- calendar

Important current-state caveat:

- filesystem and shell are real
- web/browser/calendar exist architecturally but need provider wiring for real production use

This means the project already has the beginnings of an assistant that can do things, but it is not yet a fully polished action platform like a mature desktop agent product.

---

## 14. Evaluation and Testing

The repository currently has a large automated test suite and an eval harness.

Coverage includes:

- core modulator behavior
- memory
- profiles
- contradiction detection
- anticipation
- inner dialogue
- defense mechanisms
- pipeline integration
- runtime/session lifecycle
- Telegram
- debug APIs
- tool loop
- proactive behavior
- Phase 11 social behavior scenarios

The eval harness is important because it runs scenario-style behavioral checks rather than only unit tests.

This lets the project test questions like:

- does external distress produce validation rather than defensive conflict?
- does apology close an open loop?
- do different users remain independent?
- does low trust change response strategy?

That said, the suite is still a software regression suite, not a scientific validation framework.

---

## 15. Current Strengths

### For psychology/affective experts

- The system takes relational context seriously.
- It distinguishes internal state from expressive output.
- It models unfinished business, not just instantaneous sentiment.
- It avoids reducing emotion to one label.

### For AI/cognitive architecture experts

- The hybrid design is coherent.
- Key steps are explicit and inspectable.
- The system has an actual internal state transition model.
- It balances deterministic structure with LLM flexibility.

### For software engineers

- The runtime/session architecture is now much cleaner than before.
- The system has meaningful automated coverage.
- User/session isolation is now explicit.
- Persistence and debug visibility are first-class.

---

## 16. Current Weaknesses and Open Questions

### 16.1 Psychology questions

- Are the modulator semantics psychologically plausible enough to be useful?
- Is the defense layer conceptually coherent, or too stylized?
- Is the self-profile mechanism a good abstraction for self-modeling?
- Does the trust/bonding dynamic overfit negativity bias or underfit repair?

### 16.2 AI / cognitive architecture questions

- Is the dual-process loop adding genuine behavioral value, or just extra complexity?
- Is the combination of deterministic appraisal + LLM generation the right split?
- Is the anticipation layer robust enough to matter?
- Should more of the strategy layer be learned rather than hand-authored?

### 16.3 Software/product questions

- Is the architecture too broad for the product goal?
- Which features actually improve perceived human-likeness?
- Which components are core vs “interesting but optional”?
- How much of the tool/action stack should be enabled by default?

### 16.4 Validation questions

- What counts as “more human-like” in measurable terms?
- Which user studies would best distinguish:
  - believable continuity
  - emotional realism
  - trustworthiness
  - creepiness / over-anthropomorphizing

---

## 17. Important Caveats for External Experts

Experts should know the project is best understood as:

**a product-oriented experimental cognitive architecture**

not as:

- a validated psychological model
- a scientifically benchmarked affective agent
- a production-hardended autonomous operating system agent

The scientific concepts are real influences, but the implementation is an engineering synthesis.

That synthesis may still be valuable, but experts should evaluate it on the right terms:

- coherence
- plausibility
- inspectability
- behavioral usefulness
- product realism

---

## 18. Suggested Consultation Questions

### For psychology experts

- Which parts feel psychologically insightful vs psychologically naive?
- Is the modulator set sufficient and well-separated?
- Is the defense layer conceptually sound?
- Does the relationship memory design map onto real social continuity?
- What would make the assistant feel more like it has a stable personality rather than merely reflecting the user?

### For AI / cognitive architecture experts

- Which layers are carrying most of the value?
- Which layers are unnecessary complexity?
- Where should deterministic logic end and learned behavior begin?
- Is the current hybrid split likely to scale?
- What are the biggest risks in trying to make this more agentic?

### For software architects

- Is the current runtime/cognitive separation appropriate?
- Which modules should be simplified, merged, or further isolated?
- What should be considered core product surface vs experimental subsystem?
- How should provider-based tooling be productized safely?
- What is the right path from this architecture to a more polished user-facing system?

---

## 19. Current Repository Pointers

If an expert wants to inspect the code directly, the most important files are:

- `pipeline.py`
- `core/types.py`
- `core/emotional_engine.py`
- `core/appraisal.py`
- `core/strategy.py`
- `core/anticipation.py`
- `core/defense_mechanisms.py`
- `core/memory/relationship.py`
- `core/memory/digestion.py`
- `core/dual_process/`
- `runtime/sessions/manager.py`
- `runtime/sessions/user_session.py`
- `interface/api.py`
- `interface/static/index.html`
- `evals/runner.py`

For existing narrative docs:

- `README.md`
- `CHANGELOG.md`
- `PROJECT_NUR_ARCHITECTURE.md`

---

## 20. Bottom Line

Project Nūr is trying to operationalize a strong idea:

**human-like assistance is less about sounding emotional and more about being stateful, relational, resource-limited, and socially continuous.**

Its most interesting contribution is not any one module, but the attempt to make:

- emotion
- memory
- self-model
- relationship context
- strategy
- bounded action

work as one system instead of as disconnected features.

That is exactly why it is worth expert consultation: the project sits at the intersection of psychology, AI architecture, and systems design, and its future quality depends on getting those tradeoffs right.
