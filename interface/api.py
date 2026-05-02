"""FastAPI backend for Project Nūr."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import platform
import secrets
import shutil
import sys
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

from interface.v1 import build_v1_router, _count_user_rows, _validate_path_token
from pipeline import CognitivePipeline
from runtime.config import RuntimeConfig
from runtime.debug.api import _debug_to_dict as _serialize_debug
from runtime.debug.persona_view import build_persona_view
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
        from runtime.channels.telegram import is_telegram_token_pollable

        if not is_telegram_token_pollable(config.telegram_token):
            _log.warning(
                "Telegram token is configured but malformed; polling is not started"
            )
            return
        _telegram_task = asyncio.create_task(_start_telegram(config))
        _telegram_task.add_done_callback(_log_telegram_task_exception)


app = FastAPI(
    title="Project Nūr",
    version="1.0.0",
    lifespan=_lifespan,
    docs_url=None if os.environ.get("NUR_DISABLE_DOCS") else "/docs",
    redoc_url=None if os.environ.get("NUR_DISABLE_DOCS") else "/redoc",
    openapi_url=None if os.environ.get("NUR_DISABLE_DOCS") else "/openapi.json",
    description=(
        "Project Nūr cognitive API. Legacy endpoints at the root serve the "
        "bundled web UI. The stable integration surface lives under /v1 "
        "(see /docs for the full OpenAPI schema)."
    ),
)

WEB_PLATFORM = "web"
RUNTIME_CONFIG_PATH = os.environ.get("NUR_RUNTIME_CONFIG", "runtime_config.yaml")
_MAX_CHAT_MESSAGE_CHARS = 16_000
_MAX_ID_CHARS = 128
_MAX_WS_MESSAGE_CHARS = 20_000
_MAX_LIFE_UPLOAD_BYTES = 10_000_000
_MAX_SKILL_MARKDOWN_UPLOAD_BYTES = 1_000_000
_MAX_SKILL_ARCHIVE_UPLOAD_BYTES = 25_000_000
_MAX_SKILL_ARCHIVE_FILES = 250
_LIFE_UPLOAD_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".text"}
_SKILL_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_HTML_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "connect-src 'self' http://127.0.0.1:* http://localhost:*; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    ),
}

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
    expected = str(_current_config_for_middleware().api_key or "")
    if not expected:
        return
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(token, expected):
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
    message: str = Field(..., min_length=1, max_length=_MAX_CHAT_MESSAGE_CHARS)
    user_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)
    chat_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)


class ChatResponse(BaseModel):
    response: str
    debug: dict


class EndSessionRequest(BaseModel):
    user_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)
    chat_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)


class RestRequest(BaseModel):
    hours: float = 1.0
    user_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)
    chat_id: str = Field("default", min_length=1, max_length=_MAX_ID_CHARS)


class ConfigUpdateRequest(BaseModel):
    data_dir: str = "data"
    max_queue_per_user: int = Field(3, ge=1)
    max_active_sessions: int = Field(10, ge=1)
    session_timeout_seconds: float = Field(1800.0, ge=1)
    console_enabled: bool = True
    telegram_allowlist: list[str] = Field(default_factory=list)
    telegram_poll_timeout: int = Field(30, ge=1)
    dedupe_ttl: float = Field(60.0, ge=0)
    llm_backend: str = "auto"
    llm_base_url: str = ""
    llm_model: str = ""
    debug_host: str = "127.0.0.1"
    debug_port: int = Field(8077, ge=1, le=65535)
    proactive_enabled: bool = False
    proactive_idle_threshold: float = Field(300.0, ge=0)
    proactive_max_per_session: int = Field(3, ge=0)
    proactive_cooldown: float = Field(300.0, ge=0)
    proactive_check_interval: float = Field(60.0, ge=1)
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
    autonomy_level: str | None = None
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


class AdminBackupRequest(BaseModel):
    include_data: bool = True


class AdminBackupDeleteRequest(BaseModel):
    filename: str
    confirmation: str


class AdminRuntimeRestartRequest(BaseModel):
    confirmation: str


class AdminSessionResetRequest(BaseModel):
    session_key: str
    confirmation: str


class AdminUserDeleteRequest(BaseModel):
    platform: str
    user_id: str
    confirmation: str


class AdminSkillImportRequest(BaseModel):
    source_path: str | None = Field(default=None, max_length=4096)
    skill_markdown: str | None = Field(default=None, max_length=250000)
    name_hint: str = Field(default="", max_length=128)


class AdminLifeTextRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=180)
    text: str = Field(..., min_length=1, max_length=250000)
    source_type: str = Field("pasted_text", min_length=1, max_length=80)
    participants: list[str] = Field(default_factory=list, max_length=20)


class AdminLifeFileRequest(BaseModel):
    file_path: str = Field(..., min_length=1, max_length=4096)
    title: str = Field(default="", max_length=180)
    participants: list[str] = Field(default_factory=list, max_length=20)


class AdminLifeRollbackRequest(BaseModel):
    batch_id: str = Field(..., min_length=1, max_length=80)


class AdminSoulDraftRequest(BaseModel):
    """LLM-assisted soul drafting from a natural-language description."""

    description: str = Field(..., min_length=8, max_length=16000)

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 8:
            raise ValueError("description must be at least 8 characters")
        return stripped


class AdminSoulImportRequest(BaseModel):
    """Raw soul.yaml or identity document pasted by an operator."""

    raw: str = Field(..., min_length=8, max_length=20000)

    @field_validator("raw")
    @classmethod
    def _strip_raw(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 8:
            raise ValueError("raw must be at least 8 characters")
        return stripped


class AdminSoulUpdateRequest(BaseModel):
    """Validated soul.yaml replacement.

    Weights must be in [0.0, 1.0]; free-text fields are bounded so a pasted
    book cannot become the identity prompt; lists are trimmed to non-empty
    entries.
    """

    name: str = Field(..., max_length=64)
    identity: str = Field(default="", max_length=2000)
    voice: str = Field(default="", max_length=2000)
    relational_stance: str = Field(default="", max_length=2000)
    growth_policy: str = Field(default="", max_length=2000)
    likes: list[str] = Field(default_factory=list, max_length=32)
    dislikes: list[str] = Field(default_factory=list, max_length=32)
    boundaries: list[str] = Field(default_factory=list, max_length=32)
    core_values: dict[str, float] = Field(default_factory=dict)
    initial_traits: dict[str, float] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name is required and cannot be blank")
        return stripped

    @field_validator("identity", "voice", "relational_stance", "growth_policy")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("likes", "dislikes", "boundaries")
    @classmethod
    def _strip_items(cls, values: list[str]) -> list[str]:
        cleaned = [item.strip() for item in values]
        cleaned = [item for item in cleaned if item]
        for item in cleaned:
            if len(item) > 200:
                raise ValueError(f"item too long (max 200 chars): {item[:40]}...")
        return cleaned

    @field_validator("core_values", "initial_traits")
    @classmethod
    def _validate_weights(cls, values: dict[str, float]) -> dict[str, float]:
        if len(values) > 32:
            raise ValueError("too many entries (max 32)")
        cleaned: dict[str, float] = {}
        for key, raw in values.items():
            key = (key or "").strip()
            if not key:
                raise ValueError("weight key cannot be empty")
            if len(key) > 64:
                raise ValueError(f"weight key too long (max 64 chars): {key[:40]}...")
            try:
                weight = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"weight for {key!r} must be numeric") from exc
            if not 0.0 <= weight <= 1.0:
                raise ValueError(
                    f"weight for {key!r} must be in [0.0, 1.0], got {weight}"
                )
            cleaned[key] = weight
        return cleaned


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


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#13131d"/>'
        '<circle cx="16" cy="16" r="8" fill="#b8a0ff"/>'
        "</svg>"
    )
    return Response(content=svg, media_type="image/svg+xml")


@app.post("/chat", dependencies=[Depends(_require_bearer)])
async def chat(req: ChatRequest):
    try:
        if _pipeline_override is not None:
            result = _pipeline_override.process(req.message, user_id=req.user_id)
            return ChatResponse(response=result.response, debug=_serialize_debug(result.debug))

        manager = get_session_manager()
        try:
            response = await manager.handle_message(
                WEB_PLATFORM,
                req.user_id,
                req.chat_id,
                req.message,
            )
        except RuntimeError as exc:
            _log.warning("Chat unavailable: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        session = await manager.ensure_session(WEB_PLATFORM, req.user_id, req.chat_id)
        debug = _serialize_debug(session.last_debug) if session.last_debug else {}
        return ChatResponse(response=response, debug=debug)
    except HTTPException:
        raise
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
        autonomy_level=(
            existing.autonomy_level
            if req.autonomy_level is None
            else _normalize_autonomy_level(req.autonomy_level)
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

    changed_fields = _changed_config_fields(existing, config)
    restart_required_fields = sorted(
        field for field in changed_fields if field in _RESTART_REQUIRED_FIELDS
    )
    config.write_yaml(RUNTIME_CONFIG_PATH)

    reload_result = await _reload_runtime_from_config(config)

    return _config_payload(
        config,
        saved=True,
        message="Configuration saved to runtime_config.yaml",
        reloaded_web_manager=bool(reload_result["reloaded_web_manager"]),
        restart_required_fields=restart_required_fields,
        runtime_reload=reload_result,
    )


@app.get("/admin/status", dependencies=[Depends(_require_bearer)])
async def admin_status() -> dict:
    """Operator-facing runtime/config status for the admin console."""
    config = _load_runtime_config()
    manager = get_session_manager()
    return _admin_status_payload(config, manager)


@app.get("/admin/persona/state", dependencies=[Depends(_require_bearer)])
async def admin_persona_state() -> dict:
    """Unified runtime persona dashboard state across all active channels.

    This is read-only observability over the shared SessionManager. It does
    not create sessions, call the pipeline, or mutate memory/emotional state.
    """
    return _admin_persona_payload(get_session_manager())


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
        restart_required_fields=(
            result.get("apply_state", {}).get("restart_required_fields", [])
        ),
        runtime_reload=result.get("runtime_reload", {}),
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
        "ok": False,
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
                "ok": True,
                "live": True,
                "sample_response": sample[:200],
            })
        except Exception as exc:
            result.update({
                "ok": False,
                "live": True,
                "error": _redact_error(str(exc), config),
            })
    else:
        result["error"] = "Live LLM test was not run; set live=true to verify connectivity."
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


@app.get("/admin/diagnostics", dependencies=[Depends(_require_bearer)])
async def admin_diagnostics() -> dict:
    """Operational diagnostics for the admin maintenance panel."""
    config = _load_runtime_config()
    manager = get_session_manager()
    return _admin_diagnostics_payload(config, manager)


@app.post("/admin/runtime/reload", dependencies=[Depends(_require_bearer)])
async def admin_reload_runtime() -> dict:
    """Apply the saved runtime config without a full process restart.

    This evicts active web sessions so the next turn rebuilds pipelines from
    ``runtime_config.yaml`` and restarts the standalone Telegram poller.
    Host/port/CORS still require a web-server process restart.
    """
    config = _load_runtime_config()
    result = await _reload_runtime_from_config(config)
    return {
        "ok": True,
        "action": "runtime_reload",
        "message": "Saved runtime config applied to web sessions and Telegram polling.",
        **result,
        "restart_required_fields": sorted(_RESTART_REQUIRED_FIELDS),
        "restart_note": (
            "debug_host, debug_port, and cors_origins are bound by the web "
            "server process and require Restart Web Server."
        ),
    }


@app.post("/admin/runtime/restart", dependencies=[Depends(_require_bearer)])
async def admin_restart_runtime(req: AdminRuntimeRestartRequest) -> dict:
    """Schedule a local process restart after the HTTP response is sent."""
    if req.confirmation.strip() != "RESTART":
        raise HTTPException(
            status_code=400,
            detail="Confirmation must exactly match: RESTART",
        )
    drain_result = await _drain_runtime_before_process_restart()
    argv = _schedule_process_restart()
    return {
        "ok": True,
        "action": "process_restart",
        "scheduled": True,
        "message": "Web server restart scheduled.",
        "runtime_drain": drain_result,
        "argv": argv,
    }


@app.get("/admin/export/config", dependencies=[Depends(_require_bearer)])
async def admin_export_config() -> dict:
    """Return a redacted runtime configuration export."""
    config = _load_runtime_config()
    return _admin_redacted_config_export(config)


@app.post("/admin/backup", dependencies=[Depends(_require_bearer)])
async def admin_create_backup(req: AdminBackupRequest) -> dict:
    """Create a local zip backup under the configured data directory."""
    config = _load_runtime_config()
    return _create_admin_backup(config, include_data=req.include_data)


@app.get("/admin/backups", dependencies=[Depends(_require_bearer)])
async def admin_list_backups() -> dict:
    """List local admin backup archives."""
    config = _load_runtime_config()
    return _list_admin_backups(config)


@app.post("/admin/backups/delete", dependencies=[Depends(_require_bearer)])
async def admin_delete_backup(req: AdminBackupDeleteRequest) -> dict:
    """Delete one local admin backup archive after explicit confirmation."""
    config = _load_runtime_config()
    return _delete_admin_backup(
        config,
        filename=req.filename,
        confirmation=req.confirmation,
    )


@app.post("/admin/sessions/reset", dependencies=[Depends(_require_bearer)])
async def admin_reset_session(req: AdminSessionResetRequest) -> dict:
    """Evict one active session after explicit typed confirmation."""
    expected = f"RESET {req.session_key}"
    if req.confirmation != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation must exactly match: {expected}",
        )
    manager = get_session_manager()
    if req.session_key not in manager.active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    await manager.evict_session(req.session_key)
    return {
        "ok": True,
        "action": "session_reset",
        "session_key": req.session_key,
        "status": "evicted",
    }


@app.get("/admin/soul", dependencies=[Depends(_require_bearer)])
async def admin_get_soul() -> dict:
    """Return current seed identity for the admin Identity section."""
    from config.loader import get_config

    return _admin_soul_payload(get_config().soul, saved=True)


@app.post("/admin/soul/draft", dependencies=[Depends(_require_bearer)])
async def admin_draft_soul(req: AdminSoulDraftRequest) -> dict:
    """Draft a soul.yaml from a natural-language description via the LLM.

    Returns a validated draft; does NOT persist. The UI populates the
    Identity form from the draft so the operator can review and edit
    before saving.
    """
    config = _load_runtime_config()
    if not _llm_configured_for_draft(config):
        raise HTTPException(
            status_code=400,
            detail=(
                "LLM not configured. Set an LLM backend in Settings and run "
                "the LLM test before using the 'Describe your agent' helper."
            ),
        )
    prompt_template = _load_soul_draft_prompt()
    if not prompt_template:
        raise HTTPException(
            status_code=500,
            detail="Soul draft prompt template not found in config/prompts/.",
        )
    system_prompt = prompt_template.replace("{{description}}", req.description)
    try:
        backend = create_llm_backend(config)
        raw = backend.generate(system_prompt, req.description)
    except Exception as exc:  # pragma: no cover - depends on provider
        raise HTTPException(
            status_code=502,
            detail=f"LLM call failed: {exc.__class__.__name__}: {exc}",
        )
    payload = _parse_soul_draft_json(raw)
    try:
        draft = AdminSoulUpdateRequest.model_validate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM draft did not match the soul schema: {exc}",
        )
    return {
        "ok": True,
        "draft": draft.model_dump(),
        "backend": config.llm_backend,
        "notes": [
            "This is a draft, not a save. Review the fields and press Save Identity to commit.",
            "LLM-authored drafts may drift from the description on second reading; edit anything that feels off.",
        ],
    }


@app.post("/admin/soul/import", dependencies=[Depends(_require_bearer)])
async def admin_import_soul(req: AdminSoulImportRequest) -> dict:
    """Parse pasted soul.yaml or a plain identity document without an LLM call."""
    payload, source = _parse_raw_soul_document(req.raw)
    try:
        draft = AdminSoulUpdateRequest.model_validate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Pasted identity did not match the soul schema: {exc}",
        )
    return {
        "ok": True,
        "draft": draft.model_dump(),
        "source": source,
        "notes": [
            "Parsed locally. Review the fields and press Save Identity to commit.",
        ],
    }


@app.post("/admin/soul", dependencies=[Depends(_require_bearer)])
async def admin_update_soul(req: AdminSoulUpdateRequest) -> dict:
    """Write a new soul.yaml, reload the config singleton, and evict the
    cached session manager so the next chat turn builds a fresh pipeline
    that reads the new identity. No process restart required.
    """
    global _session_manager
    from config.loader import get_config, reset_config

    payload = {
        "soul": {
            "name": req.name,
            "identity": req.identity,
            "voice": req.voice,
            "relational_stance": req.relational_stance,
            "likes": req.likes,
            "dislikes": req.dislikes,
            "boundaries": req.boundaries,
            "growth_policy": req.growth_policy,
            "core_values": req.core_values,
            "initial_traits": req.initial_traits,
        }
    }
    path = _soul_yaml_path()
    _write_soul_yaml(payload, path)
    reset_config()
    reloaded_manager = False
    if _session_manager is not None:
        await _session_manager.shutdown()
        _session_manager = None
        reloaded_manager = True
    return _admin_soul_payload(
        get_config().soul,
        saved=True,
        path=path,
        reloaded_session_manager=reloaded_manager,
    )


@app.post("/admin/users/delete", dependencies=[Depends(_require_bearer)])
async def admin_delete_user(req: AdminUserDeleteRequest) -> dict:
    """Delete one user's persisted data after explicit typed confirmation."""
    rel_key = f"{req.platform}:{req.user_id}"
    expected = f"DELETE {rel_key}"
    if req.confirmation != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation must exactly match: {expected}",
        )
    _validate_path_token("platform", req.platform)
    _validate_path_token("user_id", req.user_id)
    return await _admin_delete_user_data(
        _load_runtime_config(),
        get_session_manager(),
        req.platform,
        req.user_id,
    )


@app.get("/admin/skills", dependencies=[Depends(_require_bearer)])
async def admin_list_skills() -> dict:
    """List imported skills and their latest compatibility report."""
    from runtime.skills import SkillError, list_skills

    try:
        return list_skills(_load_runtime_config())
    except SkillError as exc:
        _raise_skill_http_error(exc)


@app.post("/admin/skills/import", dependencies=[Depends(_require_bearer)])
async def admin_import_skill(req: AdminSkillImportRequest) -> dict:
    """Import a skill folder or pasted SKILL.md for review.

    Imported skills are disabled until explicitly enabled. This endpoint does
    not inject any skill into generation prompts.
    """
    from runtime.skills import SkillError, import_skill

    try:
        skill = import_skill(
            _load_runtime_config(),
            source_path=req.source_path,
            skill_markdown=req.skill_markdown,
            name_hint=req.name_hint,
        )
    except SkillError as exc:
        _raise_skill_http_error(exc)
    return {"ok": True, "skill": skill}


@app.post("/admin/skills/import/upload", dependencies=[Depends(_require_bearer)])
async def admin_import_skill_upload(
    file: UploadFile = File(...),
    name_hint: str = Form(""),
) -> dict:
    """Import an uploaded SKILL.md/Markdown file or zipped skill folder."""
    from runtime.skills import SkillError, import_skill

    filename = _upload_filename(file, fallback="skill-upload")
    suffix = Path(filename).suffix.lower()
    config = _load_runtime_config()
    try:
        if suffix == ".zip":
            payload = await _read_upload_bytes(
                file,
                max_bytes=_MAX_SKILL_ARCHIVE_UPLOAD_BYTES,
                label="Skill archive",
            )
            with tempfile.TemporaryDirectory(prefix="nur-skill-upload-") as tmp:
                extract_root = Path(tmp) / "skill"
                extract_root.mkdir(parents=True, exist_ok=True)
                _safe_extract_zip(payload, extract_root)
                source_root = _single_skill_root_from_upload(extract_root)
                skill = import_skill(
                    config,
                    source_path=str(source_root),
                    name_hint=name_hint,
                )
        elif suffix in _SKILL_MARKDOWN_SUFFIXES:
            payload = await _read_upload_bytes(
                file,
                max_bytes=_MAX_SKILL_MARKDOWN_UPLOAD_BYTES,
                label="Skill Markdown",
            )
            skill = import_skill(
                config,
                skill_markdown=payload.decode("utf-8", errors="replace"),
                name_hint=name_hint,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="Upload SKILL.md, a Markdown skill file, or a .zip skill folder.",
            )
    except SkillError as exc:
        _raise_skill_http_error(exc)
    return {"ok": True, "skill": skill}


@app.get("/admin/skills/{skill_id}", dependencies=[Depends(_require_bearer)])
async def admin_get_skill(skill_id: str) -> dict:
    from runtime.skills import SkillError, get_skill

    try:
        return {"skill": get_skill(_load_runtime_config(), skill_id)}
    except SkillError as exc:
        _raise_skill_http_error(exc)


@app.post("/admin/skills/{skill_id}/audit", dependencies=[Depends(_require_bearer)])
async def admin_audit_skill(skill_id: str) -> dict:
    from runtime.skills import SkillError, audit_installed_skill

    try:
        skill = audit_installed_skill(_load_runtime_config(), skill_id)
    except SkillError as exc:
        _raise_skill_http_error(exc)
    return {"ok": True, "skill": skill}


@app.post("/admin/skills/{skill_id}/enable", dependencies=[Depends(_require_bearer)])
async def admin_enable_skill(skill_id: str) -> dict:
    from runtime.skills import SkillError, set_skill_enabled

    try:
        skill = set_skill_enabled(_load_runtime_config(), skill_id, True)
    except SkillError as exc:
        _raise_skill_http_error(exc)
    return {"ok": True, "skill": skill}


@app.post("/admin/skills/{skill_id}/disable", dependencies=[Depends(_require_bearer)])
async def admin_disable_skill(skill_id: str) -> dict:
    from runtime.skills import SkillError, set_skill_enabled

    try:
        skill = set_skill_enabled(_load_runtime_config(), skill_id, False)
    except SkillError as exc:
        _raise_skill_http_error(exc)
    return {"ok": True, "skill": skill}


@app.delete("/admin/skills/{skill_id}", dependencies=[Depends(_require_bearer)])
async def admin_delete_skill(skill_id: str) -> dict:
    from runtime.skills import SkillError, delete_skill

    try:
        result = delete_skill(_load_runtime_config(), skill_id)
    except SkillError as exc:
        _raise_skill_http_error(exc)
    return {"ok": True, **result}


@app.get("/admin/life", dependencies=[Depends(_require_bearer)])
async def admin_life_overview() -> dict:
    from runtime.life_history import LifeHistoryError, LifeHistoryStore

    try:
        with LifeHistoryStore(_load_runtime_config()) as store:
            return store.overview()
    except LifeHistoryError as exc:
        _raise_life_http_error(exc)


@app.post("/admin/life/experiences/text", dependencies=[Depends(_require_bearer)])
async def admin_life_ingest_text(req: AdminLifeTextRequest) -> dict:
    from runtime.life_history import LifeHistoryError, LifeHistoryStore

    config = _load_runtime_config()
    backend = _optional_life_llm_backend(config)
    try:
        with LifeHistoryStore(config) as store:
            result = store.ingest_pasted_text(
                title=req.title,
                text=req.text,
                source_type=req.source_type,
                participants=req.participants,
                llm_client=backend,
            )
    except LifeHistoryError as exc:
        _raise_life_http_error(exc)
    finally:
        _close_optional_backend(backend)
    return {"ok": True, **result}


@app.post("/admin/life/experiences/file", dependencies=[Depends(_require_bearer)])
async def admin_life_ingest_file(req: AdminLifeFileRequest) -> dict:
    from runtime.life_history import LifeHistoryError, LifeHistoryStore

    config = _load_runtime_config()
    backend = _optional_life_llm_backend(config)
    try:
        with LifeHistoryStore(config) as store:
            result = store.ingest_local_file(
                file_path=req.file_path,
                title=req.title,
                participants=req.participants,
                llm_client=backend,
            )
    except LifeHistoryError as exc:
        _raise_life_http_error(exc)
    finally:
        _close_optional_backend(backend)
    return {"ok": True, **result}


@app.post("/admin/life/experiences/upload", dependencies=[Depends(_require_bearer)])
async def admin_life_ingest_upload(
    file: UploadFile = File(...),
    title: str = Form(""),
    participants: str = Form(""),
) -> dict:
    """Digest a browser-uploaded text or Markdown file into Life History."""
    from runtime.life_history import LifeHistoryError, LifeHistoryStore

    filename = _upload_filename(file, fallback="uploaded-experience.txt")
    suffix = Path(filename).suffix.lower()
    if suffix not in _LIFE_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Life History uploads support plain text, Markdown, reStructuredText, or .text files.",
        )
    payload = await _read_upload_bytes(
        file,
        max_bytes=_MAX_LIFE_UPLOAD_BYTES,
        label="Life History file",
    )
    text = payload.decode("utf-8", errors="replace")

    config = _load_runtime_config()
    backend = _optional_life_llm_backend(config)
    try:
        with LifeHistoryStore(config) as store:
            result = store.ingest_uploaded_text(
                filename=filename,
                text=text,
                title=title,
                participants=_split_form_list(participants),
                llm_client=backend,
            )
    except LifeHistoryError as exc:
        _raise_life_http_error(exc)
    finally:
        _close_optional_backend(backend)
    return {"ok": True, **result}


@app.get("/admin/life/experiences", dependencies=[Depends(_require_bearer)])
async def admin_life_experiences(limit: int = 50) -> dict:
    from runtime.life_history import LifeHistoryError, LifeHistoryStore

    try:
        with LifeHistoryStore(_load_runtime_config()) as store:
            return {"experiences": store.list_experiences(limit=limit)}
    except LifeHistoryError as exc:
        _raise_life_http_error(exc)


@app.get("/admin/life/evolution", dependencies=[Depends(_require_bearer)])
async def admin_life_evolution(limit: int = 100) -> dict:
    from runtime.life_history import LifeHistoryError, LifeHistoryStore

    try:
        with LifeHistoryStore(_load_runtime_config()) as store:
            return {"evolution_events": store.list_evolution(limit=limit)}
    except LifeHistoryError as exc:
        _raise_life_http_error(exc)


@app.get("/admin/life/beliefs", dependencies=[Depends(_require_bearer)])
async def admin_life_beliefs(limit: int = 100) -> dict:
    from runtime.life_history import LifeHistoryError, LifeHistoryStore

    try:
        with LifeHistoryStore(_load_runtime_config()) as store:
            return {"beliefs": store.list_beliefs(limit=limit)}
    except LifeHistoryError as exc:
        _raise_life_http_error(exc)


@app.get("/admin/life/drives", dependencies=[Depends(_require_bearer)])
async def admin_life_drives() -> dict:
    from runtime.life_history import LifeHistoryError, LifeHistoryStore

    try:
        with LifeHistoryStore(_load_runtime_config()) as store:
            return {"drives": store.list_drives()}
    except LifeHistoryError as exc:
        _raise_life_http_error(exc)


@app.post("/admin/life/rollback", dependencies=[Depends(_require_bearer)])
async def admin_life_rollback(req: AdminLifeRollbackRequest) -> dict:
    from runtime.life_history import LifeHistoryError, LifeHistoryStore

    try:
        with LifeHistoryStore(_load_runtime_config()) as store:
            result = store.rollback_batch(req.batch_id)
    except LifeHistoryError as exc:
        _raise_life_http_error(exc)
    return {"ok": True, **result}


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
    # headers; if no Authorization header is present, the first received JSON
    # message must carry {"token": "..."} before any chat payload is handled.
    expected = str(_current_config_for_middleware().api_key or "")
    authed = False
    if expected:
        header_token: str | None = None
        auth_header = ws.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            header_token = auth_header.split(" ", 1)[1].strip()
        authed = bool(header_token and secrets.compare_digest(header_token, expected))
        if header_token and not authed:
            await ws.close(code=status.WS_1008_POLICY_VIOLATION)
            return
    await ws.accept()
    try:
        pending_msg: dict | None = None
        if expected and not authed:
            raw = await ws.receive_text()
            if len(raw) > _MAX_WS_MESSAGE_CHARS:
                await ws.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                return
            pending_msg = json.loads(raw)
            supplied = str(pending_msg.pop("token", "") or "")
            auth_value = str(pending_msg.pop("authorization", "") or "")
            if not supplied and auth_value.lower().startswith("bearer "):
                supplied = auth_value.split(" ", 1)[1].strip()
            if not secrets.compare_digest(supplied, expected):
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            if not pending_msg.get("message"):
                await ws.send_text(json.dumps({"type": "auth", "ok": True}))
                pending_msg = None

        while True:
            if pending_msg is not None:
                msg = pending_msg
                pending_msg = None
            else:
                raw = await ws.receive_text()
                if len(raw) > _MAX_WS_MESSAGE_CHARS:
                    await ws.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                    return
                msg = json.loads(raw)
            user_id = msg.get("user_id", "default")
            chat_id = msg.get("chat_id", "default")
            user_message = msg.get("message", "")
            if len(str(user_id)) > _MAX_ID_CHARS or len(str(chat_id)) > _MAX_ID_CHARS:
                await ws.close(code=status.WS_1008_POLICY_VIOLATION)
                return
            if not user_message or len(str(user_message)) > _MAX_CHAT_MESSAGE_CHARS:
                await ws.close(code=status.WS_1009_MESSAGE_TOO_BIG)
                return

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
    return _admin_response()


@app.get("/persona")
def persona_index() -> RedirectResponse:
    return RedirectResponse(url="/admin#persona", status_code=307)


@app.get("/dashboard")
def dashboard_index() -> RedirectResponse:
    return RedirectResponse(url="/admin#persona", status_code=307)


@app.get("/admin/assets/{asset_name}", include_in_schema=False)
def admin_asset(asset_name: str):
    media_types = {
        "admin.css": "text/css",
        "admin.js": "application/javascript",
        "persona.css": "text/css",
        "persona.js": "application/javascript",
        "tokens.css": "text/css",
    }
    media_type = media_types.get(asset_name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Admin asset not found")
    return _static_asset_response(asset_name, media_type)


@app.get("/assets/{asset_name}", include_in_schema=False)
def brand_asset(asset_name: str):
    media_types = {
        "tokens.css": "text/css",
        "wordmark-light.svg": "image/svg+xml",
        "wordmark-dark.svg": "image/svg+xml",
    }
    media_type = media_types.get(asset_name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Brand asset not found")
    if asset_name.endswith(".svg"):
        static_dir = os.path.join(os.path.dirname(__file__), "..", "docs", "diagrams")
        path = os.path.normpath(os.path.join(static_dir, asset_name))
    else:
        return _static_asset_response(asset_name, media_type)
    from fastapi.responses import Response

    with open(path, "rb") as f:
        return Response(content=f.read(), media_type=media_type, headers=_HTML_SECURITY_HEADERS)


def _index_response() -> HTMLResponse:
    return _html_static_response("index.html")


def _admin_response() -> HTMLResponse:
    return _html_static_response("admin.html")


def _persona_response() -> HTMLResponse:
    return _html_static_response("persona.html")


def _html_static_response(filename: str) -> HTMLResponse:
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    path = os.path.join(static_dir, filename)
    with open(path, encoding="utf-8") as f:
        return HTMLResponse(content=f.read(), headers=_HTML_SECURITY_HEADERS)


def _static_asset_response(filename: str, media_type: str):
    from fastapi.responses import Response

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    path = os.path.join(static_dir, filename)
    with open(path, "rb") as f:
        return Response(
            content=f.read(),
            media_type=media_type,
            headers=_HTML_SECURITY_HEADERS,
        )


def _raise_skill_http_error(exc: Exception) -> None:
    detail = str(exc)
    code = 404 if "not found" in detail.lower() else 400
    raise HTTPException(status_code=code, detail=detail)


def _raise_life_http_error(exc: Exception) -> None:
    detail = str(exc)
    code = 404 if "not found" in detail.lower() else 400
    raise HTTPException(status_code=code, detail=detail)


def _optional_life_llm_backend(config: RuntimeConfig):
    """Return an LLM backend for richer digestion, or None for heuristics."""
    try:
        return create_llm_backend(config)
    except Exception:
        return None


def _close_optional_backend(backend) -> None:
    close = getattr(backend, "close", None)
    if callable(close):
        close()


def _upload_filename(upload: UploadFile, *, fallback: str) -> str:
    raw = (upload.filename or "").strip().replace("\\", "/")
    name = Path(raw).name.strip()
    return name[:180] or fallback


async def _read_upload_bytes(upload: UploadFile, *, max_bytes: int, label: str) -> bytes:
    payload = await upload.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"{label} is too large ({max_bytes} byte limit).",
        )
    if not payload:
        raise HTTPException(status_code=400, detail=f"{label} is empty.")
    return payload


def _split_form_list(raw: str) -> list[str]:
    values = []
    for item in str(raw or "").replace("\n", ",").split(","):
        stripped = item.strip()
        if stripped:
            values.append(stripped)
    return values[:20]


def _safe_extract_zip(payload: bytes, destination: Path) -> None:
    destination = destination.resolve()
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = [info for info in archive.infolist() if not info.is_dir()]
            if not members:
                raise HTTPException(status_code=400, detail="Skill archive is empty.")
            if len(members) > _MAX_SKILL_ARCHIVE_FILES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Skill archive has too many files ({_MAX_SKILL_ARCHIVE_FILES} limit).",
                )
            total_size = sum(max(0, int(info.file_size)) for info in members)
            if total_size > _MAX_SKILL_ARCHIVE_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail="Skill archive expands beyond the upload size limit.",
                )
            for info in members:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or name.startswith("../") or "/../" in name:
                    raise HTTPException(
                        status_code=400,
                        detail="Skill archive contains an unsafe path.",
                    )
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise HTTPException(
                        status_code=400,
                        detail="Skill archive contains a symbolic link.",
                    )
                target = (destination / name).resolve()
                try:
                    target.relative_to(destination)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="Skill archive contains an unsafe path.",
                    ) from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Skill archive is not a valid zip file.") from exc


def _single_skill_root_from_upload(extract_root: Path) -> Path:
    candidates = [
        path.parent
        for path in extract_root.rglob("SKILL.md")
        if path.is_file() and "__MACOSX" not in path.parts
    ]
    if not candidates:
        raise HTTPException(status_code=400, detail="No SKILL.md found in uploaded archive.")
    unique = sorted({path.resolve() for path in candidates}, key=lambda item: (len(item.parts), str(item)))
    if len(unique) > 1:
        raise HTTPException(
            status_code=400,
            detail="Uploaded archive contains multiple skills. Upload one skill folder at a time.",
        )
    return unique[0]


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


def _changed_config_fields(before: RuntimeConfig, after: RuntimeConfig) -> list[str]:
    """Return dataclass fields whose persisted values changed."""
    before_data = before.to_yaml_dict()
    after_data = after.to_yaml_dict()
    return sorted(
        field_name
        for field_name in after_data
        if before_data.get(field_name) != after_data.get(field_name)
    )


async def _reload_runtime_from_config(config: RuntimeConfig) -> dict:
    """Apply saved config to reloadable in-process runtime surfaces."""
    global _session_manager, _pipeline_override

    active_sessions_evicted = 0
    reloaded_web_manager = False

    if _pipeline_override is not None:
        _pipeline_override.close()
        _pipeline_override = None
        reloaded_web_manager = True

    if _session_manager is not None:
        active_sessions_evicted = len(_session_manager.active_sessions)
        await _session_manager.shutdown()
        _session_manager = None
        reloaded_web_manager = True

    await _restart_telegram_channel(config)
    return {
        "reloaded_web_manager": reloaded_web_manager,
        "active_sessions_evicted": active_sessions_evicted,
        "telegram_restarted": True,
    }


async def _drain_runtime_before_process_restart() -> dict:
    """Persist hot state and stop background runtime work before exec."""
    global _session_manager, _pipeline_override

    active_sessions_evicted = 0
    closed_pipeline_override = False

    if _pipeline_override is not None:
        _pipeline_override.close()
        _pipeline_override = None
        closed_pipeline_override = True

    if _session_manager is not None:
        active_sessions_evicted = len(_session_manager.active_sessions)
        await _session_manager.shutdown()
        _session_manager = None

    await _stop_telegram_channel()
    return {
        "closed_pipeline_override": closed_pipeline_override,
        "active_sessions_evicted": active_sessions_evicted,
        "telegram_stopped": True,
    }


def _restart_argv() -> list[str]:
    """Best-effort command line for re-execing the current Python process."""
    original = getattr(sys, "orig_argv", None)
    if original and len(original) > 1:
        return [sys.executable, *list(original[1:])]
    return [sys.executable, *sys.argv]


def _schedule_process_restart(delay_seconds: float = 0.35) -> list[str]:
    """Schedule an in-place process restart after the response can flush."""
    argv = _restart_argv()

    async def _restart() -> None:
        await asyncio.sleep(delay_seconds)
        os.execv(sys.executable, argv)

    asyncio.create_task(_restart())
    return argv


def _config_payload(
    config: RuntimeConfig,
    *,
    saved: bool,
    message: str | None = None,
    reloaded_web_manager: bool = False,
    restart_required_fields: list[str] | None = None,
    runtime_reload: dict | None = None,
) -> dict:
    """Serialize runtime config for the settings UI."""
    public = config.to_public_dict()
    state = _read_admin_state(config)
    public["setup_completed"] = bool(state.get("setup_completed"))
    restart_required_fields = sorted(restart_required_fields or [])
    runtime_reload = runtime_reload or {}
    return {
        "config": public,
        "secret_status": config.secret_status(),
        "config_path": os.path.abspath(RUNTIME_CONFIG_PATH),
        "saved": saved,
        "message": message,
        "reloaded_web_manager": reloaded_web_manager,
        "runtime_reload": runtime_reload,
        "apply_state": {
            "saved_to_disk": saved,
            "session_manager_reloaded": reloaded_web_manager,
            "telegram_restarted": bool(runtime_reload.get("telegram_restarted", False)),
            "active_sessions_evicted": int(runtime_reload.get("active_sessions_evicted", 0)),
            "restart_required": bool(restart_required_fields),
            "restart_required_fields": restart_required_fields,
        },
        "notes": [
            "Secret fields are never returned; leave them blank to keep the current value.",
            "Saving through this UI updates runtime_config.yaml.",
            "The standalone web server reloads its web manager and Telegram poller after save.",
            "debug_host, debug_port, and cors_origins require restarting the web server process.",
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
    "autonomy_level": "tools",
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
    "autonomy_level",
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
    restart_required_fields: list[str] | None = None,
    runtime_reload: dict | None = None,
) -> dict:
    """Serialize config and metadata for the production admin console."""
    return {
        **_config_payload(
            config,
            saved=saved,
            message=message,
            reloaded_web_manager=reloaded_web_manager,
            restart_required_fields=restart_required_fields,
            runtime_reload=runtime_reload,
        ),
        "field_metadata": _admin_field_metadata(config),
        "warnings": _admin_config_warnings(config),
        "setup": _admin_setup_status(config),
        "mock_mode": config.llm_backend == "auto" and not _llm_configured(config),
    }


def _read_soul_name() -> str:
    """Return the agent name from soul.yaml without going through the async config."""
    try:
        import yaml as _yaml
        path = _soul_yaml_path()
        with open(path, encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        return (data.get("soul") or {}).get("name") or "Nūr"
    except Exception:
        return "Nūr"


def _admin_status_payload(config: RuntimeConfig, manager: SessionManager) -> dict:
    """Operator-facing status summary used by the admin overview."""
    return {
        "status": "ok",
        "config_path": os.path.abspath(RUNTIME_CONFIG_PATH),
        "auth_enabled": bool(config.api_key),
        "soul_name": _read_soul_name(),
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


def _admin_persona_payload(manager: SessionManager) -> dict:
    """Build the standalone persona dashboard payload from active sessions."""
    now = time.time()
    sessions = []
    channel_counts: dict[str, int] = {}
    for session_key, session in sorted(manager.active_sessions.items()):
        platform, user_id, chat_id = _split_session_key(session_key)
        channel_counts[platform] = channel_counts.get(platform, 0) + 1
        sessions.append({
            "session_key": session_key,
            "platform": platform,
            "user_id": user_id or session.user_id,
            "chat_id": chat_id,
            "rel_key": session.rel_key,
            "last_activity": session.last_activity,
            "idle_seconds": round(now - session.last_activity, 1),
            "has_last_turn": session.last_debug is not None,
            "persona_view": build_persona_view(session=session, session_key=session_key),
        })
    return {
        "generated_at": now,
        "count": len(sessions),
        "channel_counts": channel_counts,
        "sessions": sessions,
    }


def _split_session_key(session_key: str) -> tuple[str, str, str]:
    parts = str(session_key or "").split(":", 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return str(session_key or ""), "", ""


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
    if field_name == "autonomy_level":
        return ["off", "assisted", "autonomous", "high_risk"]
    return None


def _normalize_autonomy_level(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in {"off", "assisted", "autonomous", "high_risk"}:
        raise HTTPException(
            status_code=400,
            detail="autonomy_level must be one of: off, assisted, autonomous, high_risk",
        )
    return normalized


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
        warnings.append(_warning("warning", "tools_enabled", "tools_without_auth", "Agentic tools are enabled with no bearer auth. Keep this instance on a trusted network."))
    if config.shell_tool_enabled and not config.tools_enabled:
        warnings.append(_warning("error", "shell_tool_enabled", "shell_without_tools", "shell_tool_enabled has no effect unless tools_enabled is true."))
    if config.shell_tool_enabled and not config.api_key:
        warnings.append(_warning("warning", "shell_tool_enabled", "shell_without_auth", "Shell tool execution is enabled with no bearer auth. Keep this instance on a trusted network."))
    if config.autonomy_level == "high_risk":
        warnings.append(_warning("warning", "autonomy_level", "high_risk_autonomy", "High-risk autonomy allows the assistant to act with fewer confirmations inside enabled tool scopes."))
    if config.cors_origins and not config.api_key:
        warnings.append(_warning("warning", "cors_origins", "cors_without_auth", "CORS origins are configured while api_key is empty."))
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
    if _soul_looks_default():
        reasons.append("default_soul")
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


_DEFAULT_SOUL_NAME = "Nūr"


def _soul_yaml_path() -> str:
    """Path where /admin/soul writes soul.yaml.

    If NUR_CONFIG_DIR is set, write there (persists across pip upgrades).
    Otherwise fall back to the packaged config dir so the loader still
    finds it without any env setup — at the cost of being overwritten by
    ``pip install --upgrade``. The admin UI surfaces that trade-off on
    save.
    """
    from config import loader as _loader  # imported lazily to avoid cycles

    override = os.environ.get("NUR_CONFIG_DIR", "").strip()
    if override:
        path = os.path.expanduser(override)
        os.makedirs(path, exist_ok=True)
        return str(os.path.join(path, "soul.yaml"))
    return str(os.path.join(os.path.dirname(os.path.abspath(_loader.__file__)), "soul.yaml"))


def _write_soul_yaml(data: dict, path: str) -> None:
    """Atomic YAML write: temp file in the same dir, then os.replace."""
    import yaml as _yaml

    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        _yaml.safe_dump(
            data,
            handle,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
    os.replace(tmp_path, path)


def _llm_configured_for_draft(config: RuntimeConfig) -> bool:
    """True when the configured LLM is callable for draft generation.

    Mock backend cannot produce a valid soul draft — MockLLMBackend
    returns a static string, not JSON. Return False so the endpoint
    gives a clean 400 instead of a confusing 502.
    """
    backend = config.llm_backend
    if backend == "mock":
        return False
    if backend in {"provider", "openai_compatible"}:
        return bool(config.llm_base_url.strip() and config.llm_model.strip())
    if backend == "minimax":
        return bool(config.minimax_api_key or os.environ.get("MINIMAX_API_KEY"))
    if backend == "auto":
        has_generic = bool(
            config.llm_base_url.strip()
            and config.llm_model.strip()
        )
        has_minimax = bool(config.minimax_api_key or os.environ.get("MINIMAX_API_KEY"))
        return has_generic or has_minimax
    return False


def _load_soul_draft_prompt() -> str:
    """Load the soul-from-description prompt template.

    Honors the same NUR_CONFIG_DIR override as every other prompt.
    """
    from config.loader import _load_prompt

    return _load_prompt("soul_from_description.md")


def _parse_soul_draft_json(raw: str) -> dict:
    """Extract a JSON object from an LLM reply.

    Permissive about leading/trailing prose or code fences the LLM may
    emit despite the 'output only JSON' instruction — grabs the first
    top-level {...} block. Raises 502 if no JSON object can be found.
    """
    text = (raw or "").strip()
    if not text:
        raise HTTPException(
            status_code=502,
            detail="LLM returned an empty response for the soul draft.",
        )
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise HTTPException(
            status_code=502,
            detail="LLM did not return a JSON object for the soul draft.",
        )
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM draft was not valid JSON: {exc}",
        )


_SOUL_FIELDS = {
    "name",
    "identity",
    "voice",
    "relational_stance",
    "growth_policy",
    "likes",
    "dislikes",
    "boundaries",
    "core_values",
    "initial_traits",
}


def _parse_raw_soul_document(raw: str) -> tuple[dict, str]:
    """Parse pasted identity text into the admin soul schema.

    Accepts either real ``soul.yaml`` (with or without a top-level ``soul`` key)
    or a plain text document beginning with ``Name: ...``. The latter keeps the
    wizard's raw-paste path deterministic instead of depending on an LLM.
    """
    text = raw.strip()
    yaml_payload = _parse_soul_yaml_payload(text)
    if yaml_payload is not None:
        return yaml_payload, "yaml"
    return _parse_plain_soul_document(text), "text"


def _parse_soul_yaml_payload(text: str) -> dict | None:
    import yaml as _yaml

    try:
        loaded = _yaml.safe_load(text)
    except _yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None

    source = loaded.get("soul") if isinstance(loaded.get("soul"), dict) else loaded
    normalized = {
        str(key).strip().lower().replace("-", "_"): value
        for key, value in source.items()
    }
    if not any(field in normalized for field in _SOUL_FIELDS):
        return None

    return _coerce_soul_payload(normalized)


def _parse_plain_soul_document(text: str) -> dict:
    lines = text.splitlines()
    first_idx = next((i for i, line in enumerate(lines) if line.strip()), -1)
    if first_idx < 0:
        raise HTTPException(status_code=400, detail="Pasted identity document is empty.")

    name = ""
    first = lines[first_idx].strip()
    if first.lower().startswith("name:"):
        name = first.split(":", 1)[1].strip()
        body = "\n".join(lines[first_idx + 1 :]).strip()
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "Paste a soul.yaml document or start the identity document with "
                "'Name: <agent name>'."
            ),
        )
    if not name:
        raise HTTPException(status_code=400, detail="Identity document name is empty.")
    if not body:
        raise HTTPException(status_code=400, detail="Identity document body is empty.")

    sections = _split_plain_identity_sections(body)
    identity = sections.get("identity") or body
    return _coerce_soul_payload(
        {
            "name": name,
            "identity": _trim_text(identity, 2000),
            "voice": _trim_text(sections.get("voice", ""), 2000),
            "relational_stance": _trim_text(sections.get("relational_stance", ""), 2000),
            "growth_policy": _trim_text(sections.get("growth_policy", ""), 2000),
            "likes": _plain_list(sections.get("likes", "")),
            "dislikes": _plain_list(sections.get("dislikes", "")),
            "boundaries": _plain_boundaries(sections.get("boundaries", "")),
            "core_values": {},
            "initial_traits": {},
        }
    )


def _split_plain_identity_sections(body: str) -> dict[str, str]:
    markers = {
        "core tone:": "voice",
        "voice:": "voice",
        "relational stance:": "relational_stance",
        "growth policy:": "growth_policy",
        "likes:": "likes",
        "dislikes:": "dislikes",
        "boundaries:": "boundaries",
    }
    sections: dict[str, list[str]] = {"identity": []}
    current = "identity"
    for line in body.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        for prefix, marker in markers.items():
            if lowered.startswith(prefix):
                current = marker
                sections.setdefault(current, [])
                remainder = stripped.split(":", 1)[1].strip()
                if remainder:
                    sections[current].append(remainder)
                break
        else:
            sections.setdefault(current, []).append(line)
            continue
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def _plain_boundaries(text: str) -> list[str]:
    if not text.strip():
        return []
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("never "):
            parts = [part.strip(" .") for part in stripped.split(".") if part.strip()]
            items.extend(parts)
        else:
            items.append(stripped)
    return [_trim_text(item, 200) for item in items[:32] if item]


def _plain_list(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        for part in line.split(","):
            value = part.strip(" -\t")
            if value:
                items.append(_trim_text(value, 200))
    return items[:32]


def _coerce_soul_payload(data: dict) -> dict:
    return {
        "name": _trim_text(str(data.get("name") or ""), 64),
        "identity": _trim_text(str(data.get("identity") or ""), 2000),
        "voice": _trim_text(str(data.get("voice") or ""), 2000),
        "relational_stance": _trim_text(str(data.get("relational_stance") or ""), 2000),
        "growth_policy": _trim_text(str(data.get("growth_policy") or ""), 2000),
        "likes": _coerce_string_list(data.get("likes")),
        "dislikes": _coerce_string_list(data.get("dislikes")),
        "boundaries": _coerce_string_list(data.get("boundaries")),
        "core_values": _coerce_weight_dict(data.get("core_values")),
        "initial_traits": _coerce_weight_dict(data.get("initial_traits")),
    }


def _coerce_string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = value.splitlines()
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = [value]
    return [_trim_text(str(item).strip(), 200) for item in candidates if str(item).strip()]


def _coerce_weight_dict(value) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw in value.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            out[name] = float(raw)
        except (TypeError, ValueError):
            out[name] = 0.5
    return out


def _trim_text(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _soul_looks_default() -> bool:
    """True when soul.name is still the built-in placeholder."""
    try:
        from config.loader import get_config

        return get_config().soul.name.strip() == _DEFAULT_SOUL_NAME
    except Exception:  # pragma: no cover - defensive
        return False


def _admin_soul_payload(
    soul,
    *,
    saved: bool = True,
    path: str | None = None,
    reloaded_session_manager: bool = False,
) -> dict:
    notes: list[str] = []
    if saved:
        notes.append(
            "Identity saved. The next chat turn will reflect the new "
            "identity across the whole prompt stack (generator, self-check, "
            "memory digestion, role labels)."
        )
        if reloaded_session_manager:
            notes.append(
                "Active chat sessions were rebuilt so they use the new "
                "identity immediately; no server restart needed."
            )
    if not os.environ.get("NUR_CONFIG_DIR", "").strip():
        notes.append(
            "To make this identity survive `pip install --upgrade`, set "
            "NUR_CONFIG_DIR to a writable directory outside site-packages "
            "and save again — future writes and reads will use that dir."
        )
    return {
        "soul": {
            "name": soul.name,
            "identity": soul.identity,
            "voice": soul.voice,
            "relational_stance": soul.relational_stance,
            "likes": list(soul.likes),
            "dislikes": list(soul.dislikes),
            "boundaries": list(soul.boundaries),
            "growth_policy": soul.growth_policy,
            "core_values": dict(soul.core_values),
            "initial_traits": dict(soul.initial_traits),
        },
        "is_default_name": soul.name.strip() == _DEFAULT_SOUL_NAME,
        "saved": saved,
        "path": path or _soul_yaml_path(),
        "reloaded_session_manager": reloaded_session_manager,
        "notes": notes,
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


def _admin_diagnostics_payload(
    config: RuntimeConfig,
    manager: SessionManager,
) -> dict:
    data_summary = _directory_summary("data_dir", config.data_dir)
    workspace_summary = _directory_summary(
        "tools_workspace",
        config.resolved_tools_workspace,
    )
    return {
        "status": "ok",
        "generated_at": _utc_now_iso(),
        "server": {
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "uptime_seconds": max(0.0, time.time() - _serve_started_at),
        },
        "config": {
            "path": os.path.abspath(RUNTIME_CONFIG_PATH),
            "exists": os.path.exists(RUNTIME_CONFIG_PATH),
            "auth_enabled": bool(config.api_key),
            "llm_backend": config.llm_backend,
            "llm_configured": _llm_configured(config),
            "telegram_configured": bool(config.telegram_token),
        },
        "runtime": {
            "active_sessions": len(manager.active_sessions),
            "max_active_sessions": config.max_active_sessions,
            "telegram_running": _telegram_task is not None and not _telegram_task.done(),
            "pipeline_override": _pipeline_override is not None,
        },
        "storage": {
            "data_dir": data_summary,
            "tools_workspace": workspace_summary,
            "backup_dir": os.path.realpath(_admin_backup_dir(config)),
        },
        "warnings": _admin_config_warnings(config),
    }


def _directory_summary(name: str, path: str) -> dict:
    check = _check_writable_directory(name, path, create_missing=False)
    summary = {
        "path": check["path"],
        "exists": check["exists"],
        "writable": check["writable"],
        "warnings": check["warnings"],
        "file_count": 0,
        "truncated": False,
    }
    if not check["exists"]:
        return summary

    max_files = 2000
    try:
        count = 0
        for _, _, files in os.walk(check["path"]):
            count += len(files)
            if count > max_files:
                summary["file_count"] = max_files
                summary["truncated"] = True
                return summary
        summary["file_count"] = count
    except OSError as exc:
        summary["warnings"] = [
            *summary["warnings"],
            _warning("warning", name, f"{name}_count_error", str(exc)),
        ]
    return summary


def _admin_redacted_config_export(config: RuntimeConfig) -> dict:
    return {
        "format": "nur.runtime_config.redacted.v1",
        "exported_at": _utc_now_iso(),
        "config_path": os.path.abspath(RUNTIME_CONFIG_PATH),
        "config": config.to_public_dict(),
        "secret_status": _admin_secret_status(config),
        "warnings": _admin_config_warnings(config),
    }


def _create_admin_backup(config: RuntimeConfig, *, include_data: bool) -> dict:
    backup_dir = _admin_backup_dir(config)
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = os.path.join(backup_dir, f"nur-backup-{timestamp}.zip")

    file_count = 0
    data_root = os.path.realpath(config.data_dir)
    backup_root = os.path.realpath(backup_dir)
    archive_realpath = os.path.realpath(archive_path)
    export_payload = _admin_redacted_config_export(config)

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "runtime_config.redacted.json",
            json.dumps(export_payload, indent=2, sort_keys=True),
        )
        file_count += 1

        if include_data and os.path.isdir(data_root):
            for root, dirs, files in os.walk(data_root):
                dirs[:] = [
                    dirname for dirname in dirs
                    if not _is_relative_to(
                        os.path.realpath(os.path.join(root, dirname)),
                        backup_root,
                    )
                ]
                for filename in files:
                    file_path = os.path.realpath(os.path.join(root, filename))
                    if (
                        file_path == archive_realpath
                        or _is_relative_to(file_path, backup_root)
                    ):
                        continue
                    arcname = os.path.join("data", os.path.relpath(file_path, data_root))
                    archive.write(file_path, arcname)
                    file_count += 1

    return {
        "ok": True,
        "created_at": _utc_now_iso(),
        "path": os.path.realpath(archive_path),
        "filename": os.path.basename(archive_path),
        "size_bytes": os.path.getsize(archive_path),
        "file_count": file_count,
        "included_data": include_data,
        "redacted_config": True,
    }


def _list_admin_backups(config: RuntimeConfig) -> dict:
    backup_dir = _admin_backup_dir(config)
    backups: list[dict] = []
    if os.path.isdir(backup_dir):
        for name in sorted(os.listdir(backup_dir), reverse=True):
            if not name.startswith("nur-backup-") or not name.endswith(".zip"):
                continue
            path = _resolve_admin_backup_path(config, name, must_exist=False)
            if not os.path.isfile(path):
                continue
            stat = os.stat(path)
            backups.append({
                "filename": name,
                "path": os.path.realpath(path),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, timezone.utc
                ).isoformat().replace("+00:00", "Z"),
            })
    return {
        "ok": True,
        "backup_dir": os.path.realpath(backup_dir),
        "count": len(backups),
        "backups": backups,
    }


def _delete_admin_backup(
    config: RuntimeConfig,
    *,
    filename: str,
    confirmation: str,
) -> dict:
    filename = str(filename or "").strip()
    expected = f"DELETE {filename}"
    if confirmation != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation must exactly match: {expected}",
        )
    path = _resolve_admin_backup_path(config, filename, must_exist=True)
    size = os.path.getsize(path)
    os.remove(path)
    return {
        "ok": True,
        "action": "backup_delete",
        "filename": filename,
        "path_removed": path,
        "size_bytes": size,
    }


def _resolve_admin_backup_path(
    config: RuntimeConfig,
    filename: str,
    *,
    must_exist: bool,
) -> str:
    if (
        not filename
        or os.sep in filename
        or (os.altsep and os.altsep in filename)
        or not filename.startswith("nur-backup-")
        or not filename.endswith(".zip")
    ):
        raise HTTPException(status_code=400, detail="Invalid backup filename.")
    backup_dir = os.path.realpath(_admin_backup_dir(config))
    path = os.path.realpath(os.path.join(backup_dir, filename))
    if not _is_relative_to(path, backup_dir):
        raise HTTPException(status_code=400, detail="Invalid backup path.")
    if must_exist and not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Backup not found.")
    return path


async def _admin_delete_user_data(
    config: RuntimeConfig,
    manager: SessionManager,
    platform_name: str,
    user_id: str,
) -> dict:
    rel_key = f"{platform_name}:{user_id}"
    user_dir = config.user_data_dir(rel_key)
    data_root = os.path.realpath(config.data_dir)
    resolved = os.path.realpath(user_dir)
    if not (resolved == data_root or resolved.startswith(data_root + os.sep)):
        raise HTTPException(
            status_code=400,
            detail="Resolved user path is not inside data_dir",
        )

    rows_deleted = _count_user_rows(config.user_db_path(rel_key), user_id)
    prefix = f"{rel_key}:"
    evict_keys = [
        key for key in list(manager.active_sessions.keys())
        if key.startswith(prefix)
    ]
    for key in evict_keys:
        await manager.evict_session(key)

    session_files_removed = 0
    sessions_dir = os.path.join(user_dir, "sessions")
    if os.path.isdir(sessions_dir):
        session_files_removed = sum(
            1 for name in os.listdir(sessions_dir)
            if name.endswith(".json")
        )

    existed = os.path.isdir(user_dir)
    had_rows = any(isinstance(value, int) and value > 0 for value in rows_deleted.values())
    if not existed and not had_rows and not evict_keys:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for {rel_key}",
        )
    if existed:
        shutil.rmtree(user_dir, ignore_errors=False)

    return {
        "ok": True,
        "action": "user_delete",
        "deleted": True,
        "rel_key": rel_key,
        "platform": platform_name,
        "user_id": user_id,
        "rows_deleted": rows_deleted,
        "session_files_removed": session_files_removed,
        "sessions_evicted": evict_keys,
        "path_removed": user_dir if existed else None,
        "shared_self_model_db_preserved": True,
    }


def _admin_backup_dir(config: RuntimeConfig) -> str:
    return os.path.join(config.data_dir, "backups")


def _is_relative_to(path: str, parent: str) -> bool:
    try:
        common = os.path.commonpath([os.path.realpath(path), os.path.realpath(parent)])
    except ValueError:
        return False
    return common == os.path.realpath(parent)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _web_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("project-nur")
    except PackageNotFoundError:
        return "unknown"


def _parse_web_args(argv: list[str] | None = None):
    import argparse

    parser = argparse.ArgumentParser(
        prog="nur-web",
        description=(
            "Serve the Nūr HTTP API and bundled chat/admin UI via Uvicorn. "
            "Reads runtime_config.yaml from the current working directory. "
            "Defaults bind to localhost; see docs/DEPLOYMENT_AND_ADMIN.md "
            "before exposing beyond the local machine."
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="TCP port to bind (default: 8000).",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("NUR_RUNTIME_CONFIG", "runtime_config.yaml"),
        help="Runtime config YAML path (default: runtime_config.yaml).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"project-nur {_web_version()}",
    )
    return parser.parse_args(argv)


def main() -> None:
    """Launch the standalone web UI on the configured host/port."""
    import uvicorn

    args = _parse_web_args()
    global RUNTIME_CONFIG_PATH
    RUNTIME_CONFIG_PATH = args.config
    os.environ["NUR_RUNTIME_CONFIG"] = args.config
    uvicorn.run("interface.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
