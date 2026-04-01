"""Self profile — the AI's model of itself.

Same mechanism as person profiles turned inward. Strengths, flaws, and
triggers are not declared — they emerge from accumulated behavioral
observations. The self-profile at conversation 500 is different from
conversation 1.

No LLM calls. Database reads + math.
"""

from __future__ import annotations

import time

from config.loader import get_config
from core.types import DefenseEvent, SelfProfile
from core.profiles.base import Observation, ProfileStore

# ---------------------------------------------------------------------------
# Config-driven constants (loaded from config/profiles_schema.yaml)
# ---------------------------------------------------------------------------

_cfg = get_config().self_model
SELF_ENTITY_ID = _cfg.entity_id
STRENGTH_THRESHOLD = _cfg.strength_threshold
FLAW_THRESHOLD = _cfg.flaw_threshold
TRIGGER_THRESHOLD = _cfg.trigger_threshold
DISSONANCE_WINDOW = _cfg.dissonance_window


class SelfProfileManager:
    """Manages the AI's self-model using the same profiling mechanism as others."""

    def __init__(self, store: ProfileStore) -> None:
        self._store = store
        self._entity_id = SELF_ENTITY_ID

    # ------------------------------------------------------------------
    # Observation recording
    # ------------------------------------------------------------------

    def record_behavior(
        self,
        trait: str,
        value: float,
        context: str = "",
    ) -> None:
        """Record an observation about the AI's own behavior.

        Called after each response — what traits did the system exhibit?
        e.g., ("blunt", 0.8, "high certainty response to sensitive topic")
        """
        obs = Observation(
            entity_id=self._entity_id,
            trait=trait,
            value=value,
            context=context,
        )
        self._store.record_observation(obs)

    # ------------------------------------------------------------------
    # Profile extraction
    # ------------------------------------------------------------------

    def get_profile(self) -> SelfProfile:
        """Build the current self-profile from accumulated observations.

        Strengths, flaws, and triggers emerge from pattern extraction —
        not from declaration.
        """
        trait_scores = self._store.extract_traits(self._entity_id)
        if not trait_scores:
            return SelfProfile()

        observed_traits = list(trait_scores.keys())
        strengths = [t for t, s in trait_scores.items() if s >= STRENGTH_THRESHOLD]
        flaws = [t for t, s in trait_scores.items() if self._is_flaw(t, s)]
        triggers = self._extract_triggers()
        dissonance = self._compute_dissonance(trait_scores)

        maturity = self._compute_maturity(trait_scores, flaws)
        defense_log = self._load_defense_log()

        return SelfProfile(
            observed_traits=observed_traits,
            strengths=strengths,
            flaws=flaws,
            triggers=triggers,
            dissonance=dissonance,
            maturity_score=maturity,
            defense_log=defense_log,
        )

    def get_trait_scores(self) -> dict[str, float]:
        """Get raw trait scores for the self-model."""
        return self._store.extract_traits(self._entity_id)

    def get_expected_traits(self) -> dict[str, float]:
        """Return expected trait scores for contradiction comparison.

        Same interface as PersonProfileManager — unified mechanism.
        """
        return self.get_trait_scores()

    # ------------------------------------------------------------------
    # Dissonance detection
    # ------------------------------------------------------------------

    def _compute_dissonance(self, model_traits: dict[str, float]) -> float:
        """Compare recent behavior against the self-model.

        Dissonance = mean absolute gap between recent observations
        and the model's expected scores. High dissonance means
        "I'm not acting like myself."
        """
        recent = self._store.get_observations(
            self._entity_id, limit=DISSONANCE_WINDOW
        )
        if not recent or not model_traits:
            return 0.0

        gaps: list[float] = []
        for obs in recent:
            if obs.trait in model_traits:
                expected = model_traits[obs.trait]
                gap = abs(obs.value - expected)
                gaps.append(gap)

        return sum(gaps) / len(gaps) if gaps else 0.0

    # ------------------------------------------------------------------
    # Trait classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_flaw(trait: str, score: float) -> bool:
        """Determine if a trait at this score constitutes a flaw.

        Negative traits (blunt, impatient, avoidant) with high scores = flaws.
        Positive traits with low scores are NOT flaws — they're just not strengths.
        """
        if trait in _cfg.negative_traits and score >= FLAW_THRESHOLD:
            return True
        return False

    def _extract_triggers(self) -> list[str]:
        """Extract topics/situations that consistently cause spikes.

        A trigger is a context that appears repeatedly with high-intensity
        observations.
        """
        observations = self._store.get_observations(self._entity_id, limit=100)
        context_intensities: dict[str, list[float]] = {}
        for obs in observations:
            if obs.context:
                context_intensities.setdefault(obs.context, []).append(obs.value)

        triggers = []
        for context, values in context_intensities.items():
            if len(values) >= 3:  # need repeated pattern
                avg = sum(values) / len(values)
                if avg >= TRIGGER_THRESHOLD:
                    triggers.append(context)

        return triggers

    # ------------------------------------------------------------------
    # Maturity derivation
    # ------------------------------------------------------------------

    def _compute_maturity(
        self,
        trait_scores: dict[str, float],
        flaws: list[str],
    ) -> float:
        """Derive maturity_score from persisted evidence.

        Inputs:
        - observation count (more self-aware over time)
        - diversity of recognized flaws
        - defense event count (awareness of defense patterns)
        """
        obs_count = self._store.observation_count(self._entity_id)
        if obs_count == 0:
            return 0.0

        # Base from observation count: caps at 0.3 around 30 observations
        base = min(0.3, obs_count * 0.01)

        # Bonus for recognizing flaws (diverse self-awareness)
        flaw_bonus = min(0.2, len(flaws) * 0.05)

        # Defense awareness: having defense events means the system
        # is encountering and recording emotional pressure
        defense_count = self._store.defense_event_count()
        defense_bonus = min(0.2, defense_count * 0.02)

        return min(1.0, base + flaw_bonus + defense_bonus)

    # ------------------------------------------------------------------
    # Defense event persistence
    # ------------------------------------------------------------------

    def persist_defense_event(self, event: DefenseEvent) -> None:
        """Persist a defense event to the profile store."""
        self._store.record_defense_event(
            timestamp=event.timestamp,
            defense_type=event.defense_type,
            raw_intensity=event.raw_intensity,
            expressed_intensity=event.expressed_intensity,
            suppression_delta=event.suppression_delta,
        )

    def _load_defense_log(self) -> list[DefenseEvent]:
        """Load defense events from persistent storage."""
        rows = self._store.get_defense_events(limit=50)
        return [
            DefenseEvent(
                timestamp=r["timestamp"],
                defense_type=r["defense_type"],
                raw_intensity=r["raw_intensity"],
                expressed_intensity=r["expressed_intensity"],
                suppression_delta=r["suppression_delta"],
            )
            for r in rows
        ]

    @property
    def observation_count(self) -> int:
        return self._store.observation_count(self._entity_id)
