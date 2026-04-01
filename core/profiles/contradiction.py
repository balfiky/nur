"""Unified contradiction detection for self and others.

Compares recent behavior against profile expectations. When divergence
exceeds threshold, generates a contradiction signal. The same mechanism
runs identically for self-profile and person profiles.

For others: "That's not like you."
For self: "I'm not acting like myself today."

No LLM calls. Comparison math only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config.loader import get_config
from core.profiles.base import Observation, ProfileStore

# ---------------------------------------------------------------------------
# Config-driven constants (loaded from config/profiles_schema.yaml)
# ---------------------------------------------------------------------------

_cfg = get_config().contradiction
CONTRADICTION_THRESHOLD = _cfg.threshold
RECENT_WINDOW = _cfg.recent_window


# ---------------------------------------------------------------------------
# Contradiction signal
# ---------------------------------------------------------------------------

@dataclass
class ContradictionSignal:
    """A detected contradiction between expected and actual behavior."""
    entity_id: str
    trait: str
    expected: float
    observed: float
    magnitude: float  # abs(expected - observed)
    context: str = ""

    @property
    def is_self(self) -> bool:
        return self.entity_id == "__self__"

    @property
    def description(self) -> str:
        direction = "higher" if self.observed > self.expected else "lower"
        if self.is_self:
            return (
                f"Self-contradiction: '{self.trait}' observed {direction} "
                f"than model ({self.observed:.2f} vs expected {self.expected:.2f})"
            )
        return (
            f"Contradiction for '{self.entity_id}': '{self.trait}' observed "
            f"{direction} than expected ({self.observed:.2f} vs {self.expected:.2f})"
        )


# ---------------------------------------------------------------------------
# Contradiction detector
# ---------------------------------------------------------------------------

@dataclass
class ContradictionResult:
    """Result of running contradiction detection."""
    entity_id: str
    contradictions: list[ContradictionSignal] = field(default_factory=list)
    overall_divergence: float = 0.0  # mean divergence across all traits

    @property
    def has_contradictions(self) -> bool:
        return len(self.contradictions) > 0


class ContradictionDetector:
    """Detects behavioral contradictions for any entity type.

    Unified: works identically for self and others. The only input is
    an entity_id and the expected trait scores (from the profile).
    """

    def __init__(self, store: ProfileStore) -> None:
        self._store = store

    def detect(
        self,
        entity_id: str,
        expected_traits: dict[str, float],
        window: int = RECENT_WINDOW,
    ) -> ContradictionResult:
        """Compare recent observations against expected trait profile.

        For each trait that appears in both the profile and recent observations,
        compute divergence. Flag traits where divergence exceeds threshold.

        Returns ContradictionResult with individual signals and overall divergence.
        """
        if not expected_traits:
            return ContradictionResult(entity_id=entity_id)

        recent = self._store.get_observations(entity_id, limit=window)
        if not recent:
            return ContradictionResult(entity_id=entity_id)

        # Aggregate recent observations by trait
        recent_by_trait: dict[str, list[float]] = {}
        recent_contexts: dict[str, str] = {}
        for obs in recent:
            recent_by_trait.setdefault(obs.trait, []).append(obs.value)
            if obs.context:
                recent_contexts[obs.trait] = obs.context

        # Compare against expectations
        contradictions: list[ContradictionSignal] = []
        all_divergences: list[float] = []

        for trait, expected_score in expected_traits.items():
            if trait not in recent_by_trait:
                continue

            observed_values = recent_by_trait[trait]
            observed_avg = sum(observed_values) / len(observed_values)
            divergence = abs(expected_score - observed_avg)
            all_divergences.append(divergence)

            if divergence >= CONTRADICTION_THRESHOLD:
                signal = ContradictionSignal(
                    entity_id=entity_id,
                    trait=trait,
                    expected=expected_score,
                    observed=observed_avg,
                    magnitude=divergence,
                    context=recent_contexts.get(trait, ""),
                )
                contradictions.append(signal)

        overall = (
            sum(all_divergences) / len(all_divergences)
            if all_divergences
            else 0.0
        )

        return ContradictionResult(
            entity_id=entity_id,
            contradictions=contradictions,
            overall_divergence=overall,
        )
