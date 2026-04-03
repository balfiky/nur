"""FastAPI backend for Project Nūr."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from pipeline import CognitivePipeline
from runtime.config import RuntimeConfig
from runtime.debug.api import _debug_to_dict as _serialize_debug
from runtime.llm.backend import create_llm_backend
from runtime.sessions.manager import SessionManager
from runtime.sessions.user_session import UserSession
from runtime.tools import create_tool_executor


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    # Shutdown
    global _session_manager, _pipeline_override
    if _session_manager is not None:
        await _session_manager.shutdown()
        _session_manager = None
    if _pipeline_override is not None:
        _pipeline_override.close()
        _pipeline_override = None


app = FastAPI(title="Project Nūr", version="0.3.0", lifespan=_lifespan)

WEB_PLATFORM = "web"

_session_manager: SessionManager | None = None
_pipeline_override: CognitivePipeline | None = None


class ChatRequest(BaseModel):
    message: str
    user_id: str = "default"
    chat_id: str = "default"


class ChatResponse(BaseModel):
    response: str
    debug: dict


class EndSessionRequest(BaseModel):
    user_id: str = "default"
    chat_id: str = "default"


class RestRequest(BaseModel):
    hours: float = 1.0
    user_id: str = "default"
    chat_id: str = "default"


def get_session_manager() -> SessionManager:
    """Create the shared web SessionManager lazily."""
    global _session_manager
    if _session_manager is None:
        config = RuntimeConfig.from_yaml("runtime_config.yaml")
        _session_manager = SessionManager(
            config=config,
            backend_factory=lambda: create_llm_backend(config),
            tool_executor_factory=create_tool_executor,
        )
    return _session_manager


def set_session_manager(manager: SessionManager | None) -> None:
    """Testing hook to replace the default web SessionManager."""
    global _session_manager, _pipeline_override
    _session_manager = manager
    _pipeline_override = None


def set_pipeline(pipeline: CognitivePipeline | None) -> None:
    """Focused testing hook that bypasses the SessionManager."""
    global _pipeline_override, _session_manager
    _pipeline_override = pipeline
    _session_manager = None


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if _pipeline_override is not None:
        result = _pipeline_override.process(req.message, user_id=req.user_id)
        return ChatResponse(response=result.response, debug=_serialize_debug(result.debug))

    manager = get_session_manager()
    response = await manager.handle_message(
        WEB_PLATFORM,
        req.user_id,
        req.chat_id,
        req.message,
    )
    session = await manager.ensure_session(WEB_PLATFORM, req.user_id, req.chat_id)
    debug = _serialize_debug(session.last_debug) if session.last_debug else {}
    return ChatResponse(response=response, debug=debug)


@app.get("/debug")
async def debug(user_id: str = "default", chat_id: str = "default") -> dict:
    if _pipeline_override is not None:
        unresolved = _pipeline_override.engine.active_unresolved()
        return {
            "modulator_snapshot": _pipeline_override.engine.snapshot(),
            "emotion_label": _pipeline_override.engine.to_emotion_label(),
            "energy": _pipeline_override.engine.state.energy,
            "short_term_count": len(_pipeline_override.short_term),
            "long_term_count": _pipeline_override.long_term.count(),
            "unresolved_count": len(unresolved),
            "unresolved_items": [item.to_dict() for item in unresolved],
        }

    manager = get_session_manager()
    session = await manager.ensure_session(WEB_PLATFORM, user_id, chat_id)
    return _session_debug(_session_key(user_id, chat_id), session)


@app.post("/session/end")
async def end_session(req: EndSessionRequest) -> dict:
    if _pipeline_override is not None:
        result = _pipeline_override.end_session(user_id=req.user_id)
        return _digested_to_dict(result)

    manager = get_session_manager()
    session = await manager.ensure_session(WEB_PLATFORM, req.user_id, req.chat_id)
    result = await session.end_session()
    return _digested_to_dict(result)


@app.post("/rest")
async def rest(req: RestRequest) -> dict:
    if _pipeline_override is not None:
        energy_before = _pipeline_override.engine.state.energy
        _pipeline_override.apply_rest(req.hours)
        return {
            "hours": req.hours,
            "energy_before": energy_before,
            "energy_after": _pipeline_override.engine.state.energy,
        }

    manager = get_session_manager()
    session = await manager.ensure_session(WEB_PLATFORM, req.user_id, req.chat_id)
    energy_before = session.pipeline.engine.state.energy
    await session.apply_rest(req.hours)
    return {
        "hours": req.hours,
        "energy_before": energy_before,
        "energy_after": session.pipeline.engine.state.energy,
    }


@app.websocket("/ws")
async def websocket_chat(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            user_id = msg.get("user_id", "default")
            chat_id = msg.get("chat_id", "default")
            user_message = msg.get("message", "")

            if _pipeline_override is not None:
                result = _pipeline_override.process(user_message, user_id=user_id)
                payload = {
                    "response": result.response,
                    "debug": _serialize_debug(result.debug),
                }
            else:
                manager = get_session_manager()
                response = await manager.handle_message(
                    WEB_PLATFORM,
                    user_id,
                    chat_id,
                    user_message,
                )
                session = await manager.ensure_session(WEB_PLATFORM, user_id, chat_id)
                payload = {
                    "response": response,
                    "debug": (
                        _serialize_debug(session.last_debug)
                        if session.last_debug
                        else {}
                    ),
                }

            await ws.send_text(json.dumps(payload))
    except WebSocketDisconnect:
        pass


@app.get("/")
def index() -> HTMLResponse:
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    index_path = os.path.join(static_dir, "index.html")
    with open(index_path) as f:
        return HTMLResponse(content=f.read())


def _session_key(user_id: str, chat_id: str) -> str:
    return f"{WEB_PLATFORM}:{user_id}:{chat_id}"


def _session_debug(session_key_value: str, session: UserSession) -> dict:
    pipeline = session.pipeline
    unresolved = pipeline.engine.active_unresolved()
    return {
        "session_key": session_key_value,
        "user_id": session.user_id,
        "modulator_snapshot": pipeline.engine.snapshot(),
        "emotion_label": pipeline.engine.to_emotion_label(),
        "energy": pipeline.engine.state.energy,
        "short_term_count": len(pipeline.short_term),
        "long_term_count": pipeline.long_term.count(),
        "unresolved_count": len(unresolved),
        "unresolved_items": [item.to_dict() for item in unresolved],
        "last_turn": _serialize_debug(session.last_debug) if session.last_debug else None,
    }


def _digested_to_dict(result) -> dict:
    return {
        "summary": result.summary,
        "emotional_arc_label": result.emotional_arc_label,
        "trust_delta": result.trust_delta,
        "memories_written": result.memories_written,
        "energy_drain": result.energy_drain,
        "unresolved_flags": result.unresolved_flags,
    }
