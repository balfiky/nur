"""Presentation-only relationship state view for debug surfaces."""

from __future__ import annotations

from typing import Any


_MODULATORS = ("arousal", "valence", "certainty", "bonding", "energy", "resolution")


def build_relationship_view(debug: Any, previous_debug: Any | None = None) -> dict[str, Any]:
    """Return a stable JSON view of relationship state for UI/channel display.

    This function is deliberately read-only: it uses only the supplied debug
    snapshots, performs no store lookups, and makes no cognitive decisions.
    """
    current_snapshot = _snapshot(debug)
    previous_snapshot = _snapshot(previous_debug) if previous_debug is not None else {}

    return {
        "emotion_label": str(getattr(debug, "emotion_label", "") or ""),
        "strategy": str(getattr(debug, "response_strategy", "") or ""),
        "strategy_reason": _strategy_reason(debug),
        "modulators": {
            name: {
                "value": _round_or_default(current_snapshot.get(name), 0.0),
                "delta": _delta(current_snapshot, previous_snapshot, name),
            }
            for name in _MODULATORS
        },
        "trust": {
            "value": _round_or_default(_trust(debug), 0.0),
            "delta": _trust_delta(debug, previous_debug),
        },
        "open_loops": _relationship_items(debug, "active_loops"),
        "recent_relationship_events": _relationship_items(debug, "recent_events"),
        "life_influence": _life_influence(debug),
        "life_influence_effects": dict(getattr(debug, "life_influence_effects", {}) or {}),
        "memory_used": {
            "long_term_count": len(getattr(debug, "retrieved_memories", []) or []),
            "relationship_context_used": _relationship_context_used(debug),
            "semantic_count": len(getattr(debug, "semantic_memories", []) or []),
        },
    }


def _snapshot(debug: Any | None) -> dict[str, float]:
    if debug is None:
        return {}
    raw = getattr(debug, "modulator_snapshot", {}) or {}
    return {str(key): _safe_float(value, 0.0) for key, value in raw.items()}


def _strategy_reason(debug: Any) -> str:
    trace = getattr(debug, "strategy_trace", None)
    if trace is None:
        return ""
    matched_rule = str(getattr(trace, "matched_rule", "") or "")
    if not matched_rule:
        return ""
    return matched_rule


def _trust(debug: Any | None) -> float | None:
    if debug is None:
        return None
    profile = getattr(debug, "person_profile", None)
    if profile is None:
        return None
    return _safe_float(getattr(profile, "trust", None), 0.0)


def _trust_delta(debug: Any, previous_debug: Any | None) -> float | None:
    if previous_debug is None:
        return None
    current = _trust(debug)
    previous = _trust(previous_debug)
    if current is None or previous is None:
        return None
    return _round_delta(current - previous)


def _delta(
    current_snapshot: dict[str, float],
    previous_snapshot: dict[str, float],
    key: str,
) -> float | None:
    if key not in current_snapshot or key not in previous_snapshot:
        return None
    return _round_delta(current_snapshot[key] - previous_snapshot[key])


def _relationship_items(debug: Any, field_name: str) -> list[dict[str, Any]]:
    context = getattr(debug, "relationship_context", None)
    if context is None:
        return []
    items = getattr(context, field_name, []) or []
    result: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "to_dict"):
            result.append(item.to_dict())
        elif isinstance(item, dict):
            result.append(dict(item))
    return result


def _relationship_context_used(debug: Any) -> bool:
    context = getattr(debug, "relationship_context", None)
    if context is None:
        return False
    is_empty = getattr(context, "is_empty", None)
    if callable(is_empty):
        return not bool(is_empty())
    return True


def _life_influence(debug: Any) -> dict[str, float]:
    influence = getattr(debug, "life_influence", None)
    if influence is None:
        return {}
    if hasattr(influence, "to_dict"):
        return influence.to_dict()
    if isinstance(influence, dict):
        return dict(influence)
    return {}


def _round_or_default(value: Any, default: float) -> float:
    return round(_safe_float(value, default), 6)


def _round_delta(value: float) -> float:
    if abs(value) < 1e-9:
        return 0.0
    return round(value, 6)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
