"""Forward emotional modeling — anticipation engine.

Predicts what's coming before processing the current message and
pre-adjusts modulators. Pure heuristics, NO LLM calls.

Like feeling dread when you see your boss's name in an email —
before you read it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.types import (
    Anticipation,
    ModulatorState,
    PersonProfile,
    TopicProfile,
    UnresolvedItem,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRE_SHIFT_SCALE = 0.3       # pre-shifts apply at 30% intensity
CONFIDENCE_GATE = 0.3       # below this, no pre-shift applied
TOPIC_TRAJECTORY_WINDOW = 3  # look at last N messages for topic buildup


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class AnticipationEngine:
    """Predicts emotional trajectory from context. Zero LLM calls."""

    def predict(
        self,
        recent_messages: list[str],
        person_profile: PersonProfile,
        topic_profiles: dict[str, TopicProfile],
        current_state: ModulatorState,
        unresolved_items: list[UnresolvedItem] | None = None,
        current_time: datetime | None = None,
    ) -> Anticipation:
        """Run all heuristics and merge into a single prediction.

        Each heuristic produces (topics, tone, shifts, confidence, basis).
        The highest-confidence heuristic wins as the primary prediction,
        but all shifts are merged additively.
        """
        now = current_time or datetime.now(timezone.utc)
        unresolved = unresolved_items or []

        candidates: list[tuple[list[str], str, dict[str, float], float, str]] = []

        # 1. Sensitive topic trajectory
        result = self._topic_trajectory(recent_messages, topic_profiles)
        if result:
            candidates.append(result)

        # 2. Person behavioral patterns (volatility / trust)
        result = self._person_patterns(recent_messages, person_profile, current_state)
        if result:
            candidates.append(result)

        # 3. Unresolved item aging
        result = self._unresolved_aging(unresolved, now)
        if result:
            candidates.append(result)

        # 4. Temporal patterns
        result = self._temporal_patterns(person_profile, now)
        if result:
            candidates.append(result)

        if not candidates:
            return Anticipation(
                predicted_topics=[],
                predicted_emotional_tone="neutral",
                modulator_pre_shifts={},
                confidence=0.0,
                basis="no heuristic fired",
            )

        # Primary prediction = highest confidence candidate
        candidates.sort(key=lambda c: c[3], reverse=True)
        primary = candidates[0]

        # Merge all shifts additively
        merged_shifts: dict[str, float] = {}
        for _, _, shifts, conf, _ in candidates:
            if conf >= CONFIDENCE_GATE:
                for mod, delta in shifts.items():
                    merged_shifts[mod] = merged_shifts.get(mod, 0.0) + delta

        return Anticipation(
            predicted_topics=primary[0],
            predicted_emotional_tone=primary[1],
            modulator_pre_shifts=merged_shifts,
            confidence=primary[3],
            basis=primary[4],
        )

    def apply_pre_shift(
        self,
        state: ModulatorState,
        anticipation: Anticipation,
    ) -> None:
        """Apply predicted shifts at 30% intensity, gated by confidence."""
        if anticipation.confidence < CONFIDENCE_GATE:
            return

        for modulator, delta in anticipation.modulator_pre_shifts.items():
            if not hasattr(state, modulator):
                continue
            current = getattr(state, modulator)
            shifted = _clamp(current + delta * PRE_SHIFT_SCALE)
            setattr(state, modulator, shifted)

    # ------------------------------------------------------------------
    # Heuristics
    # ------------------------------------------------------------------

    def _topic_trajectory(
        self,
        recent_messages: list[str],
        topic_profiles: dict[str, TopicProfile],
    ) -> tuple[list[str], str, dict[str, float], float, str] | None:
        """Detect sensitive topic buildup in recent messages.

        If charged/avoidant topics appear in >=2 of the last 3 messages,
        predict escalation.
        """
        if not recent_messages or not topic_profiles:
            return None

        window = recent_messages[-TOPIC_TRAJECTORY_WINDOW:]
        sensitive = {
            t: tp for t, tp in topic_profiles.items()
            if tp.emotional_charge >= 0.5 or tp.avoidance
        }
        if not sensitive:
            return None

        # Count how many messages mention sensitive topics (simple substring)
        topic_hits: dict[str, int] = {}
        for topic in sensitive:
            lower_topic = topic.lower()
            count = sum(1 for msg in window if lower_topic in msg.lower())
            if count > 0:
                topic_hits[topic] = count

        if not topic_hits:
            return None

        # Need at least 2 mentions across the window
        total_mentions = sum(topic_hits.values())
        if total_mentions < 2:
            return None

        # Pick the most-mentioned sensitive topic
        top_topic = max(topic_hits, key=topic_hits.get)  # type: ignore[arg-type]
        tp = sensitive[top_topic]

        # Confidence scales with charge and mention count
        confidence = _clamp(0.3 + tp.emotional_charge * 0.3 + min(total_mentions, 4) * 0.05)

        tone = "tense" if tp.avoidance or tp.conflict_count > 0 else "charged"
        shifts: dict[str, float] = {
            "arousal": 0.10,
            "certainty": -0.05,
        }
        if tp.avoidance:
            shifts["valence"] = -0.05

        return (
            [top_topic],
            tone,
            shifts,
            confidence,
            f"sensitive topic '{top_topic}' appeared {total_mentions}x in last {len(window)} messages",
        )

    def _person_patterns(
        self,
        recent_messages: list[str],
        person: PersonProfile,
        current_state: ModulatorState,
    ) -> tuple[list[str], str, dict[str, float], float, str] | None:
        """Predict based on person's known emotional patterns.

        High volatility + low trust = expect conflict.
        High volatility after calm stretch = brace for swing.
        """
        if person.interaction_count < 3:
            return None  # not enough history to pattern-match

        shifts: dict[str, float] = {}
        topics: list[str] = []
        basis_parts: list[str] = []

        # High volatility + low trust → expect conflict
        if person.emotional_volatility > 0.6 and person.trust < 0.4:
            confidence = _clamp(0.3 + person.emotional_volatility * 0.2)
            shifts["arousal"] = 0.08
            shifts["valence"] = -0.05
            topics.append("conflict")
            basis_parts.append(
                f"high volatility ({person.emotional_volatility:.2f}) + low trust ({person.trust:.2f})"
            )
            return (topics, "guarded", shifts, confidence, "; ".join(basis_parts))

        # High volatility + current calm → brace for swing
        if person.emotional_volatility > 0.6 and current_state.arousal < 0.4:
            confidence = _clamp(0.25 + person.emotional_volatility * 0.15)
            shifts["arousal"] = 0.05
            shifts["certainty"] = -0.03
            basis_parts.append(
                f"volatile person ({person.emotional_volatility:.2f}) in calm state"
            )
            return ([], "anticipatory", shifts, confidence, "; ".join(basis_parts))

        # Known stress response patterns
        if person.stress_response == "lashes_out" and current_state.arousal > 0.6:
            confidence = 0.35
            shifts["arousal"] = 0.05
            shifts["valence"] = -0.03
            return (
                ["conflict"],
                "bracing",
                shifts,
                confidence,
                f"person lashes out under stress, arousal already {current_state.arousal:.2f}",
            )

        return None

    def _unresolved_aging(
        self,
        unresolved: list[UnresolvedItem],
        now: datetime,
    ) -> tuple[list[str], str, dict[str, float], float, str] | None:
        """Old, high-intensity unresolved items predict resurfacing.

        Items >48h old with intensity >0.5 are overdue.
        """
        if not unresolved:
            return None

        overdue = []
        for item in unresolved:
            if item.resolved:
                continue
            created = item.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_hours = (now - created).total_seconds() / 3600.0
            if age_hours > 48 and item.intensity > 0.5:
                overdue.append((item, age_hours))

        if not overdue:
            return None

        # Sort by intensity descending — oldest high-intensity item is most likely
        overdue.sort(key=lambda x: x[0].intensity, reverse=True)
        top_item, age_h = overdue[0]

        confidence = _clamp(0.3 + min(age_h / 168, 0.3))  # grows over a week, caps at 0.6

        return (
            [top_item.description],
            "preoccupied",
            {"resolution": 0.05},
            confidence,
            f"unresolved '{top_item.source}' item aged {age_h:.0f}h, intensity {top_item.intensity:.2f}",
        )

    def _temporal_patterns(
        self,
        person: PersonProfile,
        now: datetime,
    ) -> tuple[list[str], str, dict[str, float], float, str] | None:
        """Time-of-week patterns.

        Monday mornings and late nights carry different emotional weight.
        Only fires for people with enough interaction history.
        """
        if person.interaction_count < 5:
            return None

        weekday = now.weekday()  # 0=Monday
        hour = now.hour

        shifts: dict[str, float] = {}

        # Monday morning stress (before 11am)
        if weekday == 0 and hour < 11:
            shifts["arousal"] = 0.05
            shifts["energy"] = -0.03
            return (
                [],
                "monday_stress",
                shifts,
                0.3,
                "Monday morning — anticipating stress patterns",
            )

        # Late night (after 11pm) — lower energy, more vulnerable
        if hour >= 23 or hour < 4:
            shifts["energy"] = -0.03
            shifts["arousal"] = -0.02
            return (
                [],
                "late_night",
                shifts,
                0.3,
                "late night — anticipating fatigue and vulnerability",
            )

        # Friday afternoon (after 3pm) — winding down
        if weekday == 4 and hour >= 15:
            shifts["arousal"] = -0.03
            shifts["energy"] = -0.02
            return (
                [],
                "winding_down",
                shifts,
                0.3,
                "Friday afternoon — anticipating wind-down",
            )

        return None
