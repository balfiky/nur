"""FastAPI backend for Project Nūr.

POST /chat — send a message, get response + debug state
GET /debug — get current emotional state
POST /session/end — end session, trigger digestion
POST /rest — simulate rest period
"""

from __future__ import annotations

import json
from dataclasses import asdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import os

from pipeline import CognitivePipeline

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Project Nūr", version="0.2.0")

# Global pipeline instance (single user for v1)
_pipeline: CognitivePipeline | None = None


def get_pipeline() -> CognitivePipeline:
    global _pipeline
    if _pipeline is None:
        backend = None
        if os.environ.get("MINIMAX_API_KEY"):
            from core.llm_client import LLMClient
            backend = LLMClient()
        _pipeline = CognitivePipeline(llm_backend=backend, db_path=":memory:")
    return _pipeline


def set_pipeline(p: CognitivePipeline) -> None:
    global _pipeline
    _pipeline = p


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    user_id: str = "default"


class ChatResponse(BaseModel):
    response: str
    debug: dict


class EndSessionRequest(BaseModel):
    user_id: str = "default"


class RestRequest(BaseModel):
    hours: float = 1.0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    pipe = get_pipeline()
    result = pipe.process(req.message, user_id=req.user_id)
    debug_dict = _debug_to_dict(result.debug)
    return ChatResponse(response=result.response, debug=debug_dict)


@app.get("/debug")
def debug() -> dict:
    pipe = get_pipeline()
    unresolved = pipe.engine.active_unresolved()
    return {
        "modulator_snapshot": pipe.engine.snapshot(),
        "emotion_label": pipe.engine.to_emotion_label(),
        "energy": pipe.engine.state.energy,
        "short_term_count": len(pipe.short_term),
        "long_term_count": pipe.long_term.count(),
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
    }


@app.post("/session/end")
def end_session(req: EndSessionRequest) -> dict:
    pipe = get_pipeline()
    result = pipe.end_session(user_id=req.user_id)
    return {
        "summary": result.summary,
        "emotional_arc_label": result.emotional_arc_label,
        "trust_delta": result.trust_delta,
        "memories_written": result.memories_written,
        "energy_drain": result.energy_drain,
        "unresolved_flags": result.unresolved_flags,
    }


@app.post("/rest")
def rest(req: RestRequest) -> dict:
    pipe = get_pipeline()
    energy_before = pipe.engine.state.energy
    pipe.apply_rest(req.hours)
    return {
        "hours": req.hours,
        "energy_before": energy_before,
        "energy_after": pipe.engine.state.energy,
    }


# ---------------------------------------------------------------------------
# WebSocket for streaming chat
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    await ws.accept()
    pipe = get_pipeline()
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            user_message = msg.get("message", "")
            user_id = msg.get("user_id", "default")

            result = pipe.process(user_message, user_id=user_id)
            debug_dict = _debug_to_dict(result.debug)

            await ws.send_text(json.dumps({
                "response": result.response,
                "debug": debug_dict,
            }))
    except WebSocketDisconnect:
        pass


# ---------------------------------------------------------------------------
# Serve the web UI
# ---------------------------------------------------------------------------

@app.get("/")
def index() -> HTMLResponse:
    import os
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    index_path = os.path.join(static_dir, "index.html")
    with open(index_path) as f:
        return HTMLResponse(content=f.read())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _debug_to_dict(debug) -> dict:
    """Convert DebugState to a JSON-serializable dict."""
    d = {}
    d["user_message"] = debug.user_message
    d["user_id"] = debug.user_id
    d["detected_emotion"] = (
        {"arousal": debug.detected_emotion.arousal, "valence": debug.detected_emotion.valence}
        if debug.detected_emotion else None
    )
    d["baseline_shift_applied"] = debug.baseline_shift_applied
    d["event_classified"] = debug.event_classified
    d["event_intensity"] = debug.event_intensity
    d["is_spike"] = debug.is_spike
    d["modulator_snapshot"] = debug.modulator_snapshot
    d["retrieved_memories"] = [
        {"summary": m.summary, "valence": m.emotional_valence, "spike": m.spike}
        for m in debug.retrieved_memories
    ]
    d["person_profile"] = (
        {"person_id": debug.person_profile.person_id, "trust": debug.person_profile.trust,
         "interaction_count": debug.person_profile.interaction_count}
        if debug.person_profile else None
    )
    d["self_profile"] = (
        {"strengths": debug.self_profile.strengths, "flaws": debug.self_profile.flaws,
         "triggers": debug.self_profile.triggers, "dissonance": debug.self_profile.dissonance}
        if debug.self_profile else None
    )
    d["topic_profiles"] = [
        {"topic": t.topic, "charge": t.emotional_charge, "avoidance": t.avoidance}
        for t in debug.topic_profiles
    ]
    d["contradiction_flags"] = debug.contradiction_flags
    d["response"] = debug.response
    d["self_check_passed"] = debug.self_check_passed
    d["self_check_issues"] = debug.self_check_issues
    d["correction_note"] = debug.correction_note
    d["generation_attempts"] = debug.generation_attempts
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

    # v2: Inner dialogue trace
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

    return d
