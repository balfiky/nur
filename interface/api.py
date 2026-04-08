"""FastAPI backend for Project Nūr."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

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


app = FastAPI(title="Project Nūr", version="0.3.0", lifespan=_lifespan)

WEB_PLATFORM = "web"
RUNTIME_CONFIG_PATH = "runtime_config.yaml"

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
    clear_telegram_token: bool = False
    clear_llm_api_key: bool = False
    clear_minimax_api_key: bool = False


def get_session_manager() -> SessionManager:
    """Create the shared web SessionManager lazily."""
    global _session_manager
    if _session_manager is None:
        config = _load_runtime_config()
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


@app.post("/chat")
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


@app.get("/config")
async def get_config() -> dict:
    config = _load_runtime_config()
    return _config_payload(config, saved=True)


@app.post("/config")
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
            "Changes for python main.py still apply after restarting that runtime.",
        ],
    }
