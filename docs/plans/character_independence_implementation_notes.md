# Character Independence Implementation Notes

Date: 2026-05-03

Implemented phases from `character_independence_plan.md` in one integration pass:

- A: pending cross-turn learning intake, intake receipts, and ledger-grounded identity answers.
- B1/B3: deterministic character vector assembly and deterministic coherence checks.
- F0: weighted influence with theme signatures and prompt-injection marker perception.
- H0/H: admin rollback removed from API behavior and UI; audit findings recorded.
- K0/K: genesis provenance tables and explicit `nur-genesis-reset` CLI.
- C: external belief/drive/future-behavior gates removed; domain bounds preserved.
- B2/B4/D/E/E.5/G/I/J: generator/vector integration, vector-shaped tool/proactive decisions, topic-relevant Life History retrieval wired from turn text into `retrieve_relevant()`, drive-modulated proactive scoring, skill-want trigger plumbing, dispositions, consolidation/decay/revision hooks, and introspection ingestion.
- Behavioral evals: six character-independence scenarios were added to `evals/scenarios.py` for Codex paste one-shot weighting, sustained theme accumulation, skill-want surfacing, belief revision, genesis isolation, and identity-question grounding.

Deviations:

- Existing prompt sections for soul, semantic memory, tools, and current emotional state remain in the generator prompt. Life History identity material now flows through `Durable Character State`; the older `{life_history}` placeholder is still supported for custom templates but is not used by the default template.
- Proactive config now uses `proactive_density_reference` and `proactive_recovery_seconds`; legacy YAML keys are accepted only as load-time aliases.
- `LifeHistoryStore.rollback_batch()` remains only for test-gated internals and raises unless `NUR_TESTING=1`.

Verification:

- `pytest -q` passed: 1695 passed, 1 warning.
- Final gate audit found no runtime hits for removed gate names or old proactive hard-cap constants.
