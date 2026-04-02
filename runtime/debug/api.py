"""Runtime-aware debug API — session-based inspection.

Replaces the old single-pipeline debug model with live session awareness.
All data is read from the SessionManager — no separate pipeline.

Endpoints:
    GET  /sessions                    — list active sessions
    GET  /sessions/{rel_key}/debug    — per-user debug view
    POST /sessions/{rel_key}/reset    — evict (digest + persist) a session
"""

from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException

from runtime.sessions.manager import SessionManager


def create_debug_app(session_manager: SessionManager) -> FastAPI:
    """Build a FastAPI application wired to a live SessionManager."""

    app = FastAPI(title="Jarvis Runtime Debug", version="0.7.0")

    # ------------------------------------------------------------------
    # GET /sessions — list active sessions
    # ------------------------------------------------------------------

    @app.get("/sessions")
    async def list_sessions() -> list[dict]:
        sessions = session_manager.active_sessions
        now = time.time()
        return [
            {
                "rel_key": key,
                "user_id": session.user_id,
                "last_activity": session.last_activity,
                "idle_seconds": round(now - session.last_activity, 1),
                "queue_size": session._queue.qsize(),
                "has_debug": session.last_debug is not None,
            }
            for key, session in sessions.items()
        ]

    # ------------------------------------------------------------------
    # GET /sessions/{rel_key}/debug — per-user debug view
    # ------------------------------------------------------------------

    @app.get("/sessions/{rel_key}/debug")
    async def session_debug(rel_key: str) -> dict:
        sessions = session_manager.active_sessions
        session = sessions.get(rel_key)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        pipeline = session.pipeline
        engine = pipeline.engine
        unresolved = engine.active_unresolved()

        result: dict = {
            "rel_key": rel_key,
            "user_id": session.user_id,
            "last_activity": session.last_activity,
            # Live modulator state
            "modulators": engine.snapshot(),
            "emotion_label": engine.to_emotion_label(),
            # Memory counts
            "memory": {
                "short_term": len(pipeline.short_term),
                "long_term": pipeline.long_term.count(),
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
    # POST /sessions/{rel_key}/reset — evict session
    # ------------------------------------------------------------------

    @app.post("/sessions/{rel_key}/reset")
    async def reset_session(rel_key: str) -> dict:
        sessions = session_manager.active_sessions
        if rel_key not in sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        await session_manager.evict_session(rel_key)
        return {"status": "evicted", "rel_key": rel_key}

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

    # Timing
    d["stage_timings_ms"] = debug.stage_timings_ms

    return d
