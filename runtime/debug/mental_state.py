"""Shared mental-state presentation helpers.

These functions are read-only. Telegram, web admin, and debug surfaces should
derive mental-health labels from the same snapshot math.
"""

from __future__ import annotations

from typing import Any


MODULATOR_NAMES = ("arousal", "valence", "certainty", "bonding", "energy", "resolution")


def normalize_snapshot(snapshot: dict[str, Any] | None) -> dict[str, float]:
    raw = snapshot or {}
    return {
        "arousal": _safe_float(raw.get("arousal"), 0.5),
        "valence": _safe_float(raw.get("valence"), 0.5),
        "certainty": _safe_float(raw.get("certainty"), 0.5),
        "bonding": _safe_float(raw.get("bonding"), 0.5),
        "energy": _safe_float(raw.get("energy"), 1.0),
        "resolution": _safe_float(raw.get("resolution"), 0.0),
    }


def stability_score(snapshot: dict[str, Any] | None) -> int:
    s = normalize_snapshot(snapshot)
    strain = (
        abs(s["arousal"] - 0.5) * 0.7
        + abs(s["valence"] - 0.5) * 0.9
        + (1.0 - s["certainty"]) * 0.5
        + (1.0 - s["energy"]) * 0.8
        + s["resolution"] * 0.9
    )
    return max(0, min(100, round(100 * (1.0 - min(1.0, strain / 2.2)))))


def health_label(score: int) -> str:
    if score >= 80:
        return "stable"
    if score >= 60:
        return "strained"
    if score >= 40:
        return "distressed"
    return "critical"


def mental_health(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    score = stability_score(snapshot)
    return {
        "score": score,
        "label": health_label(score),
    }


def bar(value: float, width: int = 10) -> str:
    filled = max(0, min(width, int(value * width)))
    return "█" * filled + "░" * (width - filled)


def _safe_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))
