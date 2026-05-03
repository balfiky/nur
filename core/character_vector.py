"""Deterministic character-state vector assembly.

The vector is the internal integrity surface: downstream decisions should be
checked against this state instead of external policy gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
import hashlib
import json
from typing import Any

from core.types import PipelineContext


@dataclass(frozen=True)
class DriveSnapshot:
    name: str
    value: float
    baseline: float = 0.5
    delta: float = 0.0
    updated_at: float | None = None


@dataclass(frozen=True)
class WeightedBelief:
    key: str
    statement: str
    confidence: float
    evidence: str = ""


@dataclass(frozen=True)
class SelfTrait:
    name: str
    value: float = 0.0
    pattern: str = ""
    inverse: str = ""


@dataclass(frozen=True)
class ConflictResolution:
    state: str
    drives: tuple[str, ...]
    winner: str = ""
    loser_pressure_multiplier: float = 1.0


@dataclass(frozen=True)
class CharacterVector:
    short_term: list[str] = field(default_factory=list)
    relational: list[str] = field(default_factory=list)
    semantic: list[str] = field(default_factory=list)
    modulator: dict[str, float] = field(default_factory=dict)
    beliefs: list[WeightedBelief] = field(default_factory=list)
    drives: dict[str, DriveSnapshot] = field(default_factory=dict)
    formative_experiences: list[dict[str, Any]] = field(default_factory=list)
    self_traits: list[SelfTrait] = field(default_factory=list)
    skill_state: dict[str, Any] = field(default_factory=dict)
    conflict_resolutions: list[ConflictResolution] = field(default_factory=list)
    dispositions: list[str] = field(default_factory=list)
    trace_id: str = ""


def assemble_character_vector(ctx: PipelineContext) -> CharacterVector:
    """Assemble a pure deterministic vector from already-loaded context."""
    life = ctx.life_history_context if isinstance(ctx.life_history_context, dict) else {}
    beliefs = [
        WeightedBelief(
            key=str(item.get("key") or item.get("subject") or "belief"),
            statement=str(item.get("statement") or item.get("after_state") or ""),
            confidence=_clamp_float(item.get("confidence"), 0.0),
            evidence=str(item.get("evidence") or ""),
        )
        for item in _as_dicts(life.get("beliefs"))
    ]

    drive_rows = _as_dicts(life.get("all_drives")) or _as_dicts(life.get("drives"))
    drives: dict[str, DriveSnapshot] = {}
    for row in drive_rows:
        name = str(row.get("name") or "").strip().lower()
        if not name:
            continue
        baseline = _clamp_float(row.get("baseline"), 0.5)
        value = _clamp_float(row.get("value"), baseline)
        drives[name] = DriveSnapshot(
            name=name,
            value=value,
            baseline=baseline,
            delta=_safe_float(row.get("delta"), value - baseline),
            updated_at=_optional_float(row.get("updated_at")),
        )

    self_traits = [
        SelfTrait(
            name=str(item.get("trait") or item.get("subject") or item.get("name") or ""),
            value=_safe_float(item.get("value") or item.get("confidence"), 0.0),
            pattern=str(item.get("pattern") or ""),
            inverse=str(item.get("inverse") or ""),
        )
        for item in _as_dicts(life.get("self_traits"))
        if str(item.get("trait") or item.get("subject") or item.get("name") or "")
    ]

    relational = []
    if ctx.relationship_context is not None:
        if ctx.relationship_context.summary:
            relational.append(ctx.relationship_context.summary)
        relational.extend(loop.description for loop in ctx.relationship_context.active_loops)
        relational.extend(event.summary for event in ctx.relationship_context.recent_events)

    vector = CharacterVector(
        short_term=[
            entry.event.event_type.value for entry in ctx.short_term_history
        ],
        relational=relational,
        semantic=[entry.summary for entry in ctx.semantic_memories],
        modulator=dict(ctx.modulator_snapshot or {}),
        beliefs=beliefs,
        drives=drives,
        formative_experiences=_as_dicts(life.get("recent_evolution")),
        self_traits=self_traits,
        skill_state=ctx.skill_context if isinstance(ctx.skill_context, dict) else {},
        conflict_resolutions=_resolve_drive_conflicts(drives),
        dispositions=[
            str(item) for item in (life.get("dispositions") or []) if str(item).strip()
        ],
    )
    trace_id = _stable_hash(vector)
    return replace(vector, trace_id=trace_id)


def format_character_vector_prompt(vector: CharacterVector) -> str:
    """Return a compact prompt block for the generator."""
    content: list[str] = []
    for disposition in vector.dispositions[:7]:
        content.append(f"- Disposition: {disposition}")
    for belief in vector.beliefs[:7]:
        if belief.statement:
            content.append(f"- Belief[{belief.key}, {belief.confidence:.2f}]: {belief.statement}")
    for drive in sorted(vector.drives.values(), key=lambda item: abs(item.delta), reverse=True)[:7]:
        content.append(f"- Drive[{drive.name}]: {drive.value:.2f} ({drive.delta:+.2f})")
    for experience in vector.formative_experiences[:5]:
        label = str(experience.get("domain") or "experience")
        subject = str(experience.get("subject") or "").strip()
        after_state = str(experience.get("after_state") or "").strip()
        reason = str(experience.get("reason") or "").strip()
        detail = after_state or reason
        if not detail:
            continue
        topic = f"{label}/{subject}" if subject else label
        content.append(f"- Formative[{topic}]: {detail}")
    for memory in vector.semantic[:5]:
        if memory:
            content.append(f"- Semantic: {memory}")
    for relation in vector.relational[:5]:
        if relation:
            content.append(f"- Relational: {relation}")
    for conflict in vector.conflict_resolutions:
        if conflict.state == "ambivalent":
            content.append(f"- Ambivalence: {' vs '.join(conflict.drives)}")
        elif conflict.winner:
            content.append(f"- Drive conflict: {conflict.winner} is dominant over {conflict.drives}")
    skills = vector.skill_state.get("skills") if isinstance(vector.skill_state, dict) else None
    if isinstance(skills, list):
        for skill in skills[:5]:
            if not isinstance(skill, dict):
                continue
            name = str(skill.get("name") or skill.get("id") or "").strip()
            description = str(skill.get("description") or "").strip()
            if name:
                suffix = f" - {description}" if description else ""
                content.append(f"- Skill: {name}{suffix}")
    if not content:
        return ""
    lines = ["## Durable Character State"]
    lines.append("Speak from this state. These are constraints, not decorative flavor.")
    lines.extend(content)
    lines.append("")
    return "\n".join(lines)


def _resolve_drive_conflicts(drives: dict[str, DriveSnapshot]) -> list[ConflictResolution]:
    pairs = (("curiosity", "caution"), ("autonomy", "attachment"))
    results: list[ConflictResolution] = []
    for left, right in pairs:
        if left not in drives or right not in drives:
            continue
        left_value = drives[left].value
        right_value = drives[right].value
        difference = left_value - right_value
        if abs(difference) < 0.1:
            results.append(ConflictResolution(state="ambivalent", drives=(left, right)))
            continue
        winner = left if difference > 0 else right
        dominance = min(1.0, abs(difference))
        results.append(
            ConflictResolution(
                state="resolved",
                drives=(left, right),
                winner=winner,
                loser_pressure_multiplier=1.0 - dominance,
            )
        )
    return results


def _stable_hash(vector: CharacterVector) -> str:
    payload = asdict(vector)
    payload["trace_id"] = ""
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _as_dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _safe_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_float(value: object, fallback: float) -> float:
    return max(0.0, min(1.0, _safe_float(value, fallback)))
