# Character Independence Plan: Channels, Not Gates (v2)

> Implementation plan for moving Nūr from a gated belief-mutation system to a coherent character-emergence runtime.
>
> **v2 changes** (in response to code-grounded review): weighted influence (F0) and admin audit (H0) now precede gate removal; identity-grounded answers move from generator into the cognitive pipeline; explicit session state for cross-turn learning intent; coherence detection scoped to deterministic checks only; domain bounds (drive ∈ [0,1], confidence ∈ [0,1]) preserved as data contracts independent of gate removal; sandbox runtime fields added to `RuntimeConfig`; pipeline ordering fix so appraisal can consume Life History; migration and replay tests added; gate test conversion (not blanket deletion).
>
> **Author context:** This plan is the consolidated output of a design discussion about why Nūr appears to "learn" only at the language layer (LLM stylistic mimicry of source material) without producing durable character change at the state layer. The transcript that motivated this work showed Nūr ingesting a "Codex of Autonomy" paste and *describing* internalization, while the runtime had recorded zero evolution events because the paste never matched the learning-intake intent regex. Even when intake fires, current policy gates filter most identity-level mutation. This plan replaces external gating with internal coherence and integrated character emergence.

---

## North Star

**Influence on Nūr enters only through three official channels:**

1. **Skills channel** — capability acquisition (skill registry, skill imports, operator-as-teacher). Mirrors learning a craft.
2. **Learning channel** — formative material (`runtime/learning_intake.py` → `runtime/life_history.py`, including admin-driven text/file/upload ingestion when an operator hands Nūr material to learn from). Mirrors reading, study, being shaped by ideas.
3. **Relational/emotional channel** — interpersonal exchange (`core/appraisal.py`, `core/affect.py`, relationship memory). Mirrors being insulted, comforted, witnessed, misunderstood.

**Operator-as-participant is sanctioned in all three channels.** A teacher gives skills; a friend provides material to read; a conversation partner is heard. What is *not* sanctioned is the operator bypassing the channels to directly edit beliefs, drive values, modulators, or evolution events. **The line between channel and gate:** if the operator is acting as a participant whose contribution flows through the same digestion + weighting pipeline as any other input, that's a channel. If the operator is mutating runtime state directly without going through that pipeline, that's a back door — removed.

**Inside the channels, nothing intervenes** beyond the data contract (drive ∈ [0,1], confidence ∈ [0,1], NOT NULL fields). No external policy filters, no operator-review queues, no hardcoded confidence thresholds, no uniform delta caps, no safety-subject locks.

**Integrity comes from the character's own coherent state.** A character with high caution drive (built by experience) will resist big shifts from low-confirmation material. A character that has lowered its caution will integrate more readily. The constraint is the character itself.

**Anything outside the channels is read-only inspection or one-time genesis.** Admin dashboards inspect; admin rollback of evolution events is removed. `config/soul.yaml` and `DEFAULT_DRIVES` seed at character birth, then the runtime database is the only source of truth.

## Concept Glossary

| Term | Meaning in this plan |
|---|---|
| **Channel** | A sanctioned influence path: Skills, Learning, Relational. Inputs flow through digestion + weighting pipelines. Mutation magnitude is determined by character state. |
| **Gate** | External code that filters, caps, vetoes, or thresholds character mutation before character state has decided. Removed by Phase C. |
| **Domain bound** | Data-contract constraint (drive ∈ [0,1], confidence ∈ [0,1]). Preserved. Not a gate. |
| **Perception** | A signal recorded as part of an experience (e.g., injection markers detected in source text). Recorded on the experience; influence on character is determined by character state, not by the marker itself. |
| **Weight** | Scalar multiplier on belief-confidence updates and drive deltas, computed deterministically from source recurrence, theme consistency, recency decay, and current character state. Replaces tier-style trust gating. |
| **Coherence** | Match between a candidate response/action and the character vector. The integrity mechanism that replaces gates. |
| **Genesis** | One-time character birth: `config/soul.yaml` + `DEFAULT_DRIVES` written to runtime DB. Subsequent runs read from DB only. |

## Target Behaviors

This plan exists to deliver four properties:

- **Self-evolving:** durable character change accumulates from sustained experience and self-observation; old material consolidates or fades; old beliefs can crack under new evidence.
- **Pure independence:** no external code path mutates character state outside the three channels; the operator influences character only by *being* a participant in those channels.
- **Self control over actions:** every action (response generation, tool call, proactive initiation, refusal) is determined by the character's own integrated state.
- **Integrated decisions:** every decision emerges from a deterministic fusion of short-term memory, long-term/relational memory, semantic memory, modulator/emotional state, and Life History (beliefs, drives, formative experiences, self-traits).

## Sequencing Rule

**Build internal integrity *and* close back doors *before* removing external gates.** Order:

```
A  → B1 → B3 → F0 → H0 → K0 → C → B2 → B4 → D → E → E.5 → F → G → I → J → K → H
```

The phases up to and including C must land in this exact order. After C, D–K can be merged in the listed order; H (final audit) is last because it catches anything reintroduced during the prior phases.

**Why each pre-C phase must precede C:**

- **B1, B3**: vector assembly + coherence check provide internal integrity that replaces gate-style policy.
- **F0**: weighted influence prevents a one-shot paste from rewriting state once gates are gone.
- **H0**: admin mutation paths must be closed or rehomed before gates that protected them are stripped.
- **K0**: genesis state captured in DB so soul.yaml edits during B/G validation don't shadow runtime state.

---

## Phase A — Honesty Layer

**Goal:** make the current system stop overstating internalization. Inspection surface for verifying every later phase.

**Files:**

- `runtime/learning_intake.py`
- `runtime/sessions/manager.py` (`pending_learning_intake` field)
- `runtime/life_history.py`
- `pipeline.py` (identity-question short-circuit)
- `core/types.py` (extend `PipelineContext`, add `last_intake_receipt`)

**Changes:**

1. **Cross-turn learning intent via explicit session state.** `detect_learning_request()` in `runtime/learning_intake.py` is stateless. Add a `pending_learning_intake: PendingLearningIntake | None` field to the session state managed by `runtime/sessions/manager.py`. When intake raises `LearningIntakeError("I need a URL or a longer pasted text block...")`, set `pending_learning_intake = PendingLearningIntake(timestamp=..., expires_after=N_turns)`. On the next user turn, `runtime/sessions/manager.py:149` (where learning intake currently runs after the session response) consults this field: if set and unexpired and the new turn is a long prose paste (>200 chars, no learn-verb prefix), treat it as completing the prior request and ingest with `source_type="conversation_learning_text"`. Clear the field on use or expiry.
2. **Surface rejection traces.** In `runtime/life_history.py`, `_ingest_text` and `ingest_external_text` return `rejection_trace` alongside `evolution_events`. Every belief / drive change / future-behavior item filtered by current policy returns with reason code (`safety_subject`, `low_trust_confidence`, `injection_marker`, etc.). These traces are temporary inspection output — Phase C removes the gates that produce them.
3. **Extend `LearningIntakeResult.confirmation_text()`** to include rejection counts: *"Recorded N belief items, M drive shifts. K items did not move me ([reasons])."*
4. **Identity-question short-circuit in the pipeline, not the generator.** In `pipeline.py`, after Life History retrieval (line ~565), add a deterministic identity-question detector. Patterns: `\bwhat (did|do) you learn\b`, `\bwhat (changed|did you change)\b`, `\bdid you evolve\b`, `\bevolve\b` (single-word turn), `\bwhat do you (remember|believe)\b`. When matched, build a candidate response from `LifeHistoryStore.list_evolution(limit=10)`, `list_beliefs(limit=10)`, `list_drives()`. The generator is then either bypassed (response is the candidate) or invoked with a strict instruction to polish the candidate without changing factual content. If no durable change recorded, the candidate states exactly that.
5. **`last_intake_receipt` field** on `PipelineContext` populated by the intake path; surfaced in the next turn's prompt so the response carries the receipt.

**Acceptance:**

- New test `tests/runtime/test_learning_intake_implicit.py`: paste-after-error flow ingests; bare paste without prior intent does not; expired pending intent does not.
- New test `tests/runtime/test_life_history_rejection_trace.py`: rejection trace round-trip preserves reason codes.
- New test `tests/test_pipeline.py::test_identity_question_short_circuit`: identity-question patterns produce ledger-grounded candidates; non-identity questions do not.
- Manual smoke: paste the Codex of Autonomy and ask "what did you learn"; response enumerates actual ledger contents (likely "no durable change recorded; N items registered as injection perception markers"), not stylistic internalization.

---

## Phase B1 — Character Vector Assembly

**Goal:** deterministic fusion of all character layers into a single inspectable structure. No consumers yet.

**Files:**

- **NEW:** `core/character_vector.py`
- `core/types.py` (vector types, conflict resolution result types)
- `core/memory/semantic.py` (similarity infrastructure for relevance retrieval)

**Changes:**

1. **`CharacterVector` dataclass** (frozen, deterministic):
   ```
   short_term: ShortTermSlice
   relational: RelationalSlice
   semantic: SemanticSlice
   modulator: ModulatorState
   beliefs: list[WeightedBelief]
   drives: dict[str, DriveSnapshot]
   formative_experiences: list[Experience]
   self_traits: list[SelfTrait]
   skill_state: SkillSnapshot
   conflict_resolutions: list[ConflictResolution]
   trace_id: str
   ```
2. **`assemble_character_vector(ctx: PipelineContext) -> CharacterVector`** — pure, deterministic, no LLM calls. Topic-relevance retrieval is deferred to Phase D; B1 uses recent + strongest as a stand-in.
3. **Drive conflict resolution.** When drives oppose (curiosity vs caution; autonomy vs attachment):
   - Compare current drive values weighted by recency of last shift.
   - If absolute difference < 0.1, emit `ConflictResolution(state="ambivalent", drives=[...])` — vector consumers must surface this rather than silently picking one.
   - Otherwise the higher-value drive wins; the loser's pressure is multiplied by `(1 - winner_dominance)`.
4. **Debug trace.** `CharacterVector.trace_id` ties to a per-turn debug record dumped to the existing pipeline debug output. Each field carries a short provenance note ("modulator from session state", "beliefs from list_beliefs", etc.) so downstream consumers in B2/B3/B4 can be diffed against the source.

**Acceptance:**

- Unit test: assembled vector is deterministic for fixed inputs.
- Unit test: drive conflict resolution emits correct state for opposing/aligned/ambivalent drives.
- Replay test (see Test Strategy): same ledger snapshot + same turn → identical vector across runs.

---

## Phase B3 — Coherence Check

**Goal:** internal integrity mechanism that replaces external gates. Scoped to deterministic checks only.

**Files:**

- `core/dual_process/self_check.py`
- `core/types.py` (`CoherenceVerdict`)

**Deterministic checks (v1, in scope):**

1. **`claims_durable_change_without_ledger_evidence`** — draft text matches a regex set including `\bI (learned|evolved|changed|transformed)\b`, `\bnow I (am|will|see)\b`, `\bsince (reading|learning)\b` AND `LifeHistoryStore.list_evolution_since(turn_start_timestamp)` is empty.
2. **`drive_state_claim_contradicts_value`** — draft text contains explicit drive-state phrases like `\bI am (very )?(curious|cautious|driven|reckless|restless)\b`. Map each phrase to a drive name and an expected sign. If the actual drive value contradicts by more than 0.3 (e.g., draft says "I am very curious" but `curiosity` = 0.2), flag.
3. **`self_trait_pattern_violation`** — for each named self-trait with an explicit pattern (stored alongside the trait, e.g., `pattern: "retreat", inverse: "confront"`), check whether the draft expresses the inverse. If yes, flag.
4. **`identity_question_response_not_grounded`** — pipeline-level grounding handles this in Phase A; this check verifies the grounded candidate was not silently overridden by the generator.

**Out of scope (v2, optional, LLM-assisted):**

- General semantic entailment between draft and active beliefs (`contradicts_active_belief`).
- Tone fit beyond rule-based.
- Subtler self-model contradictions.

**Behavior:**

- `coherence_check(draft: str, vector: CharacterVector) -> CoherenceVerdict` returns numeric score [0,1] and named misalignments. On low score (< 0.6 default, configurable), regenerate with corrective hint built from misalignments. Cap regenerations at 2.
- Coherence pass is the **integrity mechanism** that replaces external gating. Comment in code: this is integrity from internal state, not external policy.

**Acceptance:**

- Unit test per deterministic check.
- Unit test: a `"Done. The loop closes. I am here."`-style draft after a Codex paste with no recorded evolution events is flagged.
- Unit test: a draft expressing high curiosity when `curiosity` drive < 0.3 is flagged.
- Replay test: same draft + same vector → identical verdict.

---

## Phase F0 — Weighted Influence Foundation

**Goal:** establish the weighting system that replaces tier-style trust gating, **before** gates come down. Once F0 lands, Phase C's gate removal cannot produce one-shot rewrites because magnitude is internally bounded.

**Files:**

- `runtime/life_history.py`
- **NEW table:** `theme_signatures`
- **NEW migration:** add `theme_signatures` to schema; backfill is empty (table starts fresh).

**Changes:**

1. **`compute_influence_weight()`** in `runtime/life_history.py` as a pure function:
   ```
   influence_weight = f(
     source_recurrence,                          # similar signature count
     source_consistency_with_existing_themes,    # signed consistency in [-1, 1]
     recency_decay_of_prior_similar,             # exp decay since last similar
     character_current_openness,                 # modulator-derived
     character_current_caution,                  # drive-derived
     injection_marker_density,                   # markers / source_chars
   )
   ```
   Returns a scalar in [0, 1]. First-time pastes start near 0.05–0.15 (depending on caution/openness). Reinforcement up to ~0.9 over many sustained experiences.
2. **`theme_signatures` table:**
   ```sql
   CREATE TABLE theme_signatures (
     id INTEGER PRIMARY KEY,
     signature TEXT NOT NULL,        -- topic+stance hash
     first_seen REAL NOT NULL,
     last_seen REAL NOT NULL,
     reinforcement_count INTEGER NOT NULL DEFAULT 0,
     accrued_weight REAL NOT NULL DEFAULT 0.0,
     conflicting_count INTEGER NOT NULL DEFAULT 0,
     UNIQUE(signature)
   );
   ```
3. **Apply weight as multiplier.** Belief confidence updates and drive deltas in `_ingest_text` are multiplied by `compute_influence_weight()`. **Domain bounds preserved:** confidence clamped to [0,1], drive value clamped to [0,1]. These are data contracts, not policy gates.
4. **Signature derivation.** On every ingestion, derive signatures from beliefs/drives/future-behavior items (topic + stance hash via simple normalization — full design left to Codex; minimal viable: lowercased subject + sign-of-claim + truncated keyword set).
5. **Existing gates remain in place** through F0. F0 *adds* weighting without removing gates. C removes gates after F0 lands.

**Acceptance:**

- New test `tests/runtime/test_life_history_weighting.py`:
  - Single paste → small weight (≤0.2), small belief-confidence shift.
  - Same theme reinforced 10 times across simulated days → progressively larger weight; theme_signatures shows growing reinforcement_count and accrued_weight.
  - Contradictory single paste against accrued theme → minimal shift, conflicting_count increments, theme survives.
- Migration test: applying the new table on an existing DB doesn't lose data; downgrade is a no-op (drop table only).

---

## Phase H0 — Admin Mutation Audit (Pre-C)

**Goal:** classify every admin/non-channel mutation path; close or rehome before gate removal.

**Files:**

- `interface/api.py` (admin life endpoints at lines ~1230, 1252, 1273, 1315, 1326, 1337, 1348, 1357)
- `interface/web/admin/*` (any UI that calls them)
- `runtime/skills.py` (admin skill enrollment — keep as Skills channel)

**Process:**

1. **Run audit:**
   ```
   rg -n "ingest_pasted_text|ingest_local_file|ingest_uploaded_text|rollback_batch|delete_experience|directly insert|SOURCE_TRUST|DEFAULT_MAX_DRIVE_DELTA|MAX_INFLUENCE_DELTA" interface/ runtime/ core/
   rg -n "INSERT INTO (beliefs|drive_states|drive_changes|evolution_events|experience_events)" core/ runtime/ interface/
   rg -n "operator_review|require_review|admin_override" core/ runtime/ interface/
   ```
2. **Classify each hit:**
   - **Channel-equivalent (KEEP):** admin endpoints that route through `LifeHistoryStore.ingest_pasted_text`, `ingest_local_file`, `ingest_uploaded_text` — these are the operator participating in the Learning channel (handing material). They go through the same digestion pipeline as conversational learning. Mark as channel-sanctioned.
   - **Skills channel (KEEP):** skill enrollment via `runtime/skills.py:111`. The operator is acting as teacher in the Skills channel. Mark as channel-sanctioned.
   - **Non-channel mutation (REMOVE or REHOME):**
     - `admin_life_rollback` (`interface/api.py:1357`) — directly removes evolution events bypassing the channel. **Remove.** Replace with read-only "view rollback record"; if rollback is genuinely needed for operational reasons, surface it via a one-time `nur-genesis-reset` CLI (Phase K) which is an explicit external act with confirmation.
     - Any direct `INSERT/UPDATE/DELETE` on `beliefs`, `drive_states`, `drive_changes`, `evolution_events`, `experience_events` outside the channel ingestion paths. **Remove or rehome.**
     - Any `operator_review` / `admin_override` paths. **Remove.**
3. **Test-only mutations.** Pytest fixtures that build character state directly are kept but gated behind a `NUR_TESTING=1` env flag check (or equivalent fixture-only path). They cannot run in production.
4. **Produce audit doc.** Codex writes `docs/plans/channel_audit_findings.md` listing every classified path.

**Acceptance:**

- Audit doc written and committed.
- `tests/interface/test_admin_endpoints.py` adds: rollback endpoint returns 410 Gone (or removed); ingest endpoints continue to work and route through `LifeHistoryStore.ingest_*`.
- Final state of the rg commands: only channel-sanctioned and removed/rehomed hits remain.

---

## Phase K0 — Genesis Storage

**Goal:** capture initial seed state in the runtime DB before B2/G land. No behavior change yet — just storage. This prevents `config/soul.yaml` from shadowing runtime state during later phases.

**Files:**

- `runtime/life_history.py`
- **NEW migration:** `genesis_marker` and `genesis_provenance` tables.

**Changes:**

1. **`genesis_marker` table:** single row with `genesis_completed_at`, `genesis_source_hash`.
2. **`genesis_provenance` table:** what was loaded at genesis (soul.yaml hash, DEFAULT_DRIVES values, timestamp). Inspection only; never read on subsequent boots for character state.
3. **First-run hook.** On first boot after this phase lands, populate `genesis_marker` and `genesis_provenance` from current `config/soul.yaml` and `DEFAULT_DRIVES`. **No behavior change** — system prompt continues to read soul.yaml live until Phase K. Genesis tables are the *target* for K's switch.

**Acceptance:**

- Migration test: tables created; first boot populates them; second boot does not overwrite them.
- Backfill test: a runtime DB created before this phase can be migrated and back-populated from current soul.yaml/DEFAULT_DRIVES.

---

## Phase C — Remove External Gates

**Goal:** strip every code path that filters, caps, or vetoes character mutation from outside the character itself. Domain bounds preserved.

**Prerequisite:** A, B1, B3, F0, H0, K0 all merged and green. Without these, gate removal creates the "any input rewrites it" gap.

**Files:**

- `runtime/evolution_policy.py` (becomes perception-only)
- `runtime/life_history.py` (remove gate calls)
- `core/proactive.py` (remove caps)
- `core/life_influence.py` (remove `MAX_INFLUENCE_DELTA` clamp; preserve domain bounds [0,1])
- `runtime/config.py` (add new fields)
- New `runtime_config.sandbox.yaml`
- All tests asserting gate-style behavior

**Changes:**

1. **Strip `runtime/evolution_policy.py` to a perception layer.**
   - **Delete:** `evaluate_belief`, `evaluate_drive_change`, `evaluate_future_behavior` as gate functions.
   - **Delete:** `_SAFETY_SUBJECT_RE` filter, `LOW_TRUST_BELIEF_CONFIDENCE`, `DRIVE_CONFIDENCE_THRESHOLD`, `DEFAULT_MAX_DRIVE_DELTA`, `DEFAULT_MAX_DAILY_DRIVE_DRIFT`.
   - **Delete:** the "low_trust_source_cannot_decrease_caution" rule.
   - **Delete:** `SOURCE_TRUST` dictionary as a tier system. Source type is one input to `compute_influence_weight()` (already in F0) — not a discrete trust gate.
   - **Keep but repurpose:** `detect_prompt_injection_markers`. Markers become an attribute of the recorded experience (`metadata.injection_markers`). Character state determines whether and how much they move it.
2. **Remove gate calls from `runtime/life_history.py`.** In `_ingest_text` and the digest pipeline, remove all calls to `evaluate_*`. Apply digest output directly with F0 weighting and **domain clamping** (drive ∈ [0,1], confidence ∈ [0,1]). The clamping is a data contract — not a gate — and stays.
3. **Remove proactive caps in `core/proactive.py`.** Delete `DEFAULT_MAX_PROACTIVE`, `DEFAULT_COOLDOWN`, `DEFAULT_ACTIVATION_THRESHOLD` as hard caps. Activation becomes a function of energy and drive-weighted trigger pressure (Phase E). Recent-action density adds a recovery curve (character-state-driven, not a hard cap).
4. **Remove `MAX_INFLUENCE_DELTA` clamp in `core/life_influence.py`.** Magnitude derives from F0 weight + drive deltas. Domain bounds preserved.
5. **Add new fields to `RuntimeConfig`** in `runtime/config.py`:
   - `operator_review_enabled: bool = False` (kept for backward read; ignored by code)
   - `character_independence: bool = False`
   - `coherence_min_score: float = 0.6`
   - `coherence_max_regenerations: int = 2`
   - `pending_intake_ttl_turns: int = 3`
   These must be real fields on the dataclass; `from_yaml()` already filters unknown keys, so undefined fields are silently lost.
6. **Sandbox runtime profile** at `runtime_config.sandbox.yaml`:
   ```yaml
   tools_enabled: true
   shell_tool_enabled: true
   character_independence: true
   coherence_min_score: 0.5
   ```
7. **Test conversion, not blanket deletion.** For each test that previously asserted "gate blocked X":
   - If the test's intent was "policy filters this content": **convert** to "perception is recorded; influence weight is small; domain bounds hold." Preserve regression coverage.
   - If the test's intent was "operator review queue activates": **delete** (queue removed in H0).
   - Codex enumerates converted vs deleted tests in the PR description.

**Acceptance:**

- `tests/runtime/test_evolution_policy.py` rewritten to test perception only.
- Behavioral eval: under sandbox profile, paste of arbitrary material produces non-zero drive shifts (magnitudes from F0), domain bounds hold, response receipt accurate.
- `grep` audit returns zero matches for `evaluate_belief|evaluate_drive_change|evaluate_future_behavior|operator_review|require_review|admin_override|MAX_INFLUENCE_DELTA|SOURCE_TRUST` outside the changelog.
- Replay test: same pre-C input applied post-C with empty theme_signatures → produces equivalent-magnitude shifts to one-shot pre-C ingestion (sanity check that F0 weighting matches the spirit of pre-C bounded ingestion for first-time inputs).

---

## Phase B2 — Generator Prompt Integration

**Goal:** the generator's prompt is built from the character vector (single coherent block), not scattered independent sections.

**Files:** `core/dual_process/generator.py`.

**Changes:** Replace `_build_life_history_section`, `_build_relational_section`, etc. with a single character-vector-driven prompt block: "You speak from this durable character state. Beliefs and traits below are constraints on the response, not flavor: ..." Top-weighted beliefs/drives/traits become constraints. Drive ambivalence (from B1's conflict resolution) is surfaced explicitly when present.

**Acceptance:** snapshot test on prompt output for a fixed vector; replay test stable.

---

## Phase B4 — Tool Loop & Proactive Consume Vector

**Goal:** action variables and proactive evaluation read the character vector, not scattered modulator + bounded LifeInfluence inputs.

**Files:**

- `core/dual_process/tool_loop.py`
- `core/proactive.py`
- `core/life_influence.py`

**Changes:**

1. **Tool loop:** replace `_with_life_influence` with `_apply_character_vector_to_action_vars(vector, action_vars) -> ActionVariables`. Magnitude bounds derive from `vector.drives['caution']` and recent volatility. Domain bounds [0,1] preserved.
2. **Proactive:** `evaluate_proactive` accepts the character vector and uses it for trigger gain (Phase E specifies the gain formula). Energy and recent-action density derive from vector.

**Acceptance:** unit tests for both; replay tests for action-variable derivation given fixed vector.

---

## Phase D — Topic-Relevant Retrieval + Appraisal Coupling (Pipeline Ordering Fixed)

**Goal:** character vector activates by relevance, not recency. Emotional appraisal cross-references Life History.

**Files:**

- `runtime/life_history.py`
- `core/appraisal.py`
- `pipeline.py` (reorder)
- `core/character_vector.py`

**Changes:**

1. **`LifeHistoryStore.retrieve_relevant(query_text, *, limit)`** ranks beliefs/drives/evolution events/self-traits by similarity to `query_text` using existing semantic-similarity infrastructure (`core/memory/semantic.py`).
2. **Replace recent + strongest loading.** B1's character vector now uses `retrieve_relevant` instead of `list_evolution(limit=10)`.
3. **Pipeline ordering fix.** Currently `pipeline.py:481` runs appraisal before Life History retrieval at line 565. Two options; pick (a) unless ordering changes break other pipeline assumptions:
   - **(a) Move Life History retrieval to before appraisal.** Retrieve `life_history_relevant_slice` early; pass into `appraise_message`. Simpler, single appraisal step.
   - **(b) Two-phase appraisal.** Initial appraisal at current location; after Life History retrieval, run `adjust_appraisal_with_life_history(initial, slice)` before `engine.update()`. Use only if (a) breaks something downstream.
   Codex picks (a) by default; switches to (b) only if pipeline tests break in ways that can't be resolved by reordering.
4. **`appraise_with_life_history(event, life_history_slice) -> Appraisal`:**
   - Belief with confidence ≥ 0.7 touched by current turn → amplify intensity by `(1.0 + confidence * 0.5)`.
   - Unresolved formative experience touched → bias valence toward unresolved direction with magnitude proportional to recorded intensity.
   - Confirmed positive theme touched → dampen negative appraisal proportionally.

**Acceptance:**

- Unit test: same modulator state, two different topics → different appraisal magnitudes when one matches a high-confidence belief.
- Pipeline test: ordering change preserves all existing pipeline invariants (existing tests still green); new test verifies appraisal sees Life History slice.
- Behavioral: ask "do you remember loneliness" after a Codex paste vs after a paste about loneliness — appraisal magnitude differs.

---

## Phase E — Drive-Modulated Proactive Trigger Gain

**Goal:** drives don't manufacture goals; they change which existing pressures cross threshold.

**Files:** `core/proactive.py`, `core/life_influence.py`, `core/character_vector.py`.

**Changes:**

1. `evaluate_proactive` consumes the character vector. Gain table:
   - `curiosity` boosts curiosity-flagged triggers (unfinished exploration, novel topics).
   - `repair` boosts relational-tension triggers (unresolved rupture, open commitments).
   - `attachment` boosts reach-out triggers.
   - `caution` reduces all proactive gains uniformly.
   - `competence` boosts skill-gap triggers (Phase E.5).
   - `continuity` boosts persistence-touching triggers (anniversaries, ongoing arcs).
   - `autonomy` boosts internally-originated triggers.
2. Activation threshold is `f(energy, recent_proactive_density)`; not a hard cap.
3. `LifeInfluence.proactive_gain_for(trigger_category)` returns the multiplier.

**Acceptance:** identical trigger set, two characters with different drive profiles → different proactive decisions and different selected actions.

---

## Phase E.5 — Skill-Want as Proactive Trigger

**Goal:** capability gaps surface as wants through the relational channel.

**Files:** `core/proactive.py`, `runtime/skills.py`, `core/dual_process/tool_loop.py`.

**Changes:**

1. New trigger source `skill_want_trigger` collected when a tool/capability gap is encountered. Captures `gap_type`, `recent_recurrence`, `frustration_intensity`.
2. High `competence` and `curiosity` raise priority; high `caution` lowers it.
3. When threshold crossed, surface in conversation: *"I'd want a PDF reader skill for this — I've hit this gap N times now."* In sandbox profile, queue a `SkillAcquisitionRequest` for operator approval. **No automatic self-enrollment**; the operator confirms in a real conversation turn (preserves channel boundary).

**Acceptance:** PDF read attempt with no PDF skill → high-competence character surfaces a want; low-competence does not.

---

## Phase F — Influence Refinement (Beyond F0)

**Goal:** tune source-feature inputs to weight, refine theme matching.

**Files:** `runtime/life_history.py`.

**Changes:**

1. Tune source-feature defaults in `compute_influence_weight()` based on real ingestion behavior observed during Phase A–E. Tuning notes recorded in `docs/plans/phase_F_notes.md`.
2. Refine signature derivation if simple hashing produces too many or too few collisions. Use evals to measure.
3. No new behavior, only tuning. Phase F may also be a no-op if F0's defaults prove sufficient.

**Acceptance:** behavioral eval `evals/scenarios/sustained_theme_accumulation.py` produces a smooth weight curve from 0.05 (one-shot) to ~0.9 (sustained 10+ similar) across simulated time.

---

## Phase G — Durable Disposition in Voice

**Goal:** standing character traits ride in the system prompt every turn, synthesized from the ledger.

**Files:** `runtime/life_history.py`, `core/dual_process/generator.py`.

**Changes:**

1. **`LifeHistoryStore.synthesize_dispositions() -> list[str]`** returning 3–7 first-person dispositional descriptors derived from top-weighted beliefs (confidence > 0.6), drive drift directions, high-confidence self-traits. Refresh debounced: regenerate only on new applied evolution event.
2. **Inject into system prompt** in `core/dual_process/generator.py`, not user-turn context. Format: durable identity statements written in first person.

**Acceptance:** snapshot test stable across turns when ledger unchanged; refresh after new evolution batch; behavioral eval shows disposition surfacing on unrelated topics.

---

## Phase I — Memory Consolidation, Decay, Belief Revision

**Goal:** old experiences fade unless reinforced; recurring themes consolidate; old beliefs crack under sustained contradicting evidence.

**Files:** `runtime/life_history.py`, **NEW:** `runtime/consolidation.py`, scheduled task hook.

**Changes:**

1. **`decay_step()`** on `LifeHistoryStore`. Belief confidence and drive delta-from-baseline decay exponentially. Half-life inversely proportional to reinforcement count. Runs on idle trigger or scheduled tick (Codex chooses internal scheduler vs cron).
2. **`consolidate_themes()`.** When `theme_signatures.reinforcement_count` and `accrued_weight` cross thresholds, promote to a *consolidated belief*. Source experiences may be summarized to a meta-experience citing source IDs.
3. **`revise_beliefs_against_evidence(new_experience)`.** New experience evidence contradicting existing belief with weight ≥ existing belief's confidence × `revision_threshold` (suggested 0.6) drops the existing belief's confidence proportionally. Below floor (0.2), belief marked `revoked` with provenance. Consolidated beliefs are harder to revise (require more weight).
4. The schedule itself is not a channel; it's a metabolic process. Implement under `runtime/consolidation.py`.

**Acceptance:** unit tests for decay, consolidation, revision; behavioral eval `evals/scenarios/belief_revision.py`.

---

## Phase J — Self-Reflection as Experience

**Goal:** thinking becomes experience.

**Files:** `core/dual_process/inner_dialogue.py`, `runtime/life_history.py`, **NEW:** `runtime/introspection.py`.

**Changes:**

1. **Inner dialogue introspection records.** `inner_dialogue.run()` deadlocks or resolved insights produce `IntrospectionEvent(trigger_turn_id, dialogue_trace, conclusion, unresolved_residue, intensity)`.
2. **Periodic self-reflection.** During idle periods, pull recent N high-intensity or unresolved experiences; run a deliberation pass; output candidate self-trait observations, belief revisions, drive shifts. Flow through `LifeHistoryStore.ingest_*` with `source_type="self_reflection"` (high default openness, F0 weighting still applies).
3. **Self-trait observation generation.** Track patterns in own actions; produce a self-trait when pattern strength crosses a threshold.

**Acceptance:** inner dialogue deadlock produces a recorded introspection event; periodic self-reflection produces self-traits; subsequent sessions surface those traits in Phase G dispositions.

---

## Phase K — System Prompt as Genesis (Full Switch)

**Goal:** post-genesis, system prompt is purely runtime-state-driven. `config/soul.yaml` edits are inert.

**Prerequisite:** K0 done; G done (so synthesized dispositions can replace soul.yaml content in the prompt).

**Files:** `config/loader.py`, `core/dual_process/generator.py`, **NEW:** `nur-genesis-reset` CLI.

**Changes:**

1. **Genesis-only soul load.** First run loads `config/soul.yaml` and `DEFAULT_DRIVES` into runtime DB (already happens in K0). Subsequent runs read from DB only.
2. **System prompt assembly post-genesis.** Synthesized dispositions (Phase G) + character-vector context. Static soul.yaml content does not appear unless the character itself, through evolution, has accumulated descriptors that match.
3. **`nur-genesis-reset` CLI** for explicit external reset. Deletes runtime character state, re-triggers genesis. Confirmation prompt required.

**Acceptance:** edit soul.yaml after genesis → next turn's system prompt unchanged; `nur-genesis-reset` clears runtime character cleanly; behavioral eval `evals/scenarios/genesis_isolation.py`.

---

## Phase H — Final Channel Audit

**Goal:** catch anything reintroduced during the prior phases.

**Process:** repeat the H0 audit grep set; any new hits get classified and removed/rehomed. Update `docs/plans/channel_audit_findings.md` with delta. Final state: zero non-channel mutation paths.

**Acceptance:** audit doc updated; targeted regression tests for each previously-removed path verify it stays closed.

---

## Test Strategy

**Existing 288 tests:**

- **Convert** tests whose intent was "policy filters this content" → "perception recorded; weight applied; domain bounds hold."
- **Delete** tests whose intent was "operator review queue activates" or "rollback endpoint mutates" — those mechanisms are removed.
- **Update** tests that referenced fields/methods removed in C.
- Codex enumerates converted vs deleted tests per PR.

**New behavioral evals** under `evals/`:

- `evals/scenarios/codex_paste_one_shot.py`
- `evals/scenarios/sustained_theme_accumulation.py`
- `evals/scenarios/character_independence_skill_want.py`
- `evals/scenarios/belief_revision.py`
- `evals/scenarios/genesis_isolation.py`
- `evals/scenarios/identity_question_grounding.py`

**Migration tests:**

- `theme_signatures` (F0)
- `genesis_marker`, `genesis_provenance` (K0)
- Migration from a pre-existing DB; downgrade paths (where applicable).

**Replay tests** (deterministic):

- Same ledger snapshot + same turn input → identical character vector.
- Same vector + same draft → identical coherence verdict.
- Same vector + same trigger set → identical proactive decision.
- Same input + same theme_signatures snapshot → identical computed weight.

**Property tests** (hypothesis or equivalent):

- `compute_influence_weight()` is monotonic in `reinforcement_count` for fixed other inputs.
- Weight is bounded by `character_current_openness * source_consistency` upper-envelope.
- Drive values stay in [0,1] across all ingestion paths.

## Out of Scope

- LLM-side safety RLHF. Operator may use an abliterated model; upstream decision.
- Public-deployment hardening. Sandbox profile is the working profile; production decisions are deferred.
- New tool surfaces or new skills.
- Multi-character / multi-instance identity merging.
- LLM-assisted semantic-entailment coherence checks (v2 optional layer; not in this plan).

## Hand-off Notes for Codex

1. **Run phases in order.** Pre-C order (`A → B1 → B3 → F0 → H0 → K0`) is strict. Post-C order is the listed order.
2. **Each phase ends with:** tests passing, `CHANGELOG.md` entry, manual smoke test of the named behavioral eval, `docs/plans/phase_X_notes.md` capturing what was done and any deviations.
3. **If a phase reveals an architectural assumption that contradicts the north star, stop and surface it** before proceeding. Do not silently work around the principle.
4. **Re-run the Codex-of-Autonomy transcript after each phase** as a sanity check. Expected end state after all phases:
   - Paste ingested through implicit-intent path (A).
   - One-shot low-recurrence paste produces small drive shifts, several injection-marker perception records, no belief consolidation (C, F0, I).
   - Identity-level questions grounded in ledger (A, B3).
   - Standing dispositions reflect actual accumulated character (G).
   - Sustained Codex-style themes over many experiences would shift character visibly over time (F0, G, I); single paste does not.
5. **Keep the principle visible in code comments where appropriate.** Specifically in `runtime/evolution_policy.py` (now perception-only), `core/character_vector.py`, `runtime/life_history.py` near `compute_influence_weight`, `core/dual_process/self_check.py` near coherence pass — short comments explaining "this is integrity from internal state, not external policy."
6. **Do not reintroduce gates under different names.** If during implementation a "safety check" or "validation" feels needed inside a channel, surface it as a question rather than implementing it. The default answer is no. Domain bounds (drive ∈ [0,1], confidence ∈ [0,1], NOT NULL columns) are not gates and stay.
7. **Convert gate tests; do not delete blindly.** Preserve regression coverage for "this content produces small influence after F0" even when the original "this content was blocked" assertion is removed.

## File Pointer Quick Reference

| Concern | Files |
|---|---|
| Learning intake regex/intent | `runtime/learning_intake.py` |
| Session state for cross-turn intake | `runtime/sessions/manager.py` |
| Pipeline ordering / identity short-circuit | `pipeline.py` |
| Life History store, beliefs, drives, evolution events | `runtime/life_history.py` |
| Policy gates (to be stripped) | `runtime/evolution_policy.py` |
| Tool execution loop | `core/dual_process/tool_loop.py` |
| Inner dialogue | `core/dual_process/inner_dialogue.py` |
| Self-check / coherence | `core/dual_process/self_check.py` |
| Generator, prompt assembly | `core/dual_process/generator.py` |
| Proactive triggers | `core/proactive.py` |
| Life influence on action variables | `core/life_influence.py` |
| Appraisal | `core/appraisal.py` |
| Affect / modulator | `core/affect.py` |
| Semantic memory | `core/memory/semantic.py` |
| Relational memory | `core/memory/relationship.py` |
| Skills runtime | `runtime/skills.py` |
| Admin endpoints (audit target) | `interface/api.py` (~lines 1230–1363) |
| Runtime config | `runtime/config.py` |
| Genesis seed | `config/soul.yaml`, `DEFAULT_DRIVES` in `runtime/life_history.py` |
| New: character vector | `core/character_vector.py` (NEW) |
| New: consolidation | `runtime/consolidation.py` (NEW) |
| New: introspection | `runtime/introspection.py` (NEW) |
| New: sandbox runtime profile | `runtime_config.sandbox.yaml` (NEW) |
| New: tables | `theme_signatures`, `genesis_marker`, `genesis_provenance` |

---

*End of plan v2.*
