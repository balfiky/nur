"""Deterministic policy influence derived from Life History context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.character_vector import CharacterVector

from core.types import ActionVariables


@dataclass(frozen=True)
class LifeInfluence:
    """Signed pressures derived from Life History drives."""

    curiosity_pressure: float = 0.0
    caution_pressure: float = 0.0
    autonomy_pressure: float = 0.0
    repair_pressure: float = 0.0
    competence_pressure: float = 0.0
    continuity_pressure: float = 0.0
    attachment_pressure: float = 0.0

    def __post_init__(self) -> None:
        for field_name in (
            "curiosity_pressure",
            "caution_pressure",
            "autonomy_pressure",
            "repair_pressure",
            "competence_pressure",
            "continuity_pressure",
            "attachment_pressure",
        ):
            object.__setattr__(
                self,
                field_name,
                _safe_float(getattr(self, field_name), 0.0),
            )

    @property
    def is_neutral(self) -> bool:
        return all(abs(value) < 1e-9 for value in self.to_dict().values())

    def to_dict(self) -> dict[str, float]:
        return {
            "curiosity_pressure": self.curiosity_pressure,
            "caution_pressure": self.caution_pressure,
            "autonomy_pressure": self.autonomy_pressure,
            "repair_pressure": self.repair_pressure,
            "competence_pressure": self.competence_pressure,
            "continuity_pressure": self.continuity_pressure,
            "attachment_pressure": self.attachment_pressure,
        }

    def proactive_gain_for(self, trigger_category: str) -> float:
        """Return a drive-derived multiplier for a proactive trigger category."""
        category = str(trigger_category or "").strip().lower()
        gain = 1.0
        if category in {"curiosity", "exploration", "temporal"}:
            gain += max(0.0, self.curiosity_pressure)
        if category in {"repair", "relationship", "unresolved_item", "emotional_salience"}:
            gain += max(0.0, self.repair_pressure)
        if category in {"attachment", "reach_out", "commitment"}:
            gain += max(0.0, self.attachment_pressure)
        if category in {"competence", "skill_gap", "pending_task"}:
            gain += max(0.0, self.competence_pressure)
        if category in {"continuity", "commitment", "pending_task", "temporal"}:
            gain += max(0.0, self.continuity_pressure)
        if category in {"autonomy", "internal", "temporal"}:
            gain += max(0.0, self.autonomy_pressure)
        gain -= max(0.0, self.caution_pressure) * 0.5
        return max(0.05, gain)


def derive_life_influence(life_history_context: dict[str, Any] | None) -> LifeInfluence:
    """Derive deterministic drive pressure from compact Life History context."""
    if not life_history_context:
        return LifeInfluence()

    pressures: dict[str, float] = {
        "curiosity": 0.0,
        "caution": 0.0,
        "autonomy": 0.0,
        "repair": 0.0,
        "competence": 0.0,
        "continuity": 0.0,
        "attachment": 0.0,
    }
    drive_rows = list(_as_list(life_history_context.get("drives")))
    seen_names = {
        str(drive.get("name") or "").strip().lower()
        for drive in drive_rows
        if isinstance(drive, dict)
    }
    for drive in _as_list(life_history_context.get("all_drives")):
        if not isinstance(drive, dict):
            continue
        name = str(drive.get("name") or "").strip().lower()
        if name and name not in seen_names:
            drive_rows.append(drive)
            seen_names.add(name)

    for drive in drive_rows:
        if not isinstance(drive, dict):
            continue
        name = str(drive.get("name") or "").strip().lower()
        if name not in pressures:
            continue
        delta = _safe_float(drive.get("delta"), 0.0)
        if not delta and drive.get("value") is not None:
            baseline = _safe_float(drive.get("baseline"), 0.5)
            delta = _safe_float(drive.get("value"), baseline) - baseline
        pressures[name] += _safe_float(delta, 0.0)

    return LifeInfluence(
        curiosity_pressure=pressures["curiosity"],
        caution_pressure=pressures["caution"],
        autonomy_pressure=pressures["autonomy"],
        repair_pressure=pressures["repair"],
        competence_pressure=pressures["competence"],
        continuity_pressure=pressures["continuity"],
        attachment_pressure=pressures["attachment"],
    )


def derive_life_influence_from_vector(vector: CharacterVector | None) -> LifeInfluence:
    """Derive signed pressures from the assembled character vector."""
    if vector is None:
        return LifeInfluence()

    def pressure(name: str) -> float:
        drive = vector.drives.get(name)
        if drive is None:
            return 0.0
        delta = drive.delta
        if abs(delta) < 1e-9:
            delta = drive.value - drive.baseline
        return delta

    return LifeInfluence(
        curiosity_pressure=pressure("curiosity"),
        caution_pressure=pressure("caution"),
        autonomy_pressure=pressure("autonomy"),
        repair_pressure=pressure("repair"),
        competence_pressure=pressure("competence"),
        continuity_pressure=pressure("continuity"),
        attachment_pressure=pressure("attachment"),
    )


def apply_life_influence_to_action_variables(
    action_variables: ActionVariables,
    influence: LifeInfluence,
    *,
    read_only_action: bool = False,
) -> ActionVariables:
    """Return action variables with Life History pressure applied."""
    risk_delta = -influence.caution_pressure
    persistence_delta = influence.competence_pressure
    autonomy_delta = influence.autonomy_pressure if read_only_action else min(0.0, influence.autonomy_pressure)

    return ActionVariables(
        risk_tolerance=_clamp(action_variables.risk_tolerance + risk_delta),
        action_urgency=action_variables.action_urgency,
        clarification_threshold=action_variables.clarification_threshold,
        persistence_drive=_clamp(action_variables.persistence_drive + persistence_delta),
        autonomy_bias=_clamp(action_variables.autonomy_bias + autonomy_delta),
    )


def apply_character_vector_to_action_variables(
    action_variables: ActionVariables,
    vector: CharacterVector | None,
    *,
    read_only_action: bool = False,
) -> ActionVariables:
    """Return action variables shaped by the durable character vector."""
    if vector is None:
        return action_variables
    influence = derive_life_influence_from_vector(vector)
    adjusted = apply_life_influence_to_action_variables(
        action_variables,
        influence,
        read_only_action=read_only_action,
    )
    volatility = min(0.25, len(vector.formative_experiences) * 0.02)
    if volatility <= 0:
        return adjusted
    return ActionVariables(
        risk_tolerance=_clamp(adjusted.risk_tolerance - volatility * 0.5),
        action_urgency=adjusted.action_urgency,
        clarification_threshold=_clamp(adjusted.clarification_threshold + volatility),
        persistence_drive=adjusted.persistence_drive,
        autonomy_bias=adjusted.autonomy_bias,
    )


def curiosity_salience_bonus(influence: LifeInfluence, text: str) -> float:
    """Return a small salience bonus for learning-related semantic entries."""
    if influence.curiosity_pressure <= 0:
        return 0.0
    lower = text.lower()
    if any(word in lower for word in ("learn", "learning", "question", "curious", "study", "read")):
        return influence.curiosity_pressure
    return 0.0


def action_variable_deltas(before: ActionVariables, after: ActionVariables) -> dict[str, float]:
    """Return deterministic rounded action-variable deltas for debug traces."""
    return {
        "risk_tolerance_delta": _round_delta(after.risk_tolerance - before.risk_tolerance),
        "persistence_delta": _round_delta(after.persistence_drive - before.persistence_drive),
        "autonomy_delta": _round_delta(after.autonomy_bias - before.autonomy_bias),
    }


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round_delta(value: float) -> float:
    if abs(value) < 1e-9:
        return 0.0
    return round(value, 6)


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
