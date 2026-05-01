"""Pipeline feature toggles for ablation runs.

A ``PipelineFeatures`` is a set of booleans that turn individual
cognitive components *fully* off. "Fully off" means three things —
any deviation muddies ablation results:

1. **No state**: the component does not record anything. No events, no
   loops, no observations, no defense logs.
2. **No retrieval**: every read returns an empty result.
3. **No prompt injection**: the generator sees no section for the
   component.

These toggles are applied once at pipeline construction. Live session
flips are not supported — reflecting a component between on and off
would leave asymmetric state behind and invalidate the run.

Default is all-on. Disable only in ablation experiments, not in
production.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineFeatures:
    """Which cognitive components are active in a pipeline instance."""

    relationship_memory: bool = True
    inner_dialogue: bool = True
    defense: bool = True
    semantic_memory: bool = True
    life_history_context: bool = True

    def disabled_labels(self) -> list[str]:
        """Names of components that are currently disabled (for reports)."""
        labels = []
        if not self.relationship_memory:
            labels.append("relationship_memory")
        if not self.inner_dialogue:
            labels.append("inner_dialogue")
        if not self.defense:
            labels.append("defense")
        if not self.semantic_memory:
            labels.append("semantic_memory")
        if not self.life_history_context:
            labels.append("life_history_context")
        return labels

    def is_baseline(self) -> bool:
        """True if every component is enabled (the all-on baseline)."""
        return not self.disabled_labels()
