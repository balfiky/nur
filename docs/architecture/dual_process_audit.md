# Dual-Process Architecture Audit

**Status:** Sprint 0.1 deliverable. Ground-truth survey of `core/dual_process/` and the surrounding orchestration in `pipeline.py`.

**Question this answers:** Is the Sprint 3 prompt split (life-at-decision-time vs skills-at-execution-time) a refactor on a clean seam, or a rewrite?

**Answer:** Refactor. The pipeline already has decision and execution stages — they exist as separate, sequenced phases — but the *master generator* collapses everything back into a single LLM call where life and skills appear together in one prompt. The fix is at one specific code point, not a pipeline-wide redesign.

---

## Files surveyed

| File | LOC | Role |
|---|---|---|
| `core/dual_process/generator.py` | 568 | **Master generator.** Builds the single big system prompt and runs one LLM call. |
| `core/dual_process/inner_dialogue.py` | 498 | **Fast/slow path deliberation** (intuitive vs reflective voices). Iterative LLM-driven negotiation that produces a `candidate_response` for the master generator. |
| `core/dual_process/self_check.py` | 348 | **Post-generation validator.** Rule-based + optional LLM. May trigger regeneration with correction note. |
| `core/dual_process/tool_loop.py` | 1035 | **Tool intent → arbiter → execute → appraise.** Detects intent via regex heuristics; runs through arbiter; executes tools; produces `tool_context_summary`. |

The directory name "dual_process" refers to inner_dialogue's fast/slow voice pattern — **not** to "decision vs execution." That's an important terminological distinction for the rest of this doc.

---

## Pipeline orchestration order

From `pipeline.py` (line numbers approximate, captured 2026-05-08):

```
user_message
  ↓
[562] _load_life_history_context()         → ctx.life_history_context
[669] _load_skill_context()                → ctx.skill_context
[~600] affect / appraisal / person profile → ctx.affect_state, etc.
[708] decide_agency()                       → ctx.agency_decision
  ↓
[722] inner_dialogue.deliberate()           → candidate_response
        ↑ sees: state, person, self, values, memories, unresolved
        ↑ does NOT see: life_history, skills
  ↓
[782] assemble_character_vector(decision_ctx)
        ↑ includes life history + skills (in skill_state field)
  ↓
[803] tool_loop.run_tool_loop()             → tool_context_summary
        ↑ uses: action_variables, life_influence, character_vector
        ↑ regex-detects tool intent (NOT LLM-based)
        ↑ skills appear here only as managed objects (skills.list, etc.)
        ↑ skill *behavioral guidance* not used at this stage
  ↓
[~970] strategy = response_strategy()
[1006] assemble_character_vector(ctx)        ← reassembled with full ctx
  ↓
[1009] generator.generate(ctx)               ← THE master LLM call
        ↑ system prompt = ALL sections concatenated
        ↑ life_history_section + skill_section + everything else
  ↓
[1019] self_checker.check(response)
        ↑ may trigger regeneration with correction_note
  ↓
final response
```

---

## Where does each subsystem actually enter the LLM context?

| Subsystem | Inner dialogue prompts | Tool loop | Master generator | Self-check |
|---|:---:|:---:|:---:|:---:|
| Modulator state | ✅ | ✅ (action_variables) | ✅ | ✅ |
| Person profile | ✅ | ✅ (trust) | ✅ | — |
| Self profile | ✅ | — | ✅ | ✅ |
| Values | ✅ | — | ✅ | — |
| Memories | ✅ | — | ✅ | — |
| Unresolved items | ✅ | — | — | — |
| Soul profile | — | — | ✅ | ✅ (name only) |
| **Life history (beliefs/drives)** | ❌ | ✅ (via `life_influence` modulating action_variables) | ✅ (in `_build_life_history_section`) | — |
| **Skills (behavioral guidance)** | ❌ | ❌ (only as managed tool objects) | ✅ (in `_build_skill_section`) | — |
| Tool context summary | — | (produces) | ✅ | — |
| Candidate from inner dialogue | — | — | ✅ (`_build_candidate_section`) | — |
| Response strategy | — | — | ✅ | — |
| Agency decision | — | (consumed) | ✅ (`_build_affect_agency_section`) | — |
| Contradiction flags | — | — | ✅ | ✅ |
| Defense instruction | — | — | ✅ | — |

**The only stage where life history and skills coexist is the master generator.** This is the load-bearing finding for Sprint 3.

---

## Detailed observations per file

### generator.py

`build_system_prompt(ctx)` at line 57. Builds the system prompt by calling 19 separate `_build_*_section` helpers and concatenating them via template substitution (or fallback string-build).

Key sections relevant to Sprint 3:
- `_build_life_history_section` (line 288) — emits "## Life History / Evolving Worldview" with beliefs, drives, recent evolution. Framing: *"Private identity context… let it subtly shape perspective and priorities."*
- `_build_skill_section` (line 340) — emits "## Enabled Skills". Framing: *"Private operating guidance imported by the operator. Use these skills when relevant."*

Both sections are conditionally included — empty input → empty section. But when both have content, both go into the same prompt.

The actual generation is a single `self._backend.generate(system_prompt, full_message)` call (line 540). One LLM call per response.

### inner_dialogue.py

The "dual process" here is **fast (intuitive) vs slow (reflective)**, not decision vs execution. Loop:

1. **Round 1**: fast path generates → slow path approves or objects.
2. **Round 2** (if objection): fast path revises against objection → slow path re-evaluates.
3. **Round 3** (if still objection): arbiter synthesizes both positions.

Bypass conditions (`_max_rounds` at line 414):
- High arousal (> 0.8) → only round 1 (fast only).
- Low energy (< 0.2) → only round 1.
- Low resolution AND no real unresolved → 0 rounds (skip entirely).
- Spike-only unresolved → 0 rounds.

Prompts built by `build_fast_path_prompt` (line 122), `build_slow_path_prompt` (line 145), `build_revision_prompt` (line 176), `build_arbiter_prompt` (line 197).

**Crucially:** none of these prompts include `life_history_context` or `skill_context`. Inner dialogue operates on emotional/relational context only. Whatever final candidate emerges goes into the master generator's `_build_candidate_section`.

This means inner dialogue is *already* a "pre-decision" stage that doesn't see skills. We don't need to invent it.

### self_check.py

Two paths: rule-based checks (`_check_tone_fit`, `_check_overconfidence`, `_check_bluntness`, `_check_contradictions`, `_check_energy_fit`) and an optional LLM check. Coherence verdict (line 56) cross-references draft text against the assembled character vector to catch claims-of-change without ledger evidence and drive-state claims that contradict actual values.

Doesn't see skills. Sees character vector → indirectly sees life history (beliefs, drives, formative experiences are in the vector).

If checks fail, the pipeline regenerates with a correction note injected into `ctx.candidate_response` for the next master generator call.

### tool_loop.py

Detects tool intent through `_TOOL_PATTERNS` (line 106) — a list of `(regex, tool_name, arg_extractor)` triples. Not LLM-driven. The patterns include `skills.list`, `skills.enable`, `skills.audit`, etc. — these are skill *management* operations, not invocations of skill *behavioral guidance*.

Decision arbiter at `make_tool_decision` (line 492). Driven by:
- Autonomy level (off / assisted / autonomous / high_risk)
- Agency action (comply / refuse / disengage / slow_down / resist)
- Action variables (risk_tolerance, autonomy_bias, clarification_threshold, action_urgency)
- Tool category (READ_ONLY / WRITE / DESTRUCTIVE / EXTERNAL_ACTION)

Result: execute, clarify, defer, or refuse.

**Skills are not consulted for behavioral guidance during tool decisions.** A skill that says "always ask before deleting" has no path to influence the arbiter — that policy lives only in the master generator's prompt.

This is one of the things Sprint 3/4 needs to address: trigger-time skill retrieval should provide guidance to the tool loop too, not only the master generator.

---

## What the Sprint 3 refactor actually requires

The original plan said: *"split decision-time from execution-time prompts."* Re-reading that against the actual architecture, the cleaner formulation is:

> **Skills should only enter the master generator's prompt when a triggering condition is met.** Life history is always-on (it's identity); skills are conditional (they're craft).

Concretely:

1. **Add `applies_when` field to skill metadata** (`runtime/skills.py`). Free-text predicate or list of trigger conditions. Backward-compat: empty = always-applies.

2. **Modify `enabled_skill_context()`** to accept a `context_hint` arg (e.g., the user message + chosen tool name + response strategy). Filter skills by trigger match.

3. **Thread `context_hint` from pipeline** into `_load_skill_context` after the tool loop completes (so we know which tool, if any, was invoked).

4. **Optionally: provide skill guidance to the tool arbiter** in `tool_loop.py`. When a skill's trigger matches and it has risk-relevant directives ("always confirm before destructive"), feed those into `make_tool_decision`. This is a bigger change — defer to a later sprint.

What we do **NOT** need to do:
- Add a second LLM pass. Doubles latency, doesn't solve the actual problem (skills coexisting with life in the prompt is fixable in one pass).
- Restructure inner_dialogue or self_check. They're already skill-free.
- Change how life_history is loaded/formatted. It's working.
- Touch the orchestration sequence in `pipeline.py`. The seam is at `_load_skill_context` and `_build_skill_section`, not at the pipeline level.

**Bottom-line estimate for Sprint 3:** 1 week, not 3. The big risk I flagged in the consultant review (that the seam might require a rewrite) does not materialize.

---

## What this audit changes about the broader plan

1. **Sprint 3 estimate downgraded** to 1 week from "1–3 weeks." Spike-resolved.

2. **Constitution layer placement clarified.** The constitution belongs in `_build_life_history_section` as a "Stable orientation" sub-section above beliefs. Don't add a new section type — extend the existing one.

3. **Sprint 4 scope extends slightly.** The category audit should also add `applies_when` to migrated skills (procedural ones). Currently the plan only mentions migrating dispositional skills out — it should explicitly require `applies_when` on what remains.

4. **Future opportunity (not in current plan):** trigger-time skill retrieval inside `tool_loop.make_tool_decision`. Would let skills like `karpathy-guidelines` actually influence "ask before destructive" decisions instead of being prompt text the model may or may not honor. Park this for a later sprint.

5. **Inner dialogue is healthier than the consultant review assumed.** It already excludes skills. The pre-decision stage exists. We were arguing for something that's partially built.

---

## Citations

- Pipeline orchestration: `pipeline.py:467-1066`
- Inner dialogue prompt builders: `core/dual_process/inner_dialogue.py:122-213`
- Master generator entry: `core/dual_process/generator.py:518-551`
- Skill section builder: `core/dual_process/generator.py:340-379`
- Life history section builder: `core/dual_process/generator.py:288-337`
- Tool intent regex table: `core/dual_process/tool_loop.py:106-259`
- Tool arbiter: `core/dual_process/tool_loop.py:492-612`
- Self-check coherence verdict: `core/dual_process/self_check.py:56-97`
