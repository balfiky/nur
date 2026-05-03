"""Plain-language explanations for a completed pipeline turn."""

from __future__ import annotations

from typing import Any


def explain_turn(debug: Any) -> dict[str, str]:
    """Summarize the most user-legible parts of a DebugState."""
    appraisal = getattr(debug, "appraisal_frame", None)
    relationship = getattr(debug, "relationship_context", None)
    tool_trace = getattr(debug, "tool_trace", None)
    life = getattr(debug, "life_history_context", {}) or {}
    influence = getattr(debug, "life_influence", None)
    life_effects = getattr(debug, "life_influence_effects", {}) or {}
    defense = getattr(debug, "defense_activation", None)
    self_check_issues = getattr(debug, "self_check_issues", []) or []

    return {
        "interpretation": _interpretation(appraisal),
        "strategy": _strategy(debug, appraisal),
        "state": _state(getattr(debug, "baseline_shift_applied", {}) or {}, getattr(debug, "modulator_snapshot", {}) or {}),
        "memory": _memory(relationship, getattr(debug, "retrieved_memories", []) or []),
        "life_history": _life_history(life, influence, life_effects),
        "tools": _tools(tool_trace),
        "limits": _limits(defense, self_check_issues),
    }


def _interpretation(appraisal: Any) -> str:
    if appraisal is None:
        return "I did not record a social interpretation for this turn."
    target = getattr(appraisal, "primary_target", "unknown")
    move = getattr(appraisal, "social_move", "inform")
    if target == "external":
        return f"I interpreted this as {move} about something outside the relationship."
    if target == "assistant":
        return f"I interpreted this as {move} directed at me."
    if target == "self":
        return f"I interpreted this as {move} about the user's own state."
    if target == "shared_problem":
        return f"I interpreted this as a request about a shared problem."
    return f"I interpreted this as {move} with no clear target."


def _strategy(debug: Any, appraisal: Any) -> str:
    strategy = getattr(debug, "response_strategy", "") or ""
    if not strategy:
        return "No explicit response strategy was selected."
    trace = getattr(debug, "strategy_trace", None)
    if trace is not None and getattr(trace, "matched_rule", ""):
        return f"I selected {strategy} because the {trace.matched_rule} rule matched."
    if appraisal is not None and getattr(appraisal, "vulnerability", 0.0) >= 0.5:
        return f"I selected {strategy} because vulnerability was high."
    if appraisal is not None and getattr(appraisal, "inferred_intent", "") == "seek_action":
        return f"I selected {strategy} because the turn asked for action."
    return f"I selected {strategy} from the current appraisal and state."


def _state(baseline_shift: dict[str, float], snapshot: dict[str, float]) -> str:
    if not snapshot:
        return "No modulator snapshot was recorded."
    shifted = [name for name, value in baseline_shift.items() if abs(float(value or 0.0)) > 0.001]
    arousal = snapshot.get("arousal")
    valence = snapshot.get("valence")
    if shifted:
        return f"Baseline shifted for {', '.join(shifted[:3])}; arousal is {arousal:.2f} and valence is {valence:.2f}."
    return f"Arousal is {arousal:.2f}, valence is {valence:.2f}, and energy is {snapshot.get('energy', 0.0):.2f}."


def _memory(relationship: Any, retrieved_memories: list[Any]) -> str:
    pieces: list[str] = []
    if relationship is not None:
        loops = int(getattr(relationship, "open_loop_count", 0) or 0)
        pieces.append(f"Relationship memory contributed {loops} open loop(s).")
    else:
        pieces.append("No relationship memory context was used.")
    if retrieved_memories:
        pieces.append(f"Long-term memory returned {len(retrieved_memories)} item(s).")
    else:
        pieces.append("No long-term memories were retrieved.")
    return " ".join(pieces)


def _tools(tool_trace: Any) -> str:
    if tool_trace is None:
        return "No tools were considered."
    executed = getattr(tool_trace, "executed_results", []) or []
    if not executed:
        proposed = getattr(tool_trace, "proposed_intents", []) or []
        return "Tools were considered but none were used." if proposed else "No tools were used."
    names = ", ".join(result.tool_name for result in executed[:4])
    suffix = " and more" if len(executed) > 4 else ""
    return f"Used {names}{suffix}; results were summarized into the response context."


def _life_history(life: dict[str, Any], influence: Any, effects: dict[str, Any]) -> str:
    beliefs = len(life.get("beliefs") or [])
    drives = len(life.get("drives") or [])
    evolution = len(life.get("recent_evolution") or [])
    if beliefs == 0 and drives == 0 and evolution == 0:
        return "No Life History context contributed to this turn."
    parts = []
    if beliefs:
        parts.append(f"{beliefs} belief(s)")
    if drives:
        parts.append(f"{drives} drive shift(s)")
    if evolution:
        parts.append(f"{evolution} recent evolution event(s)")
    pressure = ""
    if influence is not None and not getattr(influence, "is_neutral", True):
        active = [
            name.replace("_pressure", "")
            for name, value in influence.to_dict().items()
            if abs(float(value)) > 0.001
        ]
        if active:
            pressure = f" Active pressure: {', '.join(active[:3])}."
    effect = " It did not change deterministic policy." if not effects else " It made a domain-clamped deterministic adjustment."
    return "Life History contributed " + ", ".join(parts) + "." + pressure + effect


def _limits(defense: Any, self_check_issues: list[str]) -> str:
    if defense is None and not self_check_issues:
        return "No defense or self-check correction fired."
    parts: list[str] = []
    if defense is not None:
        parts.append(f"Defense activated: {getattr(defense, 'defense_type', 'unknown')}.")
    if self_check_issues:
        parts.append("Self-check flagged: " + "; ".join(str(item) for item in self_check_issues[:3]) + ".")
    return " ".join(parts)
