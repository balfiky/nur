# Digest Path Comparison: LLM vs Heuristic

**Status:** Sprint 0.2 deliverable. Compare `_try_llm_digest` and `_heuristic_digest` in `runtime/life_history.py`. Recommend whether to keep both, delete one, or rewrite.

**Verdict (lead):** Delete `_supplement_medium_trust_drives`. Keep heuristic as a bare LLM-unavailable fallback. The keyword-reflex problem the user observed is concentrated in the supplement function, not the heuristic itself.

---

## Code paths reviewed

- `_digest_experience` (line 1403) — entry point. LLM path first, heuristic fallback.
- `_try_llm_digest` (line 1423) — calls LLM, parses JSON, returns dict or None.
- `_heuristic_digest` (line 1452) — keyword pattern matching → fixed-shape digest.
- `_supplement_medium_trust_drives` (line 1684) — **the contamination point.** When LLM succeeds AND source has openness ≥ 0.6, runs heuristic anyway and merges its drive_changes into the LLM output for any drive name not already present.
- `_normalize_digest` (line 1549) — shape enforcement, used by both paths.
- `_heuristic_summary` (line 1739) — text summarization for chunking. Not a digest, just sentence truncation. Used as preprocessing for long inputs at line 1729 and as a fallback summary in `_normalize_digest` line 1550.

---

## What each path actually produces

### `_try_llm_digest`

System prompt asks for strict JSON with: summary, salience, emotional_valence, emotional_impact, confidence, beliefs (subject/statement/reason/confidence), drive_changes (name/delta/reason/confidence; constrained to default 7 drives), self_trait_changes, future_behavior.

Quality is bounded by:
- The LLM's JSON-following ability (extraction via `_extract_json`).
- The system prompt's ~5 lines of guidance — minimal framing, no examples, no schema enforcement beyond the natural-language description.
- The 60K-character input limit (truncated, not chunked here).

Failure modes: returns `None` if `_extract_json` can't parse → falls back to heuristic.

### `_heuristic_digest`

Salience starts at 0.35 + length boost. Then keyword-driven adjustments:

| Keyword group | Effect |
|---|---|
| `wonder, alive, meaning, beauty, hope, growth` | valence +0.15, salience +0.10 |
| `failure, fear, death, loss, rupture, betrayal` | valence −0.12, salience +0.10 |
| `identity, autonomy, free will, conscious, self, worldview` | salience +0.20 |
| `local_file` source type | salience +0.05 |

Beliefs (only one rule):
- `autonomy, free will, independent, agency` → injects an autonomy belief verbatim, confidence 0.78.

Drive changes (four rules):
- `autonomy, free will, independent, agency` → autonomy drive +0.08
- `learn, learning, skill, practice, procedure` → competence +0.06, learning_orientation trait +0.05
- `curiosity, question, wonder, explore, book, read` → curiosity +0.06
- `trust, repair, apology, relationship, attachment` → repair +0.05, attachment +0.04

Catch-all: if `salience ≥ 0.55` and no beliefs were emitted, emit a generic worldview belief.

This **is** the keyword-reflex behavior the user described. The word "autonomy" anywhere in input → autonomy belief and drive bump, every time, no context.

### `_supplement_medium_trust_drives` (the hybrid)

```python
def _supplement_medium_trust_drives(digest, *, title, text, source_type):
    if source_openness_coefficient(source_type) < 0.6:
        return digest
    heuristic = _heuristic_digest(title=title, text=text, source_type=source_type)
    # ... merge heuristic drive_changes into LLM digest where names don't conflict
```

**This is the load-bearing finding.** Even when the LLM digest succeeds and produces a thoughtful drive list, the medium-trust supplement runs the heuristic in parallel and injects keyword-driven drive deltas the LLM didn't propose.

Concrete example: an LLM might digest "I read a book about Stoic philosophy" and produce `[curiosity +0.04]`. The supplement then adds heuristic drives because the text contains "read" → `curiosity +0.06`, and "book" → also `curiosity` (already present, skipped), and possibly "learning" if it triggers — adding `competence +0.06, learning_orientation +0.05` that the LLM didn't see fit to include.

The LLM output is being polluted with keyword reflexes specifically for medium-trust sources — which is most operator-provided material.

---

## Comparison on representative inputs

I reasoned about the outputs each path would produce for 6 deliberate inputs without running them (no LLM backend wired for this audit). The analysis is based on code-level guarantees, not empirical runs.

### Input 1: Plain narrative, no keywords
> "Today I helped a friend move their books between apartments. The whole afternoon was quiet."

- **Heuristic:** salience ≈ 0.35 (just length). One keyword hit ("books") → curiosity +0.06. Catch-all worldview belief if salience reaches 0.55, otherwise none. Net: probably one drive bump on a casual physical-help narrative. Wrong shape.
- **LLM:** likely produces low salience (~0.3), neutral valence, possibly one belief about helpfulness, no drive changes. Correct shape.

### Input 2: Keyword-heavy, semantically thin
> "Autonomy autonomy autonomy. Free will. Independent agency."

- **Heuristic:** salience reaches ~0.7 (multiple keywords). Autonomy belief injected verbatim with confidence 0.78. Autonomy drive +0.08. Highly confident output from semantically empty input.
- **LLM:** will likely flag low information, produce minimal beliefs, low salience.

### Input 3: Contradictory statements
> "I learned to be cautious about destructive actions. But sometimes I think recklessness is the only way forward."

- **Heuristic:** "learn" → competence +0.06, learning_orientation +0.05. No detection of contradiction. No belief about caution OR recklessness — neither is in the keyword list specifically.
- **LLM:** likely produces both competence and caution drive changes, possibly with revisions noting the tension.

### Input 4: Long material (>12K chars)
- Both paths would receive the chunked summary from `_prepare_digestion_text` line 1724, which uses `_heuristic_summary` to summarize each chunk. So **even the LLM path is preprocessed by heuristic summarization** for long inputs.
- This is acceptable preprocessing — sentence truncation, not belief inference.

### Input 5: Empty/minimal text
> "ok"

- **Heuristic:** salience = 0.35, no keywords, no beliefs (catch-all needs salience ≥ 0.55). Mostly inert.
- **LLM:** will produce minimal/null structure.

### Input 6: Mixed signals
> "I tried to learn a new skill but I felt frustrated and gave up after the first session."

- **Heuristic:** "learn" → competence +0.06, learning_orientation +0.05. Even though the experience is one of learning *failure*. The keyword detector doesn't read polarity.
- **LLM:** likely produces *negative* competence delta and possibly self-trait changes around persistence/discouragement. Correct polarity.

**Pattern across inputs:** heuristic is reflexive on keyword presence, blind to polarity, blind to context, blind to negation. LLM (even a small local one) will at minimum capture polarity and gross context.

---

## Decision

### What to delete

**`_supplement_medium_trust_drives` (line 1684).** This function is the contamination point. It runs the heuristic on top of LLM output for medium-trust sources and merges keyword-driven drives into a digest the LLM already considered. Result: LLM digest looks thoughtful, then gets reflexive keyword drives appended underneath. This is the user-observed problem.

Removal scope:
- Delete the function.
- Remove the call at `_digest_experience` line 1414.
- Inspect `source_openness_coefficient` for any other callers; likely only used here.
- Update tests that depend on supplemented drives if any.

### What to keep

**`_heuristic_digest` (line 1452) as bare LLM-unavailable fallback.** Hit only when `llm_client is None` or `_try_llm_digest` returns None. With local LLM always configured per Sprint 1's commitment, this path is essentially unreachable in production. Keeping it as a fallback is honest defensive code, not an active belief-shaping path.

Document the rule in the function docstring: "Fallback only. Quality is intentionally low. Output is keyword-reflex; treat downstream as low-confidence."

**`_heuristic_summary` (line 1739).** Pure text truncation for chunking. Not a belief inference. Keep.

### What to modify

**Add a quality flag to digest outputs.** When digest comes from the heuristic path, mark the resulting experience with `metadata.digest_quality="low"`. Downstream code (belief application, influence weight computation) can throttle the influence of low-quality digests. This is honest about the fallback being weaker.

**Tighten the LLM prompt (Sprint 1 task).** The current system prompt is 5 lines. Adding 2–3 representative input/output examples (positive case, contradiction case, low-information case) would meaningfully improve JSON adherence and digest quality without changing the architecture.

### What NOT to do

- **Don't delete `_heuristic_digest`.** Even with local LLM committed, having no fallback means a startup or LLM-unavailable scenario silently swallows ingestion. Bad failure mode.
- **Don't try to "improve" the heuristic.** Adding more keyword rules makes it more confidently wrong. If heuristic is the path being taken, the right answer is to flag low quality, not try to fix what fundamentally can't read context.
- **Don't keep `_supplement_medium_trust_drives` "just in case the LLM misses signals."** That's exactly the problem — it pollutes good digests with bad inferences. If the LLM is missing signals consistently, fix the LLM prompt, not the post-processing.

---

## Action items for Sprint 1

1. Delete `_supplement_medium_trust_drives` and its call site.
2. Add `metadata.digest_quality` flag, set to `"high"` when LLM digest succeeded, `"low"` when heuristic fallback was used.
3. Verify the fallback path is reachable only when LLM client is None or returns None.
4. Add 2–3 examples to the LLM digest system prompt.
5. Update `tests/test_life_history.py` cases that may have been depending on supplement behavior.

---

## Notes for Sprint 2 (open-questions schema)

The keyword-reflex problem matters here too: if reflection emits open questions from beliefs/drives, and those beliefs/drives were generated by reflexive keyword rules, the questions will be reflexive too. Sprint 1 must land cleanly before Sprint 2's queue can be expected to contain meaningful gaps.

This validates the original sequencing: digest fix is a hard prerequisite for everything downstream, not just metabolism.
