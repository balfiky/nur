"""Runtime-aware debug API — session-based inspection.

Replaces the old single-pipeline debug model with live session awareness.
All data is read from the SessionManager — no separate pipeline.

Endpoints:
    GET  /sessions                    — list active sessions
    GET  /sessions/{session_key}/debug    — per-session debug view
    POST /sessions/{session_key}/reset    — evict (digest + persist) a session
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable

from fastapi import Depends, FastAPI, Header, HTTPException, status

from runtime.debug.explain import explain_turn
from runtime.debug.relationship_view import build_relationship_view
from runtime.sessions.manager import SessionManager


def create_debug_app(
    session_manager: SessionManager,
    api_key_getter: Callable[[], str] | None = None,
) -> FastAPI:
    """Build a FastAPI application wired to a live SessionManager."""

    app = FastAPI(title="Nūr Runtime Debug", version="0.9.0")

    async def require_auth(authorization: str | None = Header(default=None)) -> None:
        expected = str(api_key_getter() if api_key_getter is not None else "")
        if not expected:
            return
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization.split(" ", 1)[1].strip()
        if not secrets.compare_digest(token, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # ------------------------------------------------------------------
    # GET /sessions — list active sessions
    # ------------------------------------------------------------------

    @app.get("/sessions", dependencies=[Depends(require_auth)])
    async def list_sessions() -> list[dict]:
        sessions = session_manager.active_sessions
        now = time.time()
        return [
            {
                "session_key": key,
                "rel_key": session.rel_key,
                "user_id": session.user_id,
                "last_activity": session.last_activity,
                "idle_seconds": round(now - session.last_activity, 1),
                "queue_size": session._queue.qsize(),
                "has_debug": session.last_debug is not None,
            }
            for key, session in sessions.items()
        ]

    # ------------------------------------------------------------------
    # GET /sessions/{session_key}/debug — per-session debug view
    # ------------------------------------------------------------------

    @app.get("/sessions/{session_key}/debug", dependencies=[Depends(require_auth)])
    async def session_debug(session_key: str) -> dict:
        sessions = session_manager.active_sessions
        session = sessions.get(session_key)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        pipeline = session.pipeline
        engine = pipeline.engine
        unresolved = engine.active_unresolved()

        result: dict = {
            "session_key": session_key,
            "rel_key": session.rel_key,
            "user_id": session.user_id,
            "last_activity": session.last_activity,
            # Live modulator state
            "modulators": engine.snapshot(),
            "emotion_label": engine.to_emotion_label(),
            # Memory counts
            "memory": {
                "short_term": len(pipeline.short_term),
                "long_term": pipeline.long_term.count(),
                "relationship_events": pipeline.relationship_memory.count_events(session.user_id),
                "open_loops": pipeline.relationship_memory.count_open_loops(session.user_id),
            },
            # Resolution
            "unresolved_count": len(unresolved),
            "unresolved_items": [
                {
                    "id": item.id,
                    "source": item.source,
                    "description": item.description,
                    "intensity": item.intensity,
                    "decay_rate": item.decay_rate,
                    "created_at": item.created_at.isoformat(),
                }
                for item in unresolved
            ],
            # Last turn's full debug snapshot
            "last_turn": _debug_to_dict(session.last_debug) if session.last_debug else None,
        }
        return result

    # ------------------------------------------------------------------
    # POST /sessions/{session_key}/reset — evict session
    # ------------------------------------------------------------------

    @app.post("/sessions/{session_key}/reset", dependencies=[Depends(require_auth)])
    async def reset_session(session_key: str) -> dict:
        sessions = session_manager.active_sessions
        if session_key not in sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        await session_manager.evict_session(session_key)
        return {"status": "evicted", "session_key": session_key}

    return app


# ---------------------------------------------------------------------------
# Debug-state serialization (preserves existing Nur debug fields)
# ---------------------------------------------------------------------------

def _debug_to_dict(debug) -> dict:
    """Convert a DebugState to a JSON-serializable dict.

    Mirrors the field set from the v1/v2 debug dashboard so existing
    tooling and inspection workflows keep working.
    """
    d: dict = {}

    # Basics
    d["user_message"] = debug.user_message
    d["user_id"] = debug.user_id
    d["timestamp"] = debug.timestamp

    # Contagion
    d["detected_emotion"] = (
        {"arousal": debug.detected_emotion.arousal, "valence": debug.detected_emotion.valence}
        if debug.detected_emotion else None
    )

    # Appraisal
    d["appraisal_frame"] = (
        debug.appraisal_frame.to_dict() if getattr(debug, "appraisal_frame", None) else None
    )

    # Context switch
    d["baseline_shift_applied"] = debug.baseline_shift_applied

    # Event classification
    d["event_classified"] = debug.event_classified
    d["event_intensity"] = debug.event_intensity
    d["is_spike"] = debug.is_spike
    d["modulator_snapshot"] = debug.modulator_snapshot

    # Memory retrieval
    d["retrieved_memories"] = [
        {"summary": m.summary, "valence": m.emotional_valence, "spike": m.spike}
        for m in debug.retrieved_memories
    ]
    d["semantic_memories"] = [
        {
            "kind": m.kind,
            "summary": m.summary,
            "topic": m.topic,
            "source": m.source,
            "score": m.score,
        }
        for m in getattr(debug, "semantic_memories", [])
    ]
    d["life_history_context"] = getattr(debug, "life_history_context", {}) or {}
    life_influence = getattr(debug, "life_influence", None)
    d["life_influence"] = life_influence.to_dict() if life_influence else {}
    d["life_influence_effects"] = getattr(debug, "life_influence_effects", {}) or {}
    d["explanation"] = explain_turn(debug)
    d["relationship_view"] = build_relationship_view(debug)
    d["skill_context"] = getattr(debug, "skill_context", {}) or {}
    d["relationship_context"] = (
        debug.relationship_context.to_dict()
        if getattr(debug, "relationship_context", None)
        else None
    )

    # Profiles
    d["person_profile"] = (
        {
            "person_id": debug.person_profile.person_id,
            "trust": debug.person_profile.trust,
            "interaction_count": debug.person_profile.interaction_count,
        }
        if debug.person_profile else None
    )
    d["self_profile"] = (
        {
            "strengths": debug.self_profile.strengths,
            "flaws": debug.self_profile.flaws,
            "triggers": debug.self_profile.triggers,
            "dissonance": debug.self_profile.dissonance,
        }
        if debug.self_profile else None
    )
    d["topic_profiles"] = [
        {"topic": t.topic, "charge": t.emotional_charge, "avoidance": t.avoidance}
        for t in debug.topic_profiles
    ]

    # Contradiction flags
    d["contradiction_flags"] = debug.contradiction_flags

    # Response strategy
    d["response_strategy"] = getattr(debug, "response_strategy", "") or ""
    strategy_trace = getattr(debug, "strategy_trace", None)
    d["strategy_trace"] = strategy_trace.to_dict() if strategy_trace else None
    d["affect_state"] = (
        debug.affect_state.to_dict()
        if getattr(debug, "affect_state", None)
        else None
    )
    d["agency_decision"] = (
        debug.agency_decision.to_dict()
        if getattr(debug, "agency_decision", None)
        else None
    )
    d["autonomy_level"] = getattr(debug, "autonomy_level", "") or ""

    # Generation
    d["response"] = debug.response
    d["self_check_passed"] = debug.self_check_passed
    d["self_check_issues"] = debug.self_check_issues
    d["correction_note"] = debug.correction_note
    d["generation_attempts"] = debug.generation_attempts

    # Energy
    d["energy_after"] = debug.energy_after
    d["emotion_label"] = debug.emotion_label

    # v2: Anticipation
    ant = debug.anticipation
    d["anticipation"] = (
        {
            "predicted_topics": ant.predicted_topics,
            "predicted_emotional_tone": ant.predicted_emotional_tone,
            "modulator_pre_shifts": ant.modulator_pre_shifts,
            "confidence": ant.confidence,
            "basis": ant.basis,
        }
        if ant else None
    )

    # v2: Inner dialogue
    trace = debug.dialogue_trace
    if trace:
        d["dialogue_trace"] = {
            "rounds": [
                {
                    "round_number": r.round_number,
                    "fast_path_candidate": r.fast_path_candidate,
                    "slow_path_evaluation": r.slow_path_evaluation,
                    "slow_path_approved": r.slow_path_approved,
                    "objection_reason": r.objection_reason,
                    "revision_notes": r.revision_notes,
                }
                for r in trace.rounds
            ],
            "final_candidate": trace.final_candidate,
            "total_llm_calls": trace.total_llm_calls,
            "reached_deadlock": trace.reached_deadlock,
            "deadlock_resolution": trace.deadlock_resolution,
            "dominant_path": trace.dominant_path,
            "tension_level": trace.tension_level,
        }
    else:
        d["dialogue_trace"] = None

    # v2: Defense activation
    defense = debug.defense_activation
    d["defense_activation"] = (
        {
            "defense_type": defense.defense_type,
            "raw_intensity": defense.raw_intensity,
            "expressed_intensity": defense.expressed_intensity,
            "suppression_delta": defense.suppression_delta,
            "reason": defense.reason,
        }
        if defense else None
    )

    # v2: Resolution
    d["unresolved_count"] = debug.unresolved_count
    d["unresolved_items"] = [
        {
            "id": item.id,
            "source": item.source,
            "description": item.description,
            "intensity": item.intensity,
            "decay_rate": item.decay_rate,
            "created_at": item.created_at.isoformat(),
        }
        for item in (debug.unresolved_items or [])
    ]

    # Agentic tools
    tt = debug.tool_trace
    if tt:
        d["tool_trace"] = {
            "proposed_intents": [
                {
                    "tool_name": i.tool_name,
                    "arguments": i.arguments,
                    "reason": i.reason,
                    "expected_outcome": i.expected_outcome,
                    "urgency": i.urgency,
                    "risk_tolerance": i.risk_tolerance,
                    "autonomy_bias": i.autonomy_bias,
                    "clarification_threshold": i.clarification_threshold,
                    "persistence_drive": i.persistence_drive,
                    "confidence": i.confidence,
                }
                for i in tt.proposed_intents
            ],
            "final_decision": (
                {
                    "decision": tt.final_decision.decision,
                    "rationale": tt.final_decision.rationale,
                }
                if tt.final_decision else None
            ),
            "executed_results": [
                {
                    "tool_name": r.tool_name,
                    "success": r.success,
                    "error": r.error,
                    "latency_ms": r.latency_ms,
                    "side_effect_summary": r.side_effect_summary,
                }
                for r in tt.executed_results
            ],
            "observations": [
                {
                    "summary": o.summary,
                    "emotional_delta": o.emotional_delta,
                    "certainty_delta": o.certainty_delta,
                    "resolution_delta": o.resolution_delta,
                    "self_observation": o.self_observation,
                    "continue_tool_loop": o.continue_tool_loop,
                }
                for o in tt.observations
            ],
            "loop_count": tt.loop_count,
        }
    else:
        d["tool_trace"] = None

    # Task trace (Phase 7)
    tt_task = getattr(debug, "task_trace", None)
    if tt_task and tt_task.plan:
        plan = tt_task.plan
        d["task_trace"] = {
            "plan": {
                "id": plan.id,
                "goal": plan.goal,
                "status": plan.status.value if hasattr(plan.status, "value") else str(plan.status),
                "steps": [
                    {
                        "id": s.id,
                        "tool_name": s.tool_name,
                        "description": s.description,
                        "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                        "success": s.result.success if s.result else None,
                        "error": s.result.error if s.result and not s.result.success else None,
                    }
                    for s in plan.steps
                ],
                "steps_completed": plan.steps_completed,
                "steps_failed": plan.steps_failed,
                "current_step_index": plan.current_step_index,
                "persistence_drive": plan.persistence_drive,
            },
            "steps_executed": tt_task.steps_executed,
            "steps_succeeded": tt_task.steps_succeeded,
            "steps_failed": tt_task.steps_failed,
            "total_latency_ms": tt_task.total_latency_ms,
            "continued_after_failure": tt_task.continued_after_failure,
            "plan_outcome": tt_task.plan_outcome,
        }
    else:
        d["task_trace"] = None

    # Agentic tools: compact summary for quick inspection
    d["tool_summary"] = _build_tool_summary(debug)

    av = debug.action_variables
    d["action_variables"] = (
        {
            "risk_tolerance": av.risk_tolerance,
            "action_urgency": av.action_urgency,
            "clarification_threshold": av.clarification_threshold,
            "persistence_drive": av.persistence_drive,
            "autonomy_bias": av.autonomy_bias,
        }
        if av else None
    )

    # Tool memory effects (Phase 3)
    tme = debug.tool_memory_effects
    d["tool_memory_effects"] = (
        {
            "short_term_recorded": tme.short_term_recorded,
            "long_term_written": tme.long_term_written,
            "long_term_summary": tme.long_term_summary,
            "self_observations": tme.self_observations,
            "unresolved_items_created": tme.unresolved_items_created,
            "trust_delta": tme.trust_delta,
        }
        if tme else None
    )

    # Proactive behavior (Phase 8)
    pt = getattr(debug, "proactive_trace", None)
    if pt:
        d["proactive_trace"] = {
            "triggers_found": [
                {
                    "source": t.source.value if hasattr(t.source, "value") else str(t.source),
                    "description": t.description,
                    "intensity": t.intensity,
                    "item_id": t.item_id,
                }
                for t in pt.triggers_found
            ],
            "action_taken": (
                {
                    "action_type": pt.action_taken.action_type,
                    "message": pt.action_taken.message,
                    "rationale": pt.action_taken.rationale,
                    "trigger_source": (
                        pt.action_taken.trigger.source.value
                        if hasattr(pt.action_taken.trigger.source, "value")
                        else str(pt.action_taken.trigger.source)
                    ),
                }
                if pt.action_taken else None
            ),
            "suppressed_reasons": pt.suppressed_reasons,
            "limits_applied": pt.limits_applied,
            "life_influence_score_deltas": getattr(pt, "life_influence_score_deltas", {}),
            "idle_seconds": pt.idle_seconds,
            "proactive_count": pt.proactive_count,
            "timestamp": pt.timestamp,
        }
    else:
        d["proactive_trace"] = None

    # Timing
    d["stage_timings_ms"] = debug.stage_timings_ms

    return d


def _build_tool_summary(debug) -> dict | None:
    """Build a compact tool-activity summary for quick inspection.

    Returns None when no tool activity occurred this turn.
    """
    tt = debug.tool_trace
    if tt is None:
        return None

    executed = tt.executed_results
    if not executed and not tt.proposed_intents:
        return None

    last = executed[-1] if executed else None
    return {
        "tool_used": len(executed) > 0,
        "tools_executed": len(executed),
        "last_tool_name": last.tool_name if last else None,
        "last_tool_success": last.success if last else None,
        "decision": tt.final_decision.decision if tt.final_decision else None,
        "loop_count": tt.loop_count,
    }
