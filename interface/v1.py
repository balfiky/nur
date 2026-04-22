"""Versioned public integration API (``/v1/*``) for Project Nūr.

Unlike the legacy endpoints in ``interface/api.py`` — which exist primarily
to serve the bundled web UI — the ``v1`` surface is designed for external
integrators (dashboards, Slack bots, eval harnesses, Langflow, etc.).

Design goals:
  * **Stable shapes**: every response is a plain JSON-serializable dict with
    documented keys. Optional fields may become present; existing keys will
    not be renamed without a version bump.
  * **Auth is opt-in**: when ``RuntimeConfig.api_key`` is empty, everything
    is open. When it is set, every endpoint except ``/health`` and
    ``/ready`` requires an ``Authorization: Bearer <api_key>`` header.
  * **Single source of truth**: queries flow through the live
    :class:`SessionManager` so integrators see the same state as the web UI
    and Telegram channel.

Mounted under ``/v1`` via :func:`build_v1_router`.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from config.loader import get_config
from runtime.config import RuntimeConfig
from runtime.debug.api import _debug_to_dict
from runtime.sessions.manager import SessionManager
from runtime.sessions.user_session import UserSession


# ---------------------------------------------------------------------------
# Pydantic request models (responses are plain dicts for simplicity)
# ---------------------------------------------------------------------------


class V1ChatRequest(BaseModel):
    message: str = Field(..., description="User message to process")
    user_id: str = Field("default", description="Stable user identifier")
    chat_id: str = Field("default", description="Conversation identifier within this user")
    platform: str = Field("web", description="Channel tag used for session keying")
    include_debug: bool = Field(False, description="Include full turn debug payload in the response")


class V1RestRequest(BaseModel):
    hours: float = Field(1.0, ge=0.0, le=48.0, description="Simulated hours of rest")
    user_id: str = Field("default")
    chat_id: str = Field("default")
    platform: str = Field("web")


class V1EndSessionRequest(BaseModel):
    user_id: str = Field("default")
    chat_id: str = Field("default")
    platform: str = Field("web")


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def build_v1_router(
    *,
    session_manager_getter,
    config_getter,
    serve_started_at: float,
) -> APIRouter:
    """Create the ``/v1`` router.

    Parameters are callables so the router stays decoupled from the module
    globals in ``interface/api.py`` (which are reset between tests).
    """

    router = APIRouter(prefix="/v1")

    # ------------------------------------------------------------------
    # Auth dependency — reads the current config on every request so that
    # enabling or rotating the key via POST /config takes effect without
    # restarting the app.
    # ------------------------------------------------------------------

    async def require_auth(
        authorization: str | None = Header(default=None),
    ) -> None:
        cfg = config_getter()
        expected = cfg.api_key
        if not expected:
            return  # auth disabled
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization.split(" ", 1)[1].strip()
        if token != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def get_manager() -> SessionManager:
        return session_manager_getter()

    def _session_key(platform: str, user_id: str, chat_id: str) -> str:
        return f"{platform}:{user_id}:{chat_id}"

    # ------------------------------------------------------------------
    # Public endpoints (no auth even when api_key is set)
    # ------------------------------------------------------------------

    @router.get("/health", summary="Liveness probe")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "nur",
            "version": "1",
            "uptime_seconds": round(time.time() - serve_started_at, 1),
        }

    @router.get("/ready", summary="Readiness probe")
    async def ready() -> dict[str, Any]:
        cfg = config_getter()
        mgr = get_manager()
        return {
            "status": "ready",
            "llm_backend": cfg.llm_backend,
            "has_llm_key": bool(cfg.llm_api_key or cfg.minimax_api_key),
            "auth_enabled": bool(cfg.api_key),
            "active_sessions": len(mgr.active_sessions),
            "max_active_sessions": cfg.max_active_sessions,
        }

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    @router.post(
        "/chat",
        summary="Process one turn through the cognitive pipeline",
        dependencies=[Depends(require_auth)],
    )
    async def chat(req: V1ChatRequest) -> dict[str, Any]:
        mgr = get_manager()
        response = await mgr.handle_message(
            req.platform, req.user_id, req.chat_id, req.message,
        )
        session = await mgr.ensure_session(req.platform, req.user_id, req.chat_id)
        payload: dict[str, Any] = {
            "response": response,
            "session_key": _session_key(req.platform, req.user_id, req.chat_id),
            "user_id": req.user_id,
            "emotion_label": session.pipeline.engine.to_emotion_label(),
            "energy": session.pipeline.engine.state.energy,
        }
        if req.include_debug and session.last_debug is not None:
            payload["debug"] = _debug_to_dict(session.last_debug)
        return payload

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    @router.get(
        "/sessions",
        summary="List active sessions",
        dependencies=[Depends(require_auth)],
    )
    async def list_sessions() -> dict[str, Any]:
        mgr = get_manager()
        now = time.time()
        items = [
            {
                "session_key": key,
                "platform": key.split(":", 2)[0] if ":" in key else "",
                "user_id": session.user_id,
                "rel_key": session.rel_key,
                "idle_seconds": round(now - session.last_activity, 1),
                "queue_size": session._queue.qsize(),
                "emotion_label": session.pipeline.engine.to_emotion_label(),
                "energy": session.pipeline.engine.state.energy,
            }
            for key, session in mgr.active_sessions.items()
        ]
        return {"count": len(items), "sessions": items}

    @router.get(
        "/sessions/{session_key:path}",
        summary="Per-session snapshot (modulators, memory counts, last turn debug)",
        dependencies=[Depends(require_auth)],
    )
    async def session_snapshot(session_key: str) -> dict[str, Any]:
        mgr = get_manager()
        session = mgr.active_sessions.get(session_key)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return _serialize_session(session_key, session)

    @router.post(
        "/sessions/{session_key:path}/reset",
        summary="Evict a session (digest, persist, close)",
        dependencies=[Depends(require_auth)],
    )
    async def reset_session(session_key: str) -> dict[str, Any]:
        mgr = get_manager()
        if session_key not in mgr.active_sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        await mgr.evict_session(session_key)
        return {"status": "evicted", "session_key": session_key}

    @router.post(
        "/sessions/end",
        summary="End a session's current turn without evicting (digest, persist)",
        dependencies=[Depends(require_auth)],
    )
    async def end_session(req: V1EndSessionRequest) -> dict[str, Any]:
        mgr = get_manager()
        session = await mgr.ensure_session(req.platform, req.user_id, req.chat_id)
        result = await session.end_session()
        return {
            "summary": result.summary,
            "emotional_arc_label": result.emotional_arc_label,
            "trust_delta": result.trust_delta,
            "memories_written": result.memories_written,
            "energy_drain": result.energy_drain,
            "unresolved_flags": result.unresolved_flags,
        }

    @router.post(
        "/sessions/rest",
        summary="Apply simulated rest (recover energy, decay modulators)",
        dependencies=[Depends(require_auth)],
    )
    async def session_rest(req: V1RestRequest) -> dict[str, Any]:
        mgr = get_manager()
        session = await mgr.ensure_session(req.platform, req.user_id, req.chat_id)
        energy_before = session.pipeline.engine.state.energy
        await session.apply_rest(req.hours)
        return {
            "hours": req.hours,
            "energy_before": energy_before,
            "energy_after": session.pipeline.engine.state.energy,
            "session_key": _session_key(req.platform, req.user_id, req.chat_id),
        }

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    @router.get(
        "/soul",
        summary="Seeded soul / authored identity for Nūr",
        dependencies=[Depends(require_auth)],
    )
    async def get_soul() -> dict[str, Any]:
        soul = get_config().soul
        return {
            "name": soul.name,
            "identity": soul.identity,
            "voice": soul.voice,
            "relational_stance": soul.relational_stance,
            "likes": soul.likes,
            "dislikes": soul.dislikes,
            "boundaries": soul.boundaries,
            "growth_policy": soul.growth_policy,
            "core_values": soul.core_values,
            "initial_traits": soul.initial_traits,
        }

    @router.get(
        "/profiles/self",
        summary="Current self-model (strengths, flaws, triggers, maturity)",
        dependencies=[Depends(require_auth)],
    )
    async def get_self_profile(
        user_id: str = Query("default"),
        chat_id: str = Query("default"),
        platform: str = Query("web"),
    ) -> dict[str, Any]:
        mgr = get_manager()
        session = await mgr.ensure_session(platform, user_id, chat_id)
        profile = session.pipeline.self_profile.get_profile()
        return {
            "observed_traits": profile.observed_traits,
            "strengths": profile.strengths,
            "flaws": profile.flaws,
            "triggers": profile.triggers,
            "dissonance": profile.dissonance,
            "maturity_score": profile.maturity_score,
            "defense_events_logged": len(profile.defense_log),
            "trait_scores": session.pipeline.self_profile.get_trait_scores(),
        }

    @router.get(
        "/profiles/person",
        summary="Person profile (trust, reliability, volatility, baseline shift)",
        dependencies=[Depends(require_auth)],
    )
    async def get_person_profile(
        user_id: str = Query(..., description="User to load"),
        chat_id: str = Query("default"),
        platform: str = Query("web"),
    ) -> dict[str, Any]:
        mgr = get_manager()
        session = await mgr.ensure_session(platform, user_id, chat_id)
        profile = session.pipeline.person_profiles.get_or_create(user_id)
        return {
            "person_id": profile.person_id,
            "name": profile.name,
            "trust": profile.trust,
            "reliability": profile.reliability,
            "emotional_volatility": profile.emotional_volatility,
            "stress_response": profile.stress_response,
            "interaction_count": profile.interaction_count,
            "primacy_weight": profile.primacy_weight,
            "baseline_shift": {
                "arousal": profile.baseline_shift.arousal,
                "valence": profile.baseline_shift.valence,
                "certainty": profile.baseline_shift.certainty,
                "bonding": profile.baseline_shift.bonding,
            },
        }

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    @router.get(
        "/memory/long_term",
        summary="Recent long-term memories for a user (highest-activation first)",
        dependencies=[Depends(require_auth)],
    )
    async def get_long_term(
        user_id: str = Query(..., description="User whose memories to load"),
        chat_id: str = Query("default"),
        platform: str = Query("web"),
        limit: int = Query(20, ge=1, le=200),
    ) -> dict[str, Any]:
        mgr = get_manager()
        session = await mgr.ensure_session(platform, user_id, chat_id)
        entries = session.pipeline.long_term.by_person(user_id)[:limit]
        return {
            "count": session.pipeline.long_term.count(),
            "returned": len(entries),
            "entries": [
                {
                    "id": e.id,
                    "timestamp": e.timestamp,
                    "summary": e.summary,
                    "emotional_valence": e.emotional_valence,
                    "trust_delta": e.trust_delta,
                    "topic": e.topic,
                    "source_person": e.source_person,
                    "confidence": e.confidence,
                    "spike": e.spike,
                }
                for e in entries
            ],
        }

    @router.get(
        "/memory/relationship",
        summary="Relationship events and open loops for a user",
        dependencies=[Depends(require_auth)],
    )
    async def get_relationship_memory(
        user_id: str = Query(..., description="User whose relationship to load"),
        chat_id: str = Query("default"),
        platform: str = Query("web"),
        topic: str = Query("", description="Optional topic filter"),
        event_limit: int = Query(10, ge=1, le=100),
        loop_limit: int = Query(10, ge=1, le=100),
    ) -> dict[str, Any]:
        mgr = get_manager()
        session = await mgr.ensure_session(platform, user_id, chat_id)
        mem = session.pipeline.relationship_memory
        events = mem.recent_events(user_id, topic=topic, limit=event_limit)
        loops = mem.active_loops(user_id, topic=topic, limit=loop_limit)
        return {
            "event_count": mem.count_events(user_id),
            "open_loop_count": mem.count_open_loops(user_id),
            "events": [event.to_dict() for event in events],
            "open_loops": [loop.to_dict() for loop in loops],
        }

    @router.get(
        "/memory/semantic",
        summary="Semantic memory records for a user",
        dependencies=[Depends(require_auth)],
    )
    async def get_semantic_memory(
        user_id: str = Query(..., description="User whose semantic memory to load"),
        chat_id: str = Query("default"),
        platform: str = Query("web"),
        query: str = Query("", description="Optional search query"),
        topic: str = Query("", description="Optional topic filter"),
        limit: int = Query(20, ge=1, le=200),
    ) -> dict[str, Any]:
        mgr = get_manager()
        session = await mgr.ensure_session(platform, user_id, chat_id)
        if query:
            entries = session.pipeline.semantic_memory.retrieve(
                query,
                source_person=user_id,
                topic=topic,
                limit=limit,
            )
        else:
            entries = session.pipeline.semantic_memory.recent(
                source_person=user_id,
                topic=topic,
                limit=limit,
            )
        return {
            "count": session.pipeline.semantic_memory.count(),
            "returned": len(entries),
            "entries": [entry.to_dict() for entry in entries],
        }

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @router.get(
        "/tools",
        summary="List registered tool capabilities",
        dependencies=[Depends(require_auth)],
    )
    async def list_tools(
        user_id: str = Query("default"),
        chat_id: str = Query("default"),
        platform: str = Query("web"),
    ) -> dict[str, Any]:
        mgr = get_manager()
        session = await mgr.ensure_session(platform, user_id, chat_id)
        executor = session.pipeline._tool_executor
        if executor is None:
            return {"count": 0, "tools": []}
        registry = executor._registry
        tools = registry.list_tools()
        return {
            "count": len(tools),
            "tools": [
                {
                    "name": cap.name,
                    "description": cap.description,
                    "category": cap.category.value if hasattr(cap.category, "value") else str(cap.category),
                    "arg_schema": cap.arg_schema,
                    "supports_streaming": cap.supports_streaming,
                    "requires_network": cap.requires_network,
                    "mcp_backed": cap.mcp_backed,
                }
                for cap in tools
            ],
        }

    # ------------------------------------------------------------------
    # Config (read-only here; write still lives on legacy POST /config)
    # ------------------------------------------------------------------

    @router.get(
        "/config",
        summary="Current runtime config (secrets redacted)",
        dependencies=[Depends(require_auth)],
    )
    async def get_config_v1() -> dict[str, Any]:
        cfg = config_getter()
        return {
            "config": cfg.to_public_dict(),
            "secret_status": cfg.secret_status(),
        }

    return router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_session(session_key: str, session: UserSession) -> dict[str, Any]:
    """Per-session snapshot used by the ``/v1/sessions/{key}`` endpoint.

    Kept here (not reused from the debug API) so the stable v1 shape can
    evolve independently from the internal debug payload.
    """
    pipeline = session.pipeline
    engine = pipeline.engine
    unresolved = engine.active_unresolved()
    return {
        "session_key": session_key,
        "user_id": session.user_id,
        "rel_key": session.rel_key,
        "last_activity": session.last_activity,
        "modulators": engine.snapshot(),
        "emotion_label": engine.to_emotion_label(),
        "energy": engine.state.energy,
        "memory": {
            "short_term": len(pipeline.short_term),
            "long_term": pipeline.long_term.count(),
            "relationship_events": pipeline.relationship_memory.count_events(session.user_id),
            "open_loops": pipeline.relationship_memory.count_open_loops(session.user_id),
        },
        "unresolved_count": len(unresolved),
        "unresolved_items": [item.to_dict() for item in unresolved],
        "last_turn": _debug_to_dict(session.last_debug) if session.last_debug else None,
    }
