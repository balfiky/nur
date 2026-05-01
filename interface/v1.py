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

import os
import secrets
import shutil
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from config.loader import get_config
from runtime.config import RuntimeConfig
from runtime.debug.api import _debug_to_dict
from runtime.debug.persona_view import build_persona_view
from runtime.sessions.manager import SessionManager
from runtime.sessions.user_session import UserSession


# ---------------------------------------------------------------------------
# Path-traversal guards for destructive operations
# ---------------------------------------------------------------------------

_DISALLOWED_PATH_MARKERS = ("/", "\\", "..", "\x00")


def _validate_path_token(name: str, value: str) -> None:
    """Reject values that could escape the per-user data directory."""
    if not value:
        raise HTTPException(status_code=400, detail=f"{name} must not be empty")
    if ":" in value:
        raise HTTPException(status_code=400, detail=f"{name} must not contain ':'")
    for marker in _DISALLOWED_PATH_MARKERS:
        if marker in value:
            raise HTTPException(
                status_code=400,
                detail=f"{name} contains disallowed character",
            )


# ---------------------------------------------------------------------------
# Pydantic request models (responses are plain dicts for simplicity)
# ---------------------------------------------------------------------------


_MAX_CHAT_MESSAGE_CHARS = 16_000
_MAX_ID_CHARS = 128


class V1ChatRequest(BaseModel):
    message: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_CHAT_MESSAGE_CHARS,
        description="User message to process",
    )
    user_id: str = Field(
        "default",
        min_length=1,
        max_length=_MAX_ID_CHARS,
        description="Stable user identifier",
    )
    chat_id: str = Field(
        "default",
        min_length=1,
        max_length=_MAX_ID_CHARS,
        description="Conversation identifier within this user",
    )
    platform: str = Field(
        "web",
        min_length=1,
        max_length=_MAX_ID_CHARS,
        description="Channel tag used for session keying",
    )
    include_debug: bool = Field(False, description="Include full turn debug payload in the response")


class V1RestRequest(BaseModel):
    hours: float = Field(1.0, ge=0.0, le=48.0, description="Simulated hours of rest")
    user_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)
    chat_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)
    platform: str = Field("web", min_length=1, max_length=_MAX_ID_CHARS)


class V1EndSessionRequest(BaseModel):
    user_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)
    chat_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)
    platform: str = Field("web", min_length=1, max_length=_MAX_ID_CHARS)


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
        expected = str(cfg.api_key or "")
        if not expected:
            return  # auth disabled
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
        env_has_key = bool(
            os.environ.get("LLM_API_KEY") or os.environ.get("MINIMAX_API_KEY")
        )
        return {
            "status": "ready",
            "llm_backend": cfg.llm_backend,
            "has_llm_key": bool(cfg.llm_api_key or cfg.minimax_api_key or env_has_key),
            "auth_enabled": bool(cfg.api_key),
            "active_sessions": len(mgr.active_sessions),
            "max_active_sessions": cfg.max_active_sessions,
        }

    @router.get(
        "/persona/state",
        summary="Presentation-only persona state for an active session",
        dependencies=[Depends(require_auth)],
    )
    async def persona_state(
        platform: str = Query("web", min_length=1, max_length=_MAX_ID_CHARS),
        user_id: str = Query("default", min_length=1, max_length=_MAX_ID_CHARS),
        chat_id: str = Query("default", min_length=1, max_length=_MAX_ID_CHARS),
    ) -> dict[str, Any]:
        _validate_path_token("platform", platform)
        _validate_path_token("user_id", user_id)
        _validate_path_token("chat_id", chat_id)
        mgr = get_manager()
        session_key = _session_key(platform, user_id, chat_id)
        session = mgr.active_sessions.get(session_key)
        if session is None:
            return build_persona_view(
                session_key=session_key,
                inactive_reason="No active session. Send a message first.",
            )
        return build_persona_view(session=session, session_key=session_key)

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
        try:
            response = await mgr.handle_message(
                req.platform, req.user_id, req.chat_id, req.message,
            )
            session = await mgr.ensure_session(req.platform, req.user_id, req.chat_id)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
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

    # ------------------------------------------------------------------
    # User-data deletion (fulfills the PRIVACY.md deletion promise)
    # ------------------------------------------------------------------

    @router.delete(
        "/users/{platform}/{user_id}",
        summary="Wipe all persisted data for a user on a given platform",
        dependencies=[Depends(require_auth)],
    )
    async def delete_user(platform: str, user_id: str) -> dict[str, Any]:
        """Evict live sessions for this user and delete their on-disk data.

        Scope:
          * All per-user SQLite rows under ``data/<platform>_<user_id>/nur.db``
          * All session JSONs under ``data/<platform>_<user_id>/sessions/``
          * The per-user directory itself

        Explicitly preserved:
          * ``data/shared/self_model.db`` — contains only rows for entity
            ``__self__`` and a defense log with no per-user column. Wiping
            it on every user delete would throw away the assistant's
            accumulated self-model, which is not what "delete user X" means.

        Idempotency: returns 404 if neither on-disk data nor DB rows exist.
        """
        _validate_path_token("platform", platform)
        _validate_path_token("user_id", user_id)

        mgr = get_manager()
        cfg = config_getter()
        rel_key = f"{platform}:{user_id}"
        user_dir = cfg.user_data_dir(rel_key)

        # Defense in depth: ensure the resolved path is actually inside data_dir
        # even if the validation above is bypassed by a future refactor.
        data_root = os.path.realpath(cfg.data_dir)
        resolved = os.path.realpath(user_dir)
        if not (resolved == data_root or resolved.startswith(data_root + os.sep)):
            raise HTTPException(
                status_code=400,
                detail="Resolved user path is not inside data_dir",
            )

        # Best-effort pre-delete counts. None = counting failed; partial
        # counts are OK — we must not fail the wipe just because a query
        # couldn't run.
        rows_deleted = _count_user_rows(cfg.user_db_path(rel_key), user_id)

        # Evict all live sessions for this rel_key before touching the
        # filesystem. Eviction closes the DB connection and flushes
        # session state so rmtree can succeed cleanly.
        prefix = f"{rel_key}:"
        evict_keys = [k for k in list(mgr.active_sessions.keys()) if k.startswith(prefix)]
        for key in evict_keys:
            await mgr.evict_session(key)

        # Count session-state files before the wipe.
        session_files_removed = 0
        sessions_dir = os.path.join(user_dir, "sessions")
        if os.path.isdir(sessions_dir):
            session_files_removed = sum(
                1 for name in os.listdir(sessions_dir) if name.endswith(".json")
            )

        existed = os.path.isdir(user_dir)
        had_rows = any(isinstance(v, int) and v > 0 for v in rows_deleted.values())

        if not existed and not had_rows and not evict_keys:
            raise HTTPException(
                status_code=404,
                detail=f"No data found for {rel_key}",
            )

        if existed:
            shutil.rmtree(user_dir, ignore_errors=False)

        return {
            "deleted": True,
            "rel_key": rel_key,
            "platform": platform,
            "user_id": user_id,
            "rows_deleted": rows_deleted,
            "session_files_removed": session_files_removed,
            "sessions_evicted": evict_keys,
            "path_removed": user_dir if existed else None,
            "shared_self_model_db_preserved": True,
        }

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


def _count_user_rows(db_path: str, user_id: str) -> dict[str, int | None]:
    """Best-effort pre-delete row counts for a user's per-user DB.

    Returns one entry per table we know holds user-scoped rows. Values
    can be:
      * ``0`` — table exists, no rows for this user
      * ``n > 0`` — that many rows will be wiped
      * ``None`` — counting failed (e.g., table missing, DB locked)

    Never raises. The endpoint must still succeed on the filesystem
    wipe even if every count here comes back as ``None``.
    """
    counts: dict[str, int | None] = {
        "memories": None,
        "relationship_events": None,
        "open_loops": None,
        "observations": None,
        "semantic_memories": None,
    }
    if not os.path.exists(db_path):
        return {k: 0 for k in counts}
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return counts
    try:
        # (table, user-filter column) — kept in sync with schema declarations in
        # core/memory/long_term.py, core/schema.py, core/memory/semantic.py,
        # core/profiles/base.py.
        queries = (
            ("memories", "source_person"),
            ("relationship_events", "source_person"),
            ("open_loops", "source_person"),
            ("observations", "entity_id"),
            ("semantic_memories", "source_person"),
        )
        for table, filter_col in queries:
            try:
                cur = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {filter_col} = ?",
                    (user_id,),
                )
                counts[table] = cur.fetchone()[0]
            except sqlite3.Error:
                counts[table] = None
    finally:
        conn.close()
    return counts


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
