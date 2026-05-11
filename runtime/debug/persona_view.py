"""Unified presentation-only persona state view."""

from __future__ import annotations

from typing import Any

from runtime.debug.explain import explain_turn
from runtime.debug.mental_state import mental_health
from runtime.debug.relationship_view import build_relationship_view


_MODULATOR_MEANINGS = {
    "arousal": "activation",
    "valence": "emotional tone",
    "certainty": "confidence",
    "bonding": "relationship warmth",
    "energy": "available energy",
    "resolution": "unresolved tension",
}


def build_persona_view(
    debug: Any | None = None,
    *,
    session: Any | None = None,
    previous_debug: Any | None = None,
    session_key: str = "",
    inactive_reason: str = "",
) -> dict[str, Any]:
    """Return a stable user-facing view of the active persona state.

    The view is read-only. It consumes the supplied DebugState/session objects
    and performs no store lookups, memory writes, or cognitive decisions.
    """
    if debug is None and session is not None:
        debug = getattr(session, "last_debug", None)
    active = session is not None or debug is not None
    if not session_key and session is not None:
        session_key = _session_key_from_session(session)

    relationship = build_relationship_view(debug, previous_debug) if debug is not None else _empty_relationship_view()
    snapshot = _modulator_snapshot(debug, session, relationship)
    label = _emotion_label(debug, session, snapshot)
    appraisal = getattr(debug, "appraisal_frame", None) if debug is not None else None

    return {
        "active": bool(active),
        "session_key": session_key,
        "inactive_reason": "" if active else (inactive_reason or "No active session."),
        "emotions": _emotion_view(label, snapshot, relationship),
        "perception": _perception_view(appraisal),
        "relationship": _relationship_summary(relationship),
        "life": _life_view(debug, relationship),
        "memory": _memory_view(debug, relationship),
        "skills_tools": _skills_tools_view(debug),
        "explanation": explain_turn(debug) if debug is not None else _empty_explanation(),
    }


def _empty_relationship_view() -> dict[str, Any]:
    return {
        "emotion_label": "neutral",
        "strategy": "",
        "strategy_reason": "",
        "modulators": {
            name: {"value": _default_modulator_value(name), "delta": None}
            for name in _MODULATOR_MEANINGS
        },
        "trust": {"value": 0.0, "delta": None},
        "open_loops": [],
        "recent_relationship_events": [],
        "life_influence": {},
        "life_influence_effects": {},
        "memory_used": {
            "long_term_count": 0,
            "relationship_context_used": False,
            "semantic_count": 0,
        },
    }


def _session_key_from_session(session: Any) -> str:
    session_key = str(getattr(session, "session_key", "") or "")
    if session_key:
        return session_key
    rel_key = str(getattr(session, "rel_key", "") or "")
    chat_id = str(getattr(session, "chat_id", "") or "")
    if rel_key and chat_id:
        platform, _, user_id = rel_key.partition(":")
        return f"{platform}:{user_id}:{chat_id}" if user_id else f"{rel_key}:{chat_id}"
    return ""


def _modulator_snapshot(
    debug: Any | None,
    session: Any | None,
    relationship: dict[str, Any],
) -> dict[str, float]:
    raw = getattr(debug, "modulator_snapshot", None) if debug is not None else None
    if not raw and session is not None:
        pipeline = getattr(session, "pipeline", None)
        engine = getattr(pipeline, "engine", None)
        if engine is not None and hasattr(engine, "snapshot"):
            raw = engine.snapshot()
    if not raw:
        raw = {
            name: (relationship.get("modulators", {}).get(name, {}) or {}).get("value")
            for name in _MODULATOR_MEANINGS
        }
    return {
        name: _safe_float(raw.get(name), _default_modulator_value(name))
        for name in _MODULATOR_MEANINGS
    }


def _emotion_label(debug: Any | None, session: Any | None, snapshot: dict[str, float]) -> str:
    label = str(getattr(debug, "emotion_label", "") or "") if debug is not None else ""
    if label:
        return label
    pipeline = getattr(session, "pipeline", None) if session is not None else None
    engine = getattr(pipeline, "engine", None)
    if engine is not None and hasattr(engine, "to_emotion_label"):
        return str(engine.to_emotion_label())
    return _infer_emotion_label(snapshot)


def _emotion_view(
    label: str,
    snapshot: dict[str, float],
    relationship: dict[str, Any],
) -> dict[str, Any]:
    modulators = relationship.get("modulators", {}) or {}
    primary = label or _infer_emotion_label(snapshot)
    secondary = _secondary_emotions(primary, snapshot)
    drivers = _emotion_drivers(snapshot)
    intensity = _emotion_intensity(snapshot)
    confidence = 0.45 if primary == "neutral" else min(0.95, 0.55 + intensity * 0.35)
    return {
        "primary": primary,
        "simple_label": _simple_emotion(primary, snapshot),
        "secondary": secondary,
        "intensity": round(intensity, 3),
        "confidence": round(confidence, 3),
        "mental_health": mental_health(snapshot),
        "drivers": drivers,
        "modulators": {
            name: {
                "value": round(snapshot[name], 6),
                "delta": (modulators.get(name, {}) or {}).get("delta"),
                "level": _level(snapshot[name], inverted=(name == "energy")),
                "meaning": _MODULATOR_MEANINGS[name],
            }
            for name in _MODULATOR_MEANINGS
        },
    }


def _infer_emotion_label(s: dict[str, float]) -> str:
    if s["energy"] < 0.2:
        return "irritable" if s["arousal"] > 0.5 else "exhausted"
    if s["arousal"] > 0.7 and s["valence"] < 0.3:
        return "angry" if s["certainty"] > 0.7 else "fearful"
    if s["arousal"] > 0.7 and s["valence"] > 0.7:
        return "excited"
    if s["valence"] > 0.7 and s["bonding"] > 0.7:
        return "joyful" if s["arousal"] >= 0.4 else "loving"
    if s["valence"] > 0.7:
        return "content"
    if s["valence"] < 0.3 and s["arousal"] < 0.3:
        return "sad"
    if s["certainty"] < 0.3:
        return "anxious" if s["arousal"] > 0.6 else "uncertain"
    if s["valence"] < 0.3 and s["bonding"] < 0.3:
        return "detached"
    return "neutral"


def _simple_emotion(primary: str, s: dict[str, float]) -> str:
    if primary in {"joyful", "loving", "content", "excited"}:
        return "happy"
    if primary in {"fearful", "uncertain"} and s["arousal"] > 0.55:
        return "anxious"
    if primary == "exhausted":
        return "tired"
    return primary or "neutral"


def _secondary_emotions(primary: str, s: dict[str, float]) -> list[str]:
    labels: list[str] = []
    if s["certainty"] < 0.35 and s["arousal"] > 0.55:
        labels.append("anxious")
    if s["valence"] < 0.35 and s["arousal"] < 0.4:
        labels.append("sad")
    if s["valence"] < 0.35 and s["certainty"] > 0.65 and s["arousal"] > 0.6:
        labels.append("angry")
    if s["valence"] > 0.65:
        labels.append("happy")
    if s["energy"] < 0.3:
        labels.append("tired")
    if s["bonding"] < 0.35:
        labels.append("distant")
    if s["bonding"] > 0.7:
        labels.append("warm")
    if s["resolution"] > 0.55:
        labels.append("unresolved")
    simple = _simple_emotion(primary, s)
    return [item for item in dict.fromkeys(labels) if item not in {primary, simple}][:4]


def _emotion_drivers(s: dict[str, float]) -> list[str]:
    drivers: list[str] = []
    if s["arousal"] >= 0.65:
        drivers.append("high activation")
    elif s["arousal"] <= 0.35:
        drivers.append("low activation")
    if s["valence"] >= 0.65:
        drivers.append("positive emotional tone")
    elif s["valence"] <= 0.35:
        drivers.append("negative emotional tone")
    if s["certainty"] <= 0.35:
        drivers.append("low certainty")
    elif s["certainty"] >= 0.7:
        drivers.append("high certainty")
    if s["bonding"] >= 0.65:
        drivers.append("high relationship warmth")
    elif s["bonding"] <= 0.35:
        drivers.append("low relationship warmth")
    if s["energy"] <= 0.35:
        drivers.append("low energy")
    if s["resolution"] >= 0.5:
        drivers.append("unresolved tension")
    return drivers or ["balanced modulator state"]


def _emotion_intensity(s: dict[str, float]) -> float:
    deviations = [
        abs(s["arousal"] - 0.5) * 2,
        abs(s["valence"] - 0.5) * 2,
        abs(s["certainty"] - 0.5) * 2,
        abs(s["bonding"] - 0.5) * 2,
        max(0.0, 1.0 - s["energy"]),
        s["resolution"],
    ]
    return max(0.0, min(1.0, max(deviations)))


def _perception_view(appraisal: Any) -> dict[str, Any]:
    if appraisal is None:
        return {
            "available": False,
            "target": "",
            "social_move": "",
            "intent": "",
            "vulnerability": 0.0,
            "action_need": 0.0,
            "summary": "No social perception was recorded for this turn.",
        }
    target = str(getattr(appraisal, "primary_target", "") or "")
    move = str(getattr(appraisal, "social_move", "") or "")
    intent = str(getattr(appraisal, "inferred_intent", "") or "")
    vulnerability = _safe_float(getattr(appraisal, "vulnerability", 0.0), 0.0)
    action_need = _safe_float(getattr(appraisal, "action_need", 0.0), 0.0)
    return {
        "available": True,
        "target": target,
        "social_move": move,
        "intent": intent,
        "vulnerability": round(vulnerability, 6),
        "action_need": round(action_need, 6),
        "summary": _perception_summary(target, move, vulnerability, action_need),
    }


def _perception_summary(target: str, move: str, vulnerability: float, action_need: float) -> str:
    if target == "assistant":
        base = f"Read as {move or 'a social move'} directed at Nūr."
    elif target == "external":
        base = f"Read as {move or 'a social move'} about something outside the relationship."
    elif target == "self":
        base = f"Read as {move or 'a social move'} about the user's own state."
    elif target == "shared_problem":
        base = "Read as a shared problem to work on."
    else:
        base = "No clear target was identified."
    if vulnerability >= 0.5:
        base += " Vulnerability is elevated."
    if action_need >= 0.5:
        base += " The turn asks for action."
    return base


def _relationship_summary(view: dict[str, Any]) -> dict[str, Any]:
    mods = view.get("modulators", {}) or {}
    return {
        "strategy": view.get("strategy", ""),
        "strategy_reason": view.get("strategy_reason", ""),
        "trust": view.get("trust", {"value": 0.0, "delta": None}),
        "bonding": mods.get("bonding", {"value": 0.0, "delta": None}),
        "resolution": mods.get("resolution", {"value": 0.0, "delta": None}),
        "open_loop_count": len(view.get("open_loops", []) or []),
        "open_loops": view.get("open_loops", []) or [],
        "recent_events": view.get("recent_relationship_events", []) or [],
    }


def _life_view(debug: Any | None, relationship: dict[str, Any]) -> dict[str, Any]:
    life = getattr(debug, "life_history_context", {}) if debug is not None else {}
    life = life or {}
    influence = relationship.get("life_influence", {}) or {}
    effects = relationship.get("life_influence_effects", {}) or {}
    active_pressures = {
        key: value
        for key, value in influence.items()
        if isinstance(value, (int, float)) and abs(float(value)) >= 0.0005
    }
    return {
        "context_available": bool(life),
        "belief_count": len(life.get("beliefs") or []),
        "drive_count": len(life.get("drives") or []),
        "recent_evolution_count": len(life.get("recent_evolution") or []),
        "active_pressures": active_pressures,
        "effects": effects,
    }


def _memory_view(debug: Any | None, relationship: dict[str, Any]) -> dict[str, Any]:
    memory_used = relationship.get("memory_used", {}) or {}
    retrieved = list(getattr(debug, "retrieved_memories", []) or []) if debug is not None else []
    semantic = list(getattr(debug, "semantic_memories", []) or []) if debug is not None else []
    return {
        "relationship_context_used": bool(memory_used.get("relationship_context_used")),
        "open_loop_count": len(relationship.get("open_loops", []) or []),
        "recent_event_count": len(relationship.get("recent_relationship_events", []) or []),
        "long_term_count": int(memory_used.get("long_term_count") or 0),
        "semantic_count": int(memory_used.get("semantic_count") or 0),
        "long_term_summaries": [
            {
                "summary": str(getattr(item, "summary", "") or ""),
                "activation": round(float(getattr(item, "activation", 0.0)), 3),
                "spike": bool(getattr(item, "spike", False)),
            }
            for item in retrieved[:4]
        ],
        "semantic_summaries": [
            {
                "summary": str(getattr(item, "summary", "") or getattr(item, "topic", "") or ""),
                "kind": str(getattr(item, "kind", "semantic") or "semantic"),
                "score": round(float(getattr(item, "score", 0.0)), 3),
            }
            for item in semantic[:4]
        ],
    }


def _skills_tools_view(debug: Any | None) -> dict[str, Any]:
    skill_context = getattr(debug, "skill_context", {}) if debug is not None else {}
    skill_context = skill_context or {}
    skills = list(skill_context.get("skills") or [])
    tool_trace = getattr(debug, "tool_trace", None) if debug is not None else None
    proposed = list(getattr(tool_trace, "proposed_intents", []) or []) if tool_trace is not None else []
    executed = list(getattr(tool_trace, "executed_results", []) or []) if tool_trace is not None else []
    return {
        "enabled_skill_count": int(skill_context.get("count") or len(skills)),
        "enabled_skills": [
            {
                "id": str(skill.get("id") or ""),
                "name": str(skill.get("name") or skill.get("id") or ""),
                "description": str(skill.get("description") or ""),
            }
            for skill in skills[:5]
            if isinstance(skill, dict)
        ],
        "tools_considered": len(proposed),
        "tools_used": len(executed),
        "tool_names": [str(getattr(result, "tool_name", "") or "") for result in executed[:5]],
        "summary": _tool_summary(tool_trace, proposed, executed),
    }


def _tool_summary(tool_trace: Any, proposed: list[Any], executed: list[Any]) -> str:
    if tool_trace is None:
        return "No tools were considered."
    if executed:
        names = ", ".join(str(getattr(result, "tool_name", "") or "") for result in executed[:3])
        return f"Used {names}."
    if proposed:
        return "Tools were considered but not executed."
    return "No tools were used."


def _empty_explanation() -> dict[str, str]:
    return {
        "interpretation": "No turn has been processed yet.",
        "strategy": "No response strategy has been selected.",
        "state": "No modulator snapshot is available.",
        "memory": "No memory context has been used.",
        "life_history": "No Life History context has influenced this session yet.",
        "tools": "No tools were considered.",
        "limits": "No defense or self-check information is available.",
    }


def _level(value: float, *, inverted: bool = False) -> str:
    if inverted:
        if value <= 0.35:
            return "low"
        if value >= 0.7:
            return "high"
        return "medium"
    if value <= 0.35:
        return "low"
    if value >= 0.65:
        return "high"
    return "medium"


def _default_modulator_value(name: str) -> float:
    return 1.0 if name == "energy" else 0.0 if name == "resolution" else 0.5


def _safe_float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default
