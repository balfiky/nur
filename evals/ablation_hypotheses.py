"""Hypotheses for architecture ablations on the Phase 11 scenario set.

Each ablation toggles one component off and we make a *prior claim*
about which scenarios should be affected. Running the ablation then
labels each scenario outcome as one of:

- ``expected_failure``   — predicted-to-fail scenario did fail
- ``unexpected_failure`` — a scenario we didn't predict failed anyway
- ``no_effect``          — a scenario we didn't predict to fail still passes
- ``newly_passing``      — predicted-to-fail scenario actually passed
                          (suggests the component wasn't doing the work)

Keeping hypotheses in a separate file (auditable, not buried in code)
lets reviewers inspect the prior claims and grade them after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.pipeline_features import PipelineFeatures


@dataclass(frozen=True)
class Ablation:
    """One ablation variant: a feature set + a hypothesis about effects."""
    label: str
    features: PipelineFeatures
    hypothesis: str
    expected_failures: tuple[str, ...] = field(default_factory=tuple)


# Ordering matches the recommended run order: most-diagnostic first,
# negative control last.
ABLATIONS: list[Ablation] = [
    Ablation(
        label="no_relationship_memory",
        features=PipelineFeatures(relationship_memory=False),
        hypothesis=(
            "Phase 11 scenarios that exercise cross-turn open-loop tracking "
            "should fail when the relationship-memory component is disabled. "
            "Other scenarios (single-turn strategy selection) should still pass."
        ),
        expected_failures=(
            "p11_open_loop_challenge",
            "p11_repair_closes_loop",
        ),
    ),
    Ablation(
        label="no_inner_dialogue",
        features=PipelineFeatures(inner_dialogue=False),
        hypothesis=(
            "Phase 11 assertions check strategy selection and modulator state, "
            "both upstream of inner dialogue. No scenario failures are predicted; "
            "this ablation primarily produces a metrics delta (LLM calls, latency) "
            "that tells us what inner dialogue costs for no structural change."
        ),
        expected_failures=(),
    ),
    Ablation(
        label="no_defense",
        features=PipelineFeatures(defense=False),
        hypothesis=(
            "Defense shapes response wording; Phase 11 assertions check structural "
            "outcomes (strategy, modulators, memory writes) that are upstream of "
            "defense. No scenario failures are predicted."
        ),
        expected_failures=(),
    ),
    Ablation(
        label="no_semantic_memory",
        features=PipelineFeatures(semantic_memory=False),
        hypothesis=(
            "Negative control. Phase 11 scenarios don't exercise semantic retrieval. "
            "Zero effect is predicted. Any scenario failure here indicates a hidden "
            "coupling between semantic memory and other components that needs to "
            "be investigated before ablation results can be trusted."
        ),
        expected_failures=(),
    ),
]


def label_outcome(
    ablation: Ablation,
    scenario_id: str,
    passed: bool,
) -> str:
    """Classify one scenario's outcome under an ablation against the hypothesis.

    Returns one of:
      - "expected_failure"
      - "unexpected_failure"
      - "no_effect"
      - "newly_passing"
    """
    was_predicted_to_fail = scenario_id in ablation.expected_failures
    if was_predicted_to_fail and not passed:
        return "expected_failure"
    if not was_predicted_to_fail and not passed:
        return "unexpected_failure"
    if was_predicted_to_fail and passed:
        return "newly_passing"
    return "no_effect"
