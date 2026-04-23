# Project Nūr Second-Pass Review

Date: 2026-04-01

This is a second pass done in three separate lenses:

1. The idea itself
2. The design and architecture
3. The actual implementation

Then a fourth section pulls them back together into one judgment.

I read the repository runtime, config, prompts, tests, and architecture docs. I also ran targeted local repros for suspected failures. I could not run the full test suite because `pytest` is not installed in this environment.

## Executive Summary

The project idea is strong.

The architecture is directionally right but underspecified in a few critical ownership and control boundaries.

The codebase is not just a rough prototype; it has real structure and a meaningful test surface. But the current implementation does not fully realize its own architecture. The biggest problem is not sloppy code. The biggest problem is that several headline v2 systems are present, visible in debug output, and only partially connected to the path that determines behavior.

The current system is best described as:

- a solid emotional-state experiment
- a decent memory/profile scaffold
- a partially realized cognitive architecture
- not yet a fully integrated inner-life system

The strongest parts are the continuous modulator framing, the unified self/other profiling idea, and the insistence on math-first state transitions.

The weakest parts are state ownership, response-path integration, self-model persistence, and the mismatch between what the architecture claims and what the runtime actually uses.

## 1. Idea Pass

### Verdict

As an idea, this is good. More than good, actually. It is one of the more coherent personal-AI emotion architectures I have seen because it tries to produce behavior from persistent internal variables instead of bolting on emotion labels.

The concept is worth pursuing.

### What Is Strong About the Idea

#### 1. Emotions as modulator geometry instead of labels

This is the right direction. Discrete emotion labels are brittle and flatten blended states. A small vector of persistent modulators is a much better control surface for an AI system that is meant to feel textured over time.

The six chosen dimensions are also mostly defensible:

- `arousal` gives activation/urgency
- `valence` gives affective sign
- `certainty` gives confidence/anxiety
- `bonding` gives relationship coloration
- `energy` gives fatigue and cognitive capacity
- `resolution` gives lingering unfinished tension

That is a useful emotional basis.

#### 2. Memory is treated as emotional memory, not just factual recall

This is essential. Most assistant-memory systems remember facts and miss relational residue. The idea of storing emotionally biased memory traces and retrieving them based on current state is exactly the kind of mechanism that can make repeated interaction feel cumulative.

#### 3. Self and others sharing one profiling mechanism

This is probably the single best conceptual decision in the project.

If the system profiles others through observation and profiles itself through the same observational machinery, the self-model has a chance of being earned rather than declared. That is much stronger than hardcoding “personality traits.”

#### 4. Resolution is a good v2 addition

Adding unresolved tension as a persistent dimension is smart. It solves a real gap left by standard valence/arousal systems. Many human-feeling states are not just “positive” or “negative”; they are about open loops.

#### 5. “Pure math where possible, LLM only where necessary”

This is the correct instinct. Affective state transitions, decay, trust curves, and retrieval ranking should not be delegated to an LLM if you want inspectability and calibration.

### Where the Idea Is Weak or Conceptually Muddy

#### 1. The theory blend is more inspirational than formal

The project invokes PSI, ACT-R, and CLARION, but the implementation is not really a faithful hybrid cognitive architecture in the academic sense. It is a software architecture inspired by those theories.

That is fine.

What is not fine is overstating the fidelity. Right now:

- PSI is mostly “continuous modulators with decay”
- ACT-R is mostly “activation-like ranking with emotional bias”
- CLARION is mostly “fast/slow prompt choreography”

The idea remains valid, but the theory claims should stay modest.

#### 2. Some psychological constructs are overloaded

A few dimensions are doing too much:

- `certainty` is carrying confidence, ambiguity, anxiety, and cognitive clarity
- `bonding` is implicitly related to trust, attachment, social openness, and contagion strength
- `resolution` is carrying contradictions, commitments, deadlocks, topic avoidance, and emotional residue

That does not make the idea wrong, but it means the system will eventually hit interpretation collisions unless a few constructs are separated more cleanly.

#### 3. The model lacks a clear social-safety or dominance dimension

This matters because a lot of conversational tone is not explained by valence, arousal, certainty, and bonding alone. Shame, guardedness, intimidation, deference, and assertiveness are hard to model if “certainty” and “bonding” are doing all the work.

You may not need another modulator now, but you should at least be aware of the missing axis.

#### 4. The self-model is central to the concept, not optional

This is important enough to say bluntly: if self-observation, pattern accumulation, maturity, and defense-history persistence do not work, then a large fraction of the project’s conceptual promise disappears.

The self-model is not a garnish in this architecture. It is load-bearing.

#### 5. Global emotional carryover across users needs an explicit philosophical decision

One agent having one emotional state across all relationships can be conceptually valid.

But then you are modeling a single psyche interacting with multiple people, not a clean per-user companion.

That is a big design choice and should be explicit, because it changes how context switching, recovery, bonding, and fairness of interaction are interpreted.

### Concept-Level Recommendation

Keep the core concept. Do not simplify it into labels or “moods.”

But tighten the conceptual statement to this:

> Project Nūr is an affective-cognitive architecture inspired by PSI, ACT-R, and CLARION, not a literal implementation of those theories.

That framing is stronger and more honest.

## 2. Design and Architecture Pass

### Verdict

The module split mostly makes sense.

The project is not architecturally chaotic. The top-level decomposition into `core`, `memory`, `profiles`, `dual_process`, `config`, `interface`, and `pipeline` is sensible.

The problem is not broad module separation.

The problem is that the control boundaries and state ownership are not sharp enough, so the intended architecture leaks or dead-ends at the integration points.

### What the Architecture Gets Right

#### 1. The repository shape is logical

The main folders are where they should be:

- `core/emotional_engine.py`
- `core/memory/*`
- `core/profiles/*`
- `core/dual_process/*`
- `pipeline.py`
- `config/*`
- `interface/*`

That is a good basis.

#### 2. Shared types are centralized

Keeping cross-module contracts in `core/types.py` is the right move. This reduces accidental schema drift and makes the system easier to reason about.

#### 3. The intended processing order is mostly correct

At the design level, this ordering is defensible:

- anticipate
- detect incoming tone
- apply relationship context
- process event
- retrieve memory based on resulting state
- deliberate
- defend/filter
- generate response

That is a sensible flow.

#### 4. Math-heavy modules are mostly isolated from LLM-facing modules

That separation is good. The engine, memory math, and profile math do not need to know about prompt structure.

### Where the Architecture Breaks Down

#### 1. `pipeline.py` is a god orchestrator

`pipeline.py` currently owns too much:

- emotional sequencing
- retrieval strategy
- self/profile lookup
- contradiction handling
- dialogue invocation
- defense prompt injection
- conversation history
- trust updates
- session digestion

That is workable for v1, but now that v2 exists it is too much responsibility in one place.

The result is that cross-cutting bugs become easy to introduce and hard to see.

#### 2. State ownership is unclear

Different subsystems own different fragments of “who the agent is,” but the boundaries are muddy:

- the engine owns unresolved items
- the self-profile manager owns persistent observations
- the defense mechanism mutates an in-memory `SelfProfile`
- the pipeline owns conversation history and decides when trust changes

This is why defense logging can appear in one turn and vanish on the next: the design does not define what is authoritative.

#### 3. The final response path is architecturally inconsistent

The intended design says:

- inner dialogue produces a candidate
- defense filters that candidate
- master generator finalizes it

But the actual generator contract does not accept a candidate response as input. So the architecture says “pipeline of transformations,” while the code executes “parallel reasoning trace + unrelated final generation.”

That is the most damaging design break in the repo.

#### 4. Baseline/context semantics are underspecified

The architecture talks about person-specific baseline shift and context switching, but the runtime API behaves like additive state mutation.

Those are different designs:

- “switch baseline” means conversational mode selection
- “add shift each turn” means state ratchet

You need one model, not both.

#### 5. Prompt/config loading is split across two systems

Most prompt loading goes through `config.loader`, but `inner_dialogue.py` reads prompt files directly. That breaks configuration coherence and undermines custom-config behavior.

#### 6. The web/API architecture conflicts with the persistence story

The architecture says persistence is sacred.

The interface boots a global pipeline with `db_path=":memory:"`, which throws persistence away by default. That is a design-level contradiction, not just an implementation detail.

#### 7. Missing designed layers matter

The architecture document makes `meta_cognition` and `self_reflection` central, but those modules do not exist in the actual codebase. That leaves a hole between “dynamic self-knowledge” as described and “static extraction from observed traits” as implemented.

### Architecture Recommendations

#### 1. Introduce a `TurnState` / `TurnContext`

A single object should flow through the turn:

- input message
- detected emotion
- pre-shift state
- event
- memory retrieval
- profiles
- contradiction signals
- dialogue candidate
- defense result
- final response

That would make the pipeline legible and reduce hidden coupling.

#### 2. Split persistent state into three layers

At minimum:

- `AgentState`: global emotional and long-term self state
- `RelationshipState`: person-specific trust/history/baseline
- `SessionState`: short-term conversation-local trace

Right now these are partially mixed.

#### 3. Make response generation explicitly candidate-driven

Either:

- the master generator receives `candidate_response` and refines it

or:

- inner dialogue writes guidance only and the generator owns the entire response

The current hybrid is architecturally incoherent.

#### 4. Move all prompt access behind config

No direct prompt reads inside modules.

#### 5. Decide whether cross-user affect bleed is intended

If yes, encode it and defend it.

If no, the architecture needs per-user/session engine state instead of one shared engine.

## 3. Implementation Pass

### Verdict

The codebase has real substance, but there are several implementation failures that materially change behavior.

Some are normal prototype issues.

Some are severe enough that they invalidate key v2 claims.

### Highest-Severity Confirmed Issues

#### 1. Inner dialogue and defense do not meaningfully control the final response

Confirmed in code and via local repro.

In `pipeline.py`, the pipeline computes:

- `dialogue_trace.final_candidate`
- `filtered_output`

but then calls the generator without passing either as a response candidate:

- `pipeline.py:321-354`

The generator prompt template also has no placeholder for the candidate text:

- `core/dual_process/generator.py:50-80`
- `config/prompts/generator.md:1-27`

Result:

- inner dialogue is mostly not steering the answer
- defense output is mostly not steering the answer
- the “inner life” is largely detached from the actual emitted response

This is the single biggest implementation problem in the repo.

#### 2. No automatic temporal decay during normal processing

`EmotionalEngine.decay()` exists:

- `core/emotional_engine.py:133-156`

But the main turn pipeline never computes elapsed time and never calls it:

- `pipeline.py:188-414`

Only explicit rest calls decay/recover the system:

- `pipeline.py:478-481`

So the half-life math is mostly inactive in real use unless the caller manually simulates rest.

#### 3. Context shifts compound every message

Each turn does:

- fetch person shift
- add it directly to current state

Code:

- `pipeline.py:223-226`
- `core/emotional_engine.py:158-162`

That means a persistent shift becomes a repeated additive bias. I confirmed this locally: repeated messages with an `arousal=+0.1` baseline shift keep ratcheting arousal upward.

That is not context switching. That is drift.

#### 4. The self-model does not actually learn from live use

The self-model manager can record behavior:

- `core/profiles/self_model.py:42-59`

But the pipeline never calls it.

The only post-turn profile write is person-side `engagement`:

- `pipeline.py:402-407`

So:

- no self-observations accumulate
- `strengths`, `flaws`, `triggers`, `dissonance`, and `maturity_score` do not develop from actual turns
- the self-model is mostly decorative in live execution

#### 5. Defense logging is ephemeral, not persistent

Defense events are appended to the in-memory `SelfProfile` object:

- `core/defense_mechanisms.py:266-279`

But that `SelfProfile` is reconstructed from profile-store observations in:

- `core/profiles/self_model.py:65-87`

Since defense events are not persisted into the store, they vanish on the next reload.

I confirmed this locally:

- debug-side `self_profile.defense_log` can show an event in the current turn
- a fresh `get_profile()` returns an empty `defense_log`

So the maturity/defense-history loop is not real yet.

#### 6. `load_config()` breaks on partial self-model config

When `negative_traits` is omitted, the loader falls back to `SelfModelConfig.negative_traits`:

- `config/loader.py:275-286`

That attribute does not exist on the dataclass class object. I confirmed this with a minimal custom config dir: it raises `AttributeError`.

This means the config layer is not robust under partial override.

#### 7. Primacy weighting is reversed

`extract_traits()` says primacy is weighted more heavily, but early observations are multiplied by `primacy_weight`, whose default is `0.8`:

- `core/profiles/base.py:149-168`

That makes early observations count less than later ones.

This is either:

- a mathematical bug
- or a naming/documentation bug

Right now it is both.

#### 8. Person-profile primacy decay is hardcoded

`PersonProfileManager.record_interaction()` ignores config for both the decay amount and floor:

- `core/profiles/person.py:152-155`

This undermines the “all constants configurable” design.

### High-Severity Functional Problems

#### 9. Resolution is only partially implemented

The architecture claims unresolved sources from contradictions, dodged topics, commitments, and deadlocks.

The runtime currently adds unresolved items only for spikes and dialogue deadlocks, and resolves the oldest item on any resolution event:

- `pipeline.py:420-445`

So most of the semantic richness described for resolution is not present yet.

#### 10. Resolution decay is effectively double-applied

Active unresolved contribution is computed as:

- current `item.intensity`
- multiplied by age-based decay weight

Code:

- `core/emotional_engine.py:253-272`

But item intensity is already decayed over time in:

- `core/emotional_engine.py:274-280`

That means age reduces contribution twice:

- once by mutating intensity
- once by applying time weight again

Pick one model.

#### 11. The “2-3 round” inner dialogue is effectively “1 or 3”

`_max_rounds()` returns `3` for both high resolution and the default case:

- `core/dual_process/inner_dialogue.py:372-384`

So resolution does not actually change round budget.

The only real branch is:

- bypass to `1`
- or allow up to `3`

That is not what the design claims.

#### 12. Unparseable slow-path output silently becomes approval

`parse_slow_path_response()` defaults to:

- `return True, stripped`

for anything it cannot classify:

- `core/dual_process/inner_dialogue.py:211-232`

This is dangerous because prompt drift or mediocre backend behavior can silently disable deliberative friction.

#### 13. Trust is double-counted and session-level trust magnitude is ignored

Per-turn trust updates happen here:

- `pipeline.py:409-412`

Then session digestion computes a trust delta, but `end_session()` throws away that magnitude and applies only the sign:

- `pipeline.py:461-466`

So:

- trust is updated per message and again at session end
- the session-level trust delta magnitude is not actually respected

I confirmed this locally: a session `trust_delta` of `0.042` only becomes `+0.02` at session end.

#### 14. Rule-based contagion leaves `certainty` and `intensity` basically dead

The fallback rule detector returns only `arousal` and `valence`:

- `core/contagion.py:189-202`

So for non-LLM contagion:

- `certainty` stays at default `0.5`
- `intensity` stays at default `0.0`

I confirmed this locally with an extreme insult. The result was:

- `arousal=1.0`
- `valence=0.0`
- `certainty=0.5`
- `intensity=0.0`

That weakens downstream event classification and any logic expecting meaningful emotional intensity.

### Medium-Severity Design/Runtime Issues

#### 15. API default persistence contradicts the project promise

The default web app creates:

- one global pipeline
- with `db_path=":memory:"`

Code:

- `interface/api.py:33-40`

So the default server discards long-term memory on restart.

That is the opposite of “persistent state is sacred.”

#### 16. The global API pipeline is mutable shared state

The API exposes one module-level pipeline:

- `interface/api.py:29-46`

That means all HTTP/WebSocket users share:

- one emotional engine
- one short-term memory
- one conversation history

That may be intended philosophically, but if not, it is a serious multi-user contamination problem.

#### 17. `LongTermMemory.retrieve()` is inefficient

It currently:

- loads all memories
- scores them in Python
- performs an extra DB query per memory to fetch `access_count`
- commits once per accessed memory

Code:

- `core/memory/long_term.py:113-189`

This will degrade as memory grows.

#### 18. Prompt loading is inconsistent for v2

The main config loader loads prompt templates centrally.

But inner dialogue reads from disk directly:

- `core/dual_process/inner_dialogue.py:43-49`

This breaks custom config-dir semantics and makes prompt overrides inconsistent.

### Prompt Quality Review

#### Good

`slow_path.md` is the strongest prompt in the repo:

- `config/prompts/slow_path.md`

It is clear about the decision criteria and the expected output envelope.

#### Risky

`fast_path.md`, `fast_path_revision.md`, and `arbiter.md` are underconstrained:

- `config/prompts/fast_path.md`
- `config/prompts/fast_path_revision.md`
- `config/prompts/arbiter.md`

They do not explicitly say:

- return only the final candidate text
- do not include analysis
- do not narrate your reasoning
- do not use labels like “Fast path:” or “Revised response:”

That makes them format-drift prone.

#### Architecturally weak

`generator.md` is clear stylistically, but weak structurally for v2:

- `config/prompts/generator.md`

It has no place for:

- dialogue candidate
- suppressed/defended version
- unresolved-item salience
- anticipation note
- inner dialogue trace summary

So even a good prompt cannot realize the intended architecture if the contract is incomplete.

### Test Gap Review

The test suite is broad, but too much of it verifies presence rather than causal effect.

#### What is covered reasonably well

- low-level engine math
- memory storage/retrieval basics
- prompt builder presence
- API fields
- many unit-level v2 pieces

#### What is not tested enough

1. Final-response dependence on inner dialogue

There is no strong integration test proving:

- changing the dialogue candidate changes the emitted final response

2. Final-response dependence on defense filtering

There is no strong integration test proving:

- a fired defense changes the actual final answer, not just debug metadata

3. Automatic temporal decay in live turns

There is no test asserting:

- elapsed real/simulated time between normal `process()` calls decays state

4. Non-accumulating context switching

The existing context test only checks trust divergence:

- `tests/test_emotional_journey.py:269-318`

It does not verify that baseline shift behaves as a switch instead of a ratchet.

5. Persistent self-model learning

There is no integration test showing:

- self observations are recorded during normal pipeline use
- later turns see updated self traits
- defense history survives profile reload

6. Resolution sources beyond spikes/deadlocks

There are not meaningful integration tests for:

- contradiction-created unresolved items
- topic-created unresolved items
- commitment items

7. Rule-based contagion `intensity` / `certainty`

Tests cover arousal/valence heavily:

- `tests/test_contagion.py:9-120`

But do not assert useful rule-based `certainty` or `intensity`.

### Performance Concerns

The first real bottleneck will be latency, not CPU:

- contagion LLM
- classification LLM
- 2-5 inner-dialogue LLM calls
- generator LLM
- optional self-check LLM

Then second-order bottlenecks:

- full memory scan each retrieval
- repeated trait extraction and DB writes
- multiple SQLite connections to the same DB without transaction design

The inner dialogue loop is especially expensive because it serializes dependent calls. That is fine if it genuinely changes outcomes. It is not fine if the result is later ignored by the generator.

## 4. Unified Judgment

### What the Project Is

Project Nūr is a serious experimental architecture with a strong emotional-state thesis and a better-than-average software skeleton.

It is not fake work.

There is real thinking here.

### What the Project Is Not Yet

It is not yet the full “hybrid cognitive architecture with inner life” described in the docs.

Right now, the code implements:

- emotional state
- some memory math
- some profile math
- a deliberation trace
- a defense trace

But it does not yet fully implement:

- self-model-driven behavior adaptation
- candidate-preserving fast/slow control of final output
- persistent defense/self-awareness growth
- truly integrated resolution semantics
- temporal emotional dynamics in ordinary use

### Brutal but Fair Verdict

The project’s idea is better than the current implementation.

The architecture is better than the current integration.

The code is good enough that fixing this is realistic.

This is not a teardown situation. It is an integration-and-state-ownership situation.

### What I Would Do Next, In Order

#### Priority 1: Make v2 actually affect the answer

Do this before anything else.

- Add `candidate_response` to `PipelineContext`
- Pass `filtered_output` into the generator
- Update `generator.md` to treat that candidate as the draft to refine, not optional context
- Add integration tests proving the final answer changes when the dialogue candidate changes

If you do not do this, most of v2 remains theater.

#### Priority 2: Fix time and context semantics

- track last-turn timestamp in the pipeline
- decay/recover state at the start of each turn
- change context switching from additive mutation to actual baseline/context application

This will stabilize the emotional math immediately.

#### Priority 3: Make the self-model real

- record self-observations after each response
- persist defense events
- derive `maturity_score` from persisted behavior/history
- add periodic reflection or at least session-end self extraction

Without this, the architecture’s self-awareness claims are overstated.

#### Priority 4: Repair trust and resolution accounting

- stop double-counting trust
- apply session trust deltas by magnitude, not just sign
- implement missing unresolved sources or reduce the claims in docs
- choose one decay model for unresolved contribution

#### Priority 5: Tighten prompt contracts

- make fast/revision/arbiter outputs strictly text-only
- reject or retry unparseable slow-path outputs instead of auto-approving them
- unify prompt loading under config

#### Priority 6: Fix scalability and persistence defaults

- make the default API use a file-backed DB
- decide whether one pipeline should serve many users
- optimize retrieval before memory count grows

## Final Assessment

If you want the simplest honest sentence:

> Project Nūr already has a strong emotional engine and a promising architecture, but its most ambitious v2 features are only partially behaviorally connected, so the system currently feels more advanced internally than it actually is externally.

That is fixable.

And if fixed, the project has a real chance of becoming interesting in a way most “emotional AI” projects are not.

## Appendix: Actionable Fix Roadmap

This roadmap converts the review into implementation work. It is ordered to maximize behavioral payoff early and avoid rework.

### Guiding Rule

Do not start with prompt tuning.

First fix state flow and ownership. Then fix response-path integration. Only then tune prompts and calibration.

### Phase 0: Establish a Reliable Baseline

Goal:

- make the project testable and reproducible before behavior changes

Tasks:

- install and pin the dev environment so `pytest` runs locally and in CI
- add a minimal smoke CI job: config load, unit tests, one pipeline integration test
- add a small `tests/test_regressions.py` file for confirmed bugs from this review

Add regression tests for:

- inner dialogue candidate affects final response
- defense affects final response
- context shift does not compound across repeated turns
- elapsed time decays state during normal `process()`
- partial `self_model` config without `negative_traits` does not crash
- defense history persists across profile reload

Done when:

- the repo has a reproducible test command
- the confirmed defects above are represented as failing tests before fixes

### Phase 1: Make v2 Actually Control the Response

Goal:

- connect inner dialogue and defense to the emitted answer

Files:

- `pipeline.py`
- `core/types.py`
- `core/dual_process/generator.py`
- `config/prompts/generator.md`

Tasks:

- add `candidate_response` to `PipelineContext`
- add `defense_instruction` to `PipelineContext`
- optionally add `dialogue_summary` or `dominant_path` if you want the master generator to know why the candidate exists
- in `pipeline.py`, pass `filtered_output` into the generator context instead of discarding it
- update `build_system_prompt()` to inject the candidate and defense instruction explicitly
- rewrite `generator.md` so the contract is:
  - candidate response is the draft
  - preserve its intent unless there is a strong reason to refine
  - final answer should be a polished version of that candidate, not a fresh unrelated response

Recommended prompt addition:

- `Draft response to refine: {candidate_response}`
- `Defense filter instruction: {defense_instruction}`

Tests to add:

- changing only the inner-dialogue candidate changes the final answer
- an active defense changes the final answer versus no-defense case
- when `MockLLMBackend` is used, the candidate text appears in the master prompt

Done when:

- `dialogue_trace.final_candidate` is behaviorally upstream of `result.response`
- defense activation changes output, not only debug metadata

### Phase 2: Fix Time and Context Semantics

Goal:

- make emotional state evolve over time correctly during ordinary use

Files:

- `pipeline.py`
- `core/emotional_engine.py`
- possibly `core/types.py`

Tasks:

- track `self._last_turn_time` in `CognitivePipeline`
- at the start of `process()`, compute elapsed seconds since last turn and call `self.engine.decay(elapsed)`
- define a real context-switch model

Recommended implementation change:

- stop using `apply_context_shift()` as additive mutation of current state
- introduce one of these two models:

Option A:

- keep a stable engine baseline
- compute an effective baseline for the current person on each turn
- decay toward that effective baseline

Option B:

- add `set_context_shift()` on the engine
- store current relationship shift separately from current state
- ensure the shift is applied as a resting target, not as an accumulated delta

Also:

- add tests for repeated turns with the same user proving the shift does not ratchet upward forever
- add tests for decay between two `process()` calls without explicit `apply_rest()`

Done when:

- state after 10 repeated neutral messages with the same context does not drift infinitely from baseline
- real elapsed time affects modulator recovery without manual rest calls

### Phase 3: Make the Self-Model Real and Persistent

Goal:

- turn self-awareness from a static readout into a learning subsystem

Files:

- `pipeline.py`
- `core/profiles/self_model.py`
- `core/profiles/base.py`
- `core/defense_mechanisms.py`
- `core/types.py`

Tasks:

- define what counts as a self-observation after each response
- after each turn, record 1-3 self-observations into the `ProfileStore`

Suggested first-pass self-observations:

- `blunt` when certainty high and response contains directive markers
- `avoidant` when avoidance topics are active and response redirects
- `empathetic` when negative user emotion is acknowledged
- `verbose` when response length exceeds threshold under low energy
- `defensive` when a defense mechanism fires

Persist defense history properly:

- do not store defense events only on the ephemeral `SelfProfile` dataclass
- either:
  - persist defense events in a new SQLite table, or
  - map them into profile observations and reconstruct summary history from storage

Add `maturity_score` derivation:

- define a deterministic formula from persisted evidence
- example inputs:
  - number of self observations
  - diversity of recognized flaws/triggers
  - repeated self-corrections
  - defense frequency decreasing over time

Do not leave `maturity_score` as a static float with no update path.

Tests to add:

- after multiple turns, self observations accumulate
- `get_profile()` changes after normal pipeline use
- defense activation survives profile reload
- maturity changes over time from persisted data

Done when:

- the self-profile changes because of actual use, not only handcrafted tests
- defense and maturity participate in a persistent feedback loop

### Phase 4: Repair Trust and Resolution Accounting

Goal:

- make long-horizon emotional accounting internally coherent

Files:

- `pipeline.py`
- `core/memory/digestion.py`
- `core/memory/long_term.py`
- `core/emotional_engine.py`

Tasks for trust:

- choose one trust update model:

Option A:

- per-turn trust updates only
- digestion stores memories but does not reapply trust

Option B:

- digestion is the authoritative trust update
- per-turn trust only affects temporary state, not persistent relationship trust

Recommended:

- keep small per-turn trust changes for immediate adaptation
- remove the extra sign-only session-end update
- if digestion adjusts trust, apply the actual `result.trust_delta` magnitude

Tasks for resolution:

- choose one decay model:
  - decay `item.intensity` over time
  - or keep intensity constant and use age-based weighting
- remove the other

Then expand source coverage:

- contradiction-created unresolved items
- topic-avoidance or topic-dodge unresolved items
- commitment unresolved items
- dialogue deadlock items
- spike items

Also improve resolution:

- do not resolve the oldest unresolved item blindly on any `resolution` event
- add source- or topic-matching heuristics

Tests to add:

- session-end trust delta uses magnitude correctly
- no double-counting across turn updates and digestion
- contradiction can create an unresolved item
- topic avoidance can create an unresolved item
- resolution event resolves the matching item, not arbitrary oldest

Done when:

- trust curves are explainable from one consistent accounting model
- resolution items map to actual narrative sources instead of only spikes/deadlocks

### Phase 5: Tighten Prompt Contracts and Parsing

Goal:

- reduce silent prompt drift and make multi-step LLM behavior enforceable

Files:

- `config/prompts/fast_path.md`
- `config/prompts/fast_path_revision.md`
- `config/prompts/arbiter.md`
- `config/prompts/slow_path.md`
- `core/dual_process/inner_dialogue.py`
- `config/loader.py`

Tasks:

- make all inner-dialogue prompts explicit about output shape

Recommended additions:

- `Return ONLY the response text, with no analysis or labels.`
- `Do not explain your reasoning.`
- `Do not include quotation marks around the response.`

For slow path:

- keep the `APPROVED:` / `OBJECTION:` contract
- if parsing fails, do not auto-approve silently
- instead:
  - retry once with a stricter repair prompt, or
  - treat it as objection/uncertain and log parse failure

Unify prompt loading:

- stop direct file reads from `inner_dialogue.py`
- load all prompt text through `config.loader`

Tests to add:

- malformed slow-path output triggers retry or explicit failure path
- custom config directory overrides v2 prompts too
- fast/revision/arbiter responses are handled as plain candidate text

Done when:

- prompt failure degrades explicitly, not invisibly
- all prompts obey one loading/config mechanism

### Phase 6: Reduce Pipeline Coupling

Goal:

- stop `pipeline.py` from being the sole owner of every state transition

Files:

- `pipeline.py`
- possibly new `core/turn_state.py` or similar

Tasks:

- introduce a `TurnState` or `TurnContext` dataclass for one message pass
- move assembly logic into helper methods:
  - `_prepare_turn()`
  - `_run_affect_phase()`
  - `_run_memory_phase()`
  - `_run_deliberation_phase()`
  - `_run_response_phase()`
  - `_commit_turn()`

This is not just cleanup. It reduces hidden state mutation and makes acceptance tests much easier.

Done when:

- the main `process()` method reads as orchestration rather than inline implementation
- each phase can be unit-tested in isolation

### Phase 7: Persistence and Multi-User Semantics

Goal:

- make deployment defaults match the stated philosophy of the project

Files:

- `interface/api.py`
- `pipeline.py`
- storage-related modules if added

Tasks:

- stop defaulting the API to `:memory:` unless this is explicitly “demo mode”
- provide a default file-backed path for the interactive app
- decide whether the app is:
  - one psyche shared across all users, or
  - one emotional engine per user/session

If shared psyche is intended:

- state that explicitly in docs and UI

If not intended:

- separate global long-term identity from per-user conversation state

Also:

- if concurrent requests are possible, add a synchronization strategy or per-request state handling

Done when:

- default server behavior is compatible with persistence claims
- multi-user semantics are explicit rather than accidental

### Phase 8: Performance Pass

Goal:

- remove obvious scaling bottlenecks after correctness is restored

Files:

- `core/memory/long_term.py`
- `core/profiles/base.py`
- `pipeline.py`

Tasks:

- eliminate N+1 query behavior in memory retrieval
- fetch `access_count` in the initial query instead of one query per memory
- batch `mark_accessed` updates or defer them
- consider caching extracted trait summaries per turn
- avoid repeated profile extraction work within one `process()` call

Do not optimize this phase before correctness phases are done.

Done when:

- retrieval no longer requires one SQL query per memory row scored
- per-turn profile/retrieval work is measurably cheaper under larger memory stores

### Suggested Delivery Order

If you want the shortest path to visible improvement:

1. Phase 1
2. Phase 2
3. Phase 3
4. Phase 4
5. Phase 5
6. Phase 7
7. Phase 6
8. Phase 8

Reason:

- Phase 1 immediately makes v2 real
- Phase 2 stabilizes the emotional engine
- Phase 3 makes the self-model matter
- Phase 4 repairs long-horizon accounting
- later phases harden and scale the system

### Minimal Acceptance Checklist

The roadmap is succeeding if all of the following become true:

- inner dialogue changes the final emitted response
- defense changes the final emitted response
- repeated neutral turns with the same person do not ratchet context shifts upward
- state decays naturally between ordinary turns
- self-profile traits evolve from real usage
- defense history persists and affects later behavior
- trust is updated by one coherent model
- unresolved items come from multiple real sources, not only spikes/deadlocks
- prompt parsing failures are explicit, not silently auto-approved
- default app sessions persist across restart
