"""FastAPI backend for Project Nūr."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from interface.v1 import build_v1_router
from pipeline import CognitivePipeline
from runtime.config import RuntimeConfig
from runtime.debug.api import _debug_to_dict as _serialize_debug
from runtime.llm.backend import create_llm_backend
from runtime.sessions.manager import SessionManager
from runtime.sessions.user_session import UserSession
from runtime.tools import create_tool_executor

_log = logging.getLogger(__name__)

_telegram_task: asyncio.Task | None = None
_telegram_channel = None  # TelegramChannel | None
_serve_started_at: float = time.time()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Start Telegram channel if configured
    config = _load_runtime_config()
    await _restart_telegram_channel(config)
    yield
    # Shutdown
    global _session_manager, _pipeline_override
    await _stop_telegram_channel()
    if _session_manager is not None:
        await _session_manager.shutdown()
        _session_manager = None
    if _pipeline_override is not None:
        _pipeline_override.close()
        _pipeline_override = None


async def _start_telegram(config: RuntimeConfig) -> None:
    """Start Telegram long-polling as a background task."""
    global _telegram_channel
    from runtime.channels.telegram import (
        TelegramChannel, TelegramClient, TelegramConfig,
    )
    tg_config = TelegramConfig(
        token=config.telegram_token,
        allowlist=config.telegram_allowlist,
        poll_timeout=config.telegram_poll_timeout,
        dedupe_ttl=config.dedupe_ttl,
    )
    client = TelegramClient(tg_config.token)
    manager = get_session_manager()
    channel = TelegramChannel(client, manager, tg_config)
    _telegram_channel = channel
    _log.info("Starting Telegram channel (polling)")
    try:
        await channel.start()
    except asyncio.CancelledError:
        pass
    finally:
        if _telegram_channel is channel:
            _telegram_channel = None
        await channel.stop()


async def _stop_telegram_channel() -> None:
    """Stop the standalone web Telegram poller if it is running."""
    global _telegram_task, _telegram_channel

    task = _telegram_task
    _telegram_task = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception("Telegram channel task failed")

    if _telegram_channel is not None:
        channel = _telegram_channel
        _telegram_channel = None
        await channel.stop()


def _log_telegram_task_exception(task: asyncio.Task) -> None:
    """Surface Telegram startup/runtime errors instead of swallowing them.

    Without this callback, exceptions raised before explicit shutdown would
    only appear as an "unretrieved task exception" warning at GC time, and
    the rest of the app would keep serving requests believing Telegram was
    polling normally.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    _log.error("Telegram channel task crashed: %s", exc, exc_info=exc)


async def _restart_telegram_channel(config: RuntimeConfig) -> None:
    """Reload Telegram polling to match the latest standalone web config."""
    global _telegram_task

    await _stop_telegram_channel()
    if config.telegram_token:
        _telegram_task = asyncio.create_task(_start_telegram(config))
        _telegram_task.add_done_callback(_log_telegram_task_exception)


app = FastAPI(
    title="Project Nūr",
    version="1.0.0",
    lifespan=_lifespan,
    description=(
        "Project Nūr cognitive API. Legacy endpoints at the root serve the "
        "bundled web UI. The stable integration surface lives under /v1 "
        "(see /docs for the full OpenAPI schema)."
    ),
)

WEB_PLATFORM = "web"
RUNTIME_CONFIG_PATH = "runtime_config.yaml"

_session_manager: SessionManager | None = None
_pipeline_override: CognitivePipeline | None = None


def _current_config_for_middleware() -> RuntimeConfig:
    """Return whichever RuntimeConfig the v1 router should consult.

    Kept as a function so tests and in-process reconfiguration reflect
    immediately without re-mounting the router.
    """
    return RuntimeConfig.from_yaml(RUNTIME_CONFIG_PATH)


def _verify_bearer(token: str | None) -> None:
    """Raise 401 unless ``token`` matches the currently-configured api_key.

    If no api_key is configured, auth is disabled and this is a no-op. The
    config is re-read on every call so rotating the key through POST /config
    takes effect immediately.
    """
    expected = _current_config_for_middleware().api_key
    if not expected:
        return
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _require_bearer(
    authorization: str | None = Header(default=None),
) -> None:
    """FastAPI dependency that protects legacy endpoints when api_key is set."""
    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    _verify_bearer(token)


# CORS — origins are read from runtime_config.yaml at startup. Empty list
# means same-origin only (browsers block cross-origin XHR), which is the
# safe default. Changes to ``cors_origins`` take effect on app restart.
_cors_cfg = _current_config_for_middleware()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(_cors_cfg.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the versioned integration API at /v1.
app.include_router(
    build_v1_router(
        session_manager_getter=lambda: get_session_manager(),
        config_getter=_current_config_for_middleware,
        serve_started_at=_serve_started_at,
    )
)


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


class ConfigUpdateRequest(BaseModel):
    data_dir: str = "data"
    max_queue_per_user: int = 3
    max_active_sessions: int = 10
    session_timeout_seconds: float = 1800.0
    console_enabled: bool = True
    telegram_allowlist: list[str] = Field(default_factory=list)
    telegram_poll_timeout: int = 30
    dedupe_ttl: float = 60.0
    llm_backend: str = "auto"
    llm_base_url: str = ""
    llm_model: str = ""
    debug_host: str = "127.0.0.1"
    debug_port: int = 8077
    proactive_enabled: bool = False
    proactive_idle_threshold: float = 300.0
    proactive_max_per_session: int = 3
    proactive_cooldown: float = 300.0
    proactive_check_interval: float = 60.0
    telegram_token: str = ""
    llm_api_key: str = ""
    minimax_api_key: str = ""
    api_key: str = ""
    cors_origins: list[str] = Field(default_factory=list)
    # Tool settings. ``None`` means "leave unchanged" — without this, a
    # save of the web Settings form (which does not surface these fields)
    # would silently reset ``tools_enabled`` / ``shell_tool_enabled`` /
    # ``tools_workspace`` to their safe defaults.
    tools_enabled: bool | None = None
    tools_workspace: str | None = None
    shell_tool_enabled: bool | None = None
    clear_telegram_token: bool = False
    clear_llm_api_key: bool = False
    clear_minimax_api_key: bool = False
    clear_api_key: bool = False


class AdminConfigUpdateRequest(ConfigUpdateRequest):
    """Admin-console config update.

    Extends the existing web Settings payload with optional setup-state
    handling while preserving the same save semantics.
    """

    setup_completed: bool | None = None


class AdminLLMTestRequest(BaseModel):
    """Validate saved or draft LLM settings.

    ``live`` is intentionally opt-in so opening the admin console cannot
    accidentally call a paid or remote provider.
    """

    llm_backend: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str = ""
    minimax_api_key: str = ""
    clear_llm_api_key: bool = False
    clear_minimax_api_key: bool = False
    live: bool = False


class AdminTelegramTestRequest(BaseModel):
    telegram_token: str = ""
    clear_telegram_token: bool = False
    telegram_allowlist: list[str] | None = None


class AdminStorageTestRequest(BaseModel):
    data_dir: str | None = None
    tools_workspace: str | None = None
    create_missing: bool = True


def get_session_manager() -> SessionManager:
    """Create the shared web SessionManager lazily."""
    global _session_manager
    if _session_manager is None:
        config = _load_runtime_config()
        _session_manager = SessionManager(
            config=config,
            backend_factory=lambda: create_llm_backend(config),
            tool_executor_factory=lambda: create_tool_executor(config),
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


@app.post("/chat", dependencies=[Depends(_require_bearer)])
async def chat(req: ChatRequest):
    try:
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
    except Exception as exc:
        _log.exception("Chat error")
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)},
        )


@app.get("/debug", dependencies=[Depends(_require_bearer)])
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


@app.get("/config", dependencies=[Depends(_require_bearer)])
async def get_config() -> dict:
    config = _load_runtime_config()
    return _config_payload(config, saved=True)


@app.post("/config", dependencies=[Depends(_require_bearer)])
async def update_config(req: ConfigUpdateRequest) -> dict:
    global _session_manager

    existing = _load_runtime_config()
    config = RuntimeConfig(
        data_dir=req.data_dir.strip() or "data",
        max_queue_per_user=req.max_queue_per_user,
        max_active_sessions=req.max_active_sessions,
        session_timeout_seconds=req.session_timeout_seconds,
        console_enabled=req.console_enabled,
        telegram_token=existing.telegram_token,
        telegram_allowlist={
            item.strip()
            for item in req.telegram_allowlist
            if item.strip()
        },
        telegram_poll_timeout=req.telegram_poll_timeout,
        dedupe_ttl=req.dedupe_ttl,
        llm_backend=req.llm_backend,
        llm_base_url=req.llm_base_url.strip(),
        llm_model=req.llm_model.strip(),
        llm_api_key=existing.llm_api_key,
        minimax_api_key=existing.minimax_api_key,
        debug_host=req.debug_host.strip() or "127.0.0.1",
        debug_port=req.debug_port,
        proactive_enabled=req.proactive_enabled,
        proactive_idle_threshold=req.proactive_idle_threshold,
        proactive_max_per_session=req.proactive_max_per_session,
        proactive_cooldown=req.proactive_cooldown,
        proactive_check_interval=req.proactive_check_interval,
        api_key=existing.api_key,
        cors_origins=[o.strip() for o in req.cors_origins if o.strip()],
        tools_enabled=(
            existing.tools_enabled if req.tools_enabled is None
            else bool(req.tools_enabled)
        ),
        tools_workspace=(
            existing.tools_workspace if req.tools_workspace is None
            else req.tools_workspace.strip()
        ),
        shell_tool_enabled=(
            existing.shell_tool_enabled if req.shell_tool_enabled is None
            else bool(req.shell_tool_enabled)
        ),
    )

    if req.clear_telegram_token:
        config.telegram_token = ""
    elif req.telegram_token.strip():
        config.telegram_token = req.telegram_token.strip()

    if req.clear_llm_api_key:
        config.llm_api_key = ""
    elif req.llm_api_key.strip():
        config.llm_api_key = req.llm_api_key.strip()

    if req.clear_minimax_api_key:
        config.minimax_api_key = ""
    elif req.minimax_api_key.strip():
        config.minimax_api_key = req.minimax_api_key.strip()

    if req.clear_api_key:
        config.api_key = ""
    elif req.api_key.strip():
        config.api_key = req.api_key.strip()

    config.write_yaml(RUNTIME_CONFIG_PATH)

    reloaded_web_manager = False
    if _session_manager is not None:
        await _session_manager.shutdown()
        _session_manager = None
        reloaded_web_manager = True

    await _restart_telegram_channel(config)

    return _config_payload(
        config,
        saved=True,
        message="Configuration saved to runtime_config.yaml",
        reloaded_web_manager=reloaded_web_manager,
    )


@app.get("/admin/status", dependencies=[Depends(_require_bearer)])
async def admin_status() -> dict:
    """Operator-facing runtime/config status for the admin console."""
    config = _load_runtime_config()
    manager = get_session_manager()
    return _admin_status_payload(config, manager)


@app.get("/admin/config", dependencies=[Depends(_require_bearer)])
async def admin_get_config() -> dict:
    """Redacted config plus metadata for the admin console."""
    config = _load_runtime_config()
    return _admin_config_payload(config, saved=True)


@app.post("/admin/config", dependencies=[Depends(_require_bearer)])
async def admin_update_config(req: AdminConfigUpdateRequest) -> dict:
    """Save config through the admin-console contract."""
    result = await update_config(req)
    config = _load_runtime_config()
    if req.setup_completed is not None:
        _write_admin_state(
            config,
            setup_completed=req.setup_completed,
            last_config_save_at=time.time(),
        )
    return _admin_config_payload(
        config,
        saved=True,
        message=result.get("message"),
        reloaded_web_manager=bool(result.get("reloaded_web_manager")),
    )


@app.post("/admin/test/llm", dependencies=[Depends(_require_bearer)])
async def admin_test_llm(req: AdminLLMTestRequest) -> dict:
    """Validate LLM settings, with live provider calls opt-in."""
    config = _admin_config_for_llm_test(_load_runtime_config(), req)
    warnings = _admin_config_warnings(config)
    llm_warning_codes = {
        "missing_provider_base_url",
        "missing_provider_model",
        "missing_provider_key",
        "missing_openai_compatible_base_url",
        "missing_openai_compatible_model",
        "missing_minimax_key",
    }
    llm_warnings = [
        warning for warning in warnings
        if warning["code"] in llm_warning_codes
    ]
    if any(warning["severity"] == "error" for warning in llm_warnings):
        return {
            "ok": False,
            "checked": "llm",
            "live": False,
            "backend": config.llm_backend,
            "warnings": llm_warnings,
        }

    result = {
        "ok": True,
        "checked": "llm",
        "live": False,
        "backend": config.llm_backend,
        "warnings": llm_warnings,
    }
    if req.live or config.llm_backend == "mock":
        try:
            backend = create_llm_backend(config)
            sample = backend.generate("Reply with ok.", "health check")
            result.update({
                "live": True,
                "sample_response": sample[:200],
            })
        except Exception as exc:
            result.update({
                "ok": False,
                "live": True,
                "error": _redact_error(str(exc), config),
            })
    return result


@app.post("/admin/test/telegram", dependencies=[Depends(_require_bearer)])
async def admin_test_telegram(req: AdminTelegramTestRequest) -> dict:
    """Validate Telegram settings without starting long polling."""
    config = _admin_config_for_telegram_test(_load_runtime_config(), req)
    warnings: list[dict] = []
    if not config.telegram_token:
        warnings.append(_warning("error", "telegram_token", "missing_telegram_token", "Telegram bot token is not configured."))
    elif ":" not in config.telegram_token:
        warnings.append(_warning("warning", "telegram_token", "telegram_token_shape", "Telegram bot tokens usually contain a ':' separator."))

    invalid_allowlist = [
        item for item in config.telegram_allowlist
        if item and not item.lstrip("-").isdigit()
    ]
    if invalid_allowlist:
        warnings.append(_warning("warning", "telegram_allowlist", "telegram_allowlist_non_numeric", "Telegram allowlist should contain numeric user IDs."))

    return {
        "ok": not any(warning["severity"] == "error" for warning in warnings),
        "checked": "telegram",
        "configured": bool(config.telegram_token),
        "allowlist_count": len(config.telegram_allowlist),
        "warnings": warnings,
    }


@app.post("/admin/test/storage", dependencies=[Depends(_require_bearer)])
async def admin_test_storage(req: AdminStorageTestRequest) -> dict:
    """Validate data and tool workspace paths."""
    config = _admin_config_for_storage_test(_load_runtime_config(), req)
    checks = [
        _check_writable_directory("data_dir", config.data_dir, req.create_missing),
        _check_writable_directory(
            "tools_workspace",
            config.resolved_tools_workspace,
            req.create_missing,
        ),
    ]
    warnings = [
        warning for check in checks
        for warning in check.get("warnings", [])
    ]
    return {
        "ok": all(check["ok"] for check in checks),
        "checked": "storage",
        "checks": checks,
        "warnings": warnings,
    }


@app.post("/session/end", dependencies=[Depends(_require_bearer)])
async def end_session(req: EndSessionRequest) -> dict:
    if _pipeline_override is not None:
        result = _pipeline_override.end_session(user_id=req.user_id)
        return _digested_to_dict(result)

    manager = get_session_manager()
    session = await manager.ensure_session(WEB_PLATFORM, req.user_id, req.chat_id)
    result = await session.end_session()
    return _digested_to_dict(result)


@app.post("/rest", dependencies=[Depends(_require_bearer)])
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
    # FastAPI does not run route dependencies for WebSocket handlers, so do
    # the bearer-token check inline. Browser WS clients cannot set custom
    # headers, so also accept the token as a ?token=... query param.
    expected = _current_config_for_middleware().api_key
    if expected:
        header_token: str | None = None
        auth_header = ws.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            header_token = auth_header.split(" ", 1)[1].strip()
        query_token = ws.query_params.get("token")
        supplied = header_token or query_token
        if supplied != expected:
            # Close with a policy-violation code before accepting so
            # unauthenticated clients cannot hold a connection open.
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
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
    return _index_response()


@app.get("/admin")
def admin_index() -> HTMLResponse:
    return _index_response()


def _index_response() -> HTMLResponse:
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


def _load_runtime_config() -> RuntimeConfig:
    """Load the runtime config file used by both web and runtime surfaces."""
    return RuntimeConfig.from_yaml(RUNTIME_CONFIG_PATH)


def _config_payload(
    config: RuntimeConfig,
    *,
    saved: bool,
    message: str | None = None,
    reloaded_web_manager: bool = False,
) -> dict:
    """Serialize runtime config for the settings UI."""
    return {
        "config": config.to_public_dict(),
        "secret_status": config.secret_status(),
        "config_path": os.path.abspath(RUNTIME_CONFIG_PATH),
        "saved": saved,
        "message": message,
        "reloaded_web_manager": reloaded_web_manager,
        "notes": [
            "Secret fields are never returned; leave them blank to keep the current value.",
            "Saving through this UI updates runtime_config.yaml.",
            "The standalone web server reloads its web manager and Telegram poller after save.",
            "Changes for nur / python3 main.py still apply after restarting that runtime.",
        ],
    }


_SECRET_ENV_VARS = {
    "telegram_token": (),
    "llm_api_key": ("LLM_API_KEY",),
    "minimax_api_key": ("MINIMAX_API_KEY",),
    "api_key": (),
}

_CONFIG_FIELD_SECTIONS = {
    "data_dir": "runtime",
    "max_queue_per_user": "runtime",
    "max_active_sessions": "runtime",
    "session_timeout_seconds": "runtime",
    "console_enabled": "runtime",
    "debug_host": "runtime",
    "debug_port": "runtime",
    "llm_backend": "model",
    "llm_base_url": "model",
    "llm_model": "model",
    "llm_api_key": "model",
    "minimax_api_key": "model",
    "api_key": "access",
    "cors_origins": "access",
    "telegram_token": "channels",
    "telegram_allowlist": "channels",
    "telegram_poll_timeout": "channels",
    "dedupe_ttl": "channels",
    "tools_enabled": "tools",
    "tools_workspace": "tools",
    "shell_tool_enabled": "tools",
    "proactive_enabled": "runtime",
    "proactive_idle_threshold": "runtime",
    "proactive_max_per_session": "runtime",
    "proactive_cooldown": "runtime",
    "proactive_check_interval": "runtime",
}

_RESTART_REQUIRED_FIELDS = {"debug_host", "debug_port", "cors_origins"}
_SESSION_RELOAD_FIELDS = {
    "data_dir",
    "max_queue_per_user",
    "max_active_sessions",
    "session_timeout_seconds",
    "llm_backend",
    "llm_base_url",
    "llm_model",
    "llm_api_key",
    "minimax_api_key",
    "proactive_enabled",
    "proactive_idle_threshold",
    "proactive_max_per_session",
    "proactive_cooldown",
    "proactive_check_interval",
    "tools_enabled",
    "tools_workspace",
    "shell_tool_enabled",
}
_LIVE_RELOAD_FIELDS = {"api_key", "telegram_token", "telegram_allowlist", "telegram_poll_timeout", "dedupe_ttl"}


def _admin_config_payload(
    config: RuntimeConfig,
    *,
    saved: bool,
    message: str | None = None,
    reloaded_web_manager: bool = False,
) -> dict:
    """Serialize config and metadata for the production admin console."""
    return {
        **_config_payload(
            config,
            saved=saved,
            message=message,
            reloaded_web_manager=reloaded_web_manager,
        ),
        "field_metadata": _admin_field_metadata(config),
        "warnings": _admin_config_warnings(config),
        "setup": _admin_setup_status(config),
    }


def _admin_status_payload(config: RuntimeConfig, manager: SessionManager) -> dict:
    """Operator-facing status summary used by the admin overview."""
    return {
        "status": "ok",
        "config_path": os.path.abspath(RUNTIME_CONFIG_PATH),
        "auth_enabled": bool(config.api_key),
        "llm_backend": config.llm_backend,
        "llm_configured": _llm_configured(config),
        "telegram_configured": bool(config.telegram_token),
        "tools": {
            "enabled": config.tools_enabled,
            "workspace": config.resolved_tools_workspace,
            "shell_enabled": config.shell_tool_enabled,
        },
        "sessions": {
            "active": len(manager.active_sessions),
            "max_active": config.max_active_sessions,
        },
        "setup": _admin_setup_status(config),
        "warnings": _admin_config_warnings(config),
    }


def _admin_field_metadata(config: RuntimeConfig) -> list[dict]:
    defaults = RuntimeConfig()
    public_values = config.to_public_dict()
    secret_status = _admin_secret_status(config)
    fields_meta: list[dict] = []
    for field_name, value in public_values.items():
        metadata = {
            "name": field_name,
            "section": _CONFIG_FIELD_SECTIONS.get(field_name, "runtime"),
            "value": value,
            "default": getattr(defaults, field_name),
            "secret": field_name in secret_status,
            "restart_required": field_name in _RESTART_REQUIRED_FIELDS,
            "session_manager_reload": field_name in _SESSION_RELOAD_FIELDS,
            "live_reload": field_name in _LIVE_RELOAD_FIELDS,
            "allowed_values": _allowed_values_for_field(field_name),
        }
        if field_name in secret_status:
            metadata["secret_status"] = secret_status[field_name]
        fields_meta.append(metadata)
    return fields_meta


def _admin_secret_status(config: RuntimeConfig) -> dict[str, dict]:
    status: dict[str, dict] = {}
    for field_name, env_names in _SECRET_ENV_VARS.items():
        has_yaml_value = bool(getattr(config, field_name))
        configured_env = next((name for name in env_names if os.environ.get(name)), "")
        status[field_name] = {
            "configured": has_yaml_value or bool(configured_env),
            "stored": has_yaml_value,
            "source": "yaml" if has_yaml_value else ("environment" if configured_env else "unset"),
            "env_var": configured_env,
        }
    return status


def _allowed_values_for_field(field_name: str) -> list[str] | None:
    if field_name == "llm_backend":
        return ["auto", "provider", "openai_compatible", "minimax", "mock"]
    return None


def _admin_config_warnings(config: RuntimeConfig) -> list[dict]:
    warnings: list[dict] = []
    backend = config.llm_backend
    secret_status = _admin_secret_status(config)
    has_generic_key = secret_status["llm_api_key"]["configured"]
    has_minimax_key = secret_status["minimax_api_key"]["configured"]

    if backend == "provider":
        if not config.llm_base_url.strip():
            warnings.append(_warning("error", "llm_base_url", "missing_provider_base_url", "Hosted provider backend requires llm_base_url."))
        if not config.llm_model.strip():
            warnings.append(_warning("error", "llm_model", "missing_provider_model", "Hosted provider backend requires llm_model."))
        if not has_generic_key:
            warnings.append(_warning("warning", "llm_api_key", "missing_provider_key", "Hosted provider backend usually requires an API key."))
    elif backend == "openai_compatible":
        if not config.llm_base_url.strip():
            warnings.append(_warning("error", "llm_base_url", "missing_openai_compatible_base_url", "OpenAI-compatible backend requires llm_base_url."))
        if not config.llm_model.strip():
            warnings.append(_warning("error", "llm_model", "missing_openai_compatible_model", "OpenAI-compatible backend requires llm_model."))
    elif backend == "minimax" and not has_minimax_key:
        warnings.append(_warning("error", "minimax_api_key", "missing_minimax_key", "MiniMax backend requires minimax_api_key or MINIMAX_API_KEY."))

    if config.tools_enabled and not config.api_key:
        warnings.append(_warning("error", "tools_enabled", "tools_without_auth", "Agentic tools are enabled while api_key is empty. Set api_key before exposing this server."))
    if config.shell_tool_enabled and not config.tools_enabled:
        warnings.append(_warning("error", "shell_tool_enabled", "shell_without_tools", "shell_tool_enabled has no effect unless tools_enabled is true."))
    if config.shell_tool_enabled and not config.api_key:
        warnings.append(_warning("error", "shell_tool_enabled", "shell_without_auth", "Shell tool execution requires a protected admin/API surface."))
    if config.cors_origins and not config.api_key:
        warnings.append(_warning("error", "cors_origins", "cors_without_auth", "CORS origins are configured while api_key is empty."))
    if config.debug_host not in {"127.0.0.1", "localhost", "::1"} and not config.api_key:
        warnings.append(_warning("error", "api_key", "public_bind_without_auth", "Server bind host is not localhost while api_key is empty."))

    return warnings


def _warning(severity: str, field: str, code: str, message: str) -> dict:
    return {
        "severity": severity,
        "field": field,
        "code": code,
        "message": message,
    }


def _llm_configured(config: RuntimeConfig) -> bool:
    backend = config.llm_backend
    secret_status = _admin_secret_status(config)
    if backend == "mock":
        return True
    if backend == "provider":
        return bool(
            config.llm_base_url.strip()
            and config.llm_model.strip()
            and secret_status["llm_api_key"]["configured"]
        )
    if backend == "openai_compatible":
        return bool(config.llm_base_url.strip() and config.llm_model.strip())
    if backend == "minimax":
        return secret_status["minimax_api_key"]["configured"]
    if backend == "auto":
        return bool(
            config.llm_base_url.strip()
            or secret_status["llm_api_key"]["configured"]
            or secret_status["minimax_api_key"]["configured"]
        )
    return False


def _admin_state_path(config: RuntimeConfig) -> str:
    return os.path.join(config.data_dir, "admin_state.json")


def _read_admin_state(config: RuntimeConfig) -> dict:
    path = _admin_state_path(config)
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_admin_state(
    config: RuntimeConfig,
    *,
    setup_completed: bool,
    last_config_save_at: float | None = None,
) -> None:
    path = _admin_state_path(config)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = _read_admin_state(config)
    now = time.time()
    data = {
        **existing,
        "setup_completed": setup_completed,
        "completed_at": existing.get("completed_at") if setup_completed else None,
        "last_config_save_at": last_config_save_at if last_config_save_at is not None else now,
    }
    if setup_completed and not data["completed_at"]:
        data["completed_at"] = now
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def _admin_setup_status(config: RuntimeConfig) -> dict:
    state = _read_admin_state(config)
    completed = bool(state.get("setup_completed"))
    reasons: list[str] = []
    if not os.path.exists(RUNTIME_CONFIG_PATH):
        reasons.append("missing_config")
    if _looks_unconfigured(config):
        reasons.append("default_or_minimal_config")
    if not completed:
        reasons.append("setup_not_completed")
    required = not completed and bool(reasons)
    return {
        "required": required,
        "completed": completed,
        "state_path": os.path.abspath(_admin_state_path(config)),
        "reasons": reasons,
        "completed_at": state.get("completed_at"),
        "last_config_save_at": state.get("last_config_save_at"),
    }


def _looks_unconfigured(config: RuntimeConfig) -> bool:
    return (
        config.llm_backend in {"auto", "mock"}
        and not config.llm_base_url.strip()
        and not config.llm_model.strip()
        and not config.llm_api_key
        and not config.minimax_api_key
        and not config.telegram_token
        and not config.api_key
        and not config.tools_enabled
    )


def _admin_config_for_llm_test(
    existing: RuntimeConfig,
    req: AdminLLMTestRequest,
) -> RuntimeConfig:
    config = RuntimeConfig(**existing.to_yaml_dict())
    if req.llm_backend is not None:
        config.llm_backend = req.llm_backend
    if req.llm_base_url is not None:
        config.llm_base_url = req.llm_base_url.strip()
    if req.llm_model is not None:
        config.llm_model = req.llm_model.strip()
    if req.clear_llm_api_key:
        config.llm_api_key = ""
    elif req.llm_api_key.strip():
        config.llm_api_key = req.llm_api_key.strip()
    if req.clear_minimax_api_key:
        config.minimax_api_key = ""
    elif req.minimax_api_key.strip():
        config.minimax_api_key = req.minimax_api_key.strip()
    return config


def _admin_config_for_telegram_test(
    existing: RuntimeConfig,
    req: AdminTelegramTestRequest,
) -> RuntimeConfig:
    config = RuntimeConfig(**existing.to_yaml_dict())
    if req.clear_telegram_token:
        config.telegram_token = ""
    elif req.telegram_token.strip():
        config.telegram_token = req.telegram_token.strip()
    if req.telegram_allowlist is not None:
        config.telegram_allowlist = {
            item.strip()
            for item in req.telegram_allowlist
            if item.strip()
        }
    return config


def _admin_config_for_storage_test(
    existing: RuntimeConfig,
    req: AdminStorageTestRequest,
) -> RuntimeConfig:
    config = RuntimeConfig(**existing.to_yaml_dict())
    if req.data_dir is not None:
        config.data_dir = req.data_dir.strip() or "data"
    if req.tools_workspace is not None:
        config.tools_workspace = req.tools_workspace.strip()
    return config


def _check_writable_directory(
    name: str,
    path: str,
    create_missing: bool,
) -> dict:
    resolved = os.path.realpath(path)
    warnings: list[dict] = []
    try:
        if create_missing:
            os.makedirs(resolved, exist_ok=True)
        exists = os.path.isdir(resolved)
        if not exists:
            warnings.append(_warning("error", name, f"{name}_missing", f"{name} does not exist."))
            return {
                "name": name,
                "ok": False,
                "path": resolved,
                "exists": False,
                "writable": False,
                "warnings": warnings,
            }
        writable = os.access(resolved, os.W_OK)
        if not writable:
            warnings.append(_warning("error", name, f"{name}_not_writable", f"{name} is not writable."))
        return {
            "name": name,
            "ok": writable,
            "path": resolved,
            "exists": exists,
            "writable": writable,
            "warnings": warnings,
        }
    except OSError as exc:
        warnings.append(_warning("error", name, f"{name}_error", str(exc)))
        return {
            "name": name,
            "ok": False,
            "path": resolved,
            "exists": False,
            "writable": False,
            "warnings": warnings,
        }


def _redact_error(message: str, config: RuntimeConfig) -> str:
    redacted = message
    for secret in (
        config.telegram_token,
        config.llm_api_key,
        config.minimax_api_key,
        config.api_key,
    ):
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def main() -> None:
    """Launch the standalone web UI on localhost."""
    import uvicorn

    uvicorn.run("interface.api:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
