"""FastAPI backend for Project Nūr."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import sys
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from interface.v1 import build_v1_router, _count_user_rows, _validate_path_token
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


class AdminSessionResetRequest(BaseModel):
    session_key: str
    confirmation: str


class AdminUserDeleteRequest(BaseModel):
    platform: str
    user_id: str
    confirmation: str


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
    return Response(status_code=204)


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


@app.get("/admin/diagnostics", dependencies=[Depends(_require_bearer)])
async def admin_diagnostics() -> dict:
    """Operational diagnostics for the admin maintenance panel."""
    config = _load_runtime_config()
    manager = get_session_manager()
    return _admin_diagnostics_payload(config, manager)


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
    public = config.to_public_dict()
    state = _read_admin_state(config)
    public["setup_completed"] = bool(state.get("setup_completed"))
    return {
        "config": public,
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
        warnings.append(_warning("error", "tools_enabled", "tools_without_auth", "Agentic tools are enabled while api_key is empty. Set api_key before exposing this server."))
    if config.shell_tool_enabled and not config.tools_enabled:
        warnings.append(_warning("error", "shell_tool_enabled", "shell_without_tools", "shell_tool_enabled has no effect unless tools_enabled is true."))
    if config.shell_tool_enabled and not config.api_key:
        warnings.append(_warning("error", "shell_tool_enabled", "shell_without_auth", "Shell tool execution requires a protected admin/API surface."))
    if config.autonomy_level == "high_risk":
        warnings.append(_warning("warning", "autonomy_level", "high_risk_autonomy", "High-risk autonomy allows the assistant to act with fewer confirmations inside enabled tool scopes."))
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
        "size_bytes": os.path.getsize(archive_path),
        "file_count": file_count,
        "included_data": include_data,
        "redacted_config": True,
    }


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
        "--version",
        action="version",
        version=f"project-nur {_web_version()}",
    )
    return parser.parse_args(argv)


def main() -> None:
    """Launch the standalone web UI on the configured host/port."""
    import uvicorn

    args = _parse_web_args()
    uvicorn.run("interface.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
