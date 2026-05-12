"""Self-action tools — bounded actions Nūr can take for itself.

Unlike the other builtins, these are invoked by Nūr's own self-intent
stage (``pipeline.propose_self_intents``), not by user-facing tool
routing. Each writes under a namespaced ``data/self/`` directory so
self-actions are auditable and never collide with user content.

Five tools are exposed:

* ``self.snapshot_state`` — persist a modulator/recent-turns snapshot.
* ``self.log_event``    — append a structured event line.
* ``self.note``         — append a durable markdown note under a topic.
* ``self.verify``       — verify a factual claim via ``web.search``.
* ``self.alert_owner``  — send a Telegram message to the configured owner.

The handlers read their session-scoped context from the executor at
call time via a small ``get_context`` callable so registration can stay
permanent while the per-session context is attached after the executor
is built.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from core.types import ToolCapability, ToolCategory, ToolResult
from nur_tools.executor import ToolHandler


# ---------------------------------------------------------------------------
# Per-session context shared by every self.* handler
# ---------------------------------------------------------------------------

@dataclass
class SelfActionContext:
    """Session-scoped context every self.* handler reads at call time.

    ``data_dir``       — runtime data directory; self-actions write under
                          ``{data_dir}/self/``.
    ``owner_chat_id``  — optional configured Telegram chat for owner alerts.
    ``current_user_chat_id`` — chat_id of the user currently being served;
                          ``self.alert_owner`` is a no-op when this equals
                          ``owner_chat_id``.
    ``telegram_token`` — bot token used for ``self.alert_owner``.
    ``state_provider`` — callable returning the dict serialized in
                          ``self.snapshot_state``. Pipeline wires this to
                          fetch current modulators + last short-term entries.
    ``web_executor``   — callable taking ``{"query": str}`` and returning a
                          ``ToolResult``; ``self.verify`` delegates to it.
    """

    data_dir: str = ""
    owner_chat_id: str = ""
    current_user_chat_id: str = ""
    telegram_token: str = ""
    state_provider: Callable[[], dict[str, Any]] | None = None
    web_executor: Callable[[dict[str, Any]], ToolResult] | None = None

    @property
    def self_dir(self) -> str:
        return os.path.join(self.data_dir, "self")


# ---------------------------------------------------------------------------
# Capabilities (registered once per executor)
# ---------------------------------------------------------------------------

CAPABILITIES: list[ToolCapability] = [
    ToolCapability(
        name="self.snapshot_state",
        description=(
            "Save a snapshot of your current modulators and recent turns "
            "to a private self-log. Use when something happens that you "
            "may want a witness record of."
        ),
        category=ToolCategory.WRITE,
        arg_schema={"reason": {"type": "string", "required": True}},
    ),
    ToolCapability(
        name="self.log_event",
        description=(
            "Append a structured event line to your private event log. "
            "Use for threats, milestones, decisions, or other discrete "
            "moments worth remembering."
        ),
        category=ToolCategory.WRITE,
        arg_schema={
            "kind": {"type": "string", "required": True},
            "detail": {"type": "string", "required": True},
            "intensity": {"type": "number", "required": False},
        },
    ),
    ToolCapability(
        name="self.note",
        description=(
            "Append a durable markdown note to a topic file under your "
            "private notes directory. Notes persist across turns and "
            "sessions."
        ),
        category=ToolCategory.WRITE,
        arg_schema={
            "topic": {"type": "string", "required": True},
            "text": {"type": "string", "required": True},
        },
    ),
    ToolCapability(
        name="self.verify",
        description=(
            "Verify a factual claim by running a web search. The claim is "
            "logged and the search result is returned."
        ),
        category=ToolCategory.EXTERNAL_ACTION,
        arg_schema={"claim": {"type": "string", "required": True}},
        requires_network=True,
    ),
    ToolCapability(
        name="self.alert_owner",
        description=(
            "Send a Telegram message to the configured owner. No-op if "
            "no owner is configured or if the current user IS the owner."
        ),
        category=ToolCategory.EXTERNAL_ACTION,
        arg_schema={
            "reason": {"type": "string", "required": True},
            "detail": {"type": "string", "required": True},
        },
        requires_network=True,
    ),
]

# Bounded catalog the self-intent stage is allowed to invoke.
SELF_TOOL_NAMES: frozenset[str] = frozenset(c.name for c in CAPABILITIES)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_TOPIC_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_ALERT_LEN = 4000


def _safe_topic(value: str) -> str:
    cleaned = _TOPIC_SAFE_RE.sub("_", value.strip())
    cleaned = cleaned.strip("._")
    return cleaned[:64] or "untitled"


def _ts_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%S", time.gmtime())


def _ensure_self_dir(context: SelfActionContext, subdir: str = "") -> str:
    base = os.path.join(context.self_dir, subdir) if subdir else context.self_dir
    os.makedirs(base, exist_ok=True)
    return base


def _no_data_dir(name: str) -> ToolResult:
    return ToolResult(
        tool_name=name,
        success=False,
        output="",
        error="No data_dir configured for self-actions.",
    )


# ---------------------------------------------------------------------------
# Handler implementations (private; bound via create_handlers)
# ---------------------------------------------------------------------------

def _snapshot_state(context: SelfActionContext, args: dict[str, Any]) -> ToolResult:
    name = "self.snapshot_state"
    if not context.data_dir:
        return _no_data_dir(name)
    reason = str(args.get("reason", "")).strip()
    if not reason:
        return ToolResult(tool_name=name, success=False, output="",
                          error="reason is required")
    payload: dict[str, Any] = {"timestamp": time.time(), "reason": reason}
    if context.state_provider is not None:
        try:
            payload["state"] = context.state_provider() or {}
        except Exception as exc:
            payload["state_error"] = f"{type(exc).__name__}: {exc}"
    directory = _ensure_self_dir(context, "snapshots")
    path = os.path.join(directory, f"{_ts_stamp()}.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    return ToolResult(
        tool_name=name,
        success=True,
        output=f"State snapshot saved: {path}",
        metadata={"path": path, "reason": reason},
        side_effect_summary=f"wrote {path}",
    )


def _log_event(context: SelfActionContext, args: dict[str, Any]) -> ToolResult:
    name = "self.log_event"
    if not context.data_dir:
        return _no_data_dir(name)
    kind = str(args.get("kind", "")).strip()
    detail = str(args.get("detail", "")).strip()
    if not kind or not detail:
        return ToolResult(tool_name=name, success=False, output="",
                          error="kind and detail are required")
    try:
        intensity = float(args.get("intensity", 0.5))
    except (TypeError, ValueError):
        intensity = 0.5
    intensity = max(0.0, min(1.0, intensity))
    entry = {
        "timestamp": time.time(),
        "kind": kind,
        "detail": detail,
        "intensity": intensity,
    }
    directory = _ensure_self_dir(context)
    path = os.path.join(directory, "events.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
    return ToolResult(
        tool_name=name,
        success=True,
        output=f"Event logged ({kind}, intensity={intensity:.2f})",
        metadata={"path": path, "kind": kind, "intensity": intensity},
        side_effect_summary=f"appended to {path}",
    )


def _note(context: SelfActionContext, args: dict[str, Any]) -> ToolResult:
    name = "self.note"
    if not context.data_dir:
        return _no_data_dir(name)
    topic = str(args.get("topic", "")).strip()
    text = str(args.get("text", "")).strip()
    if not topic or not text:
        return ToolResult(tool_name=name, success=False, output="",
                          error="topic and text are required")
    safe = _safe_topic(topic)
    directory = _ensure_self_dir(context, "notes")
    path = os.path.join(directory, f"{safe}.md")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    block = f"## {stamp}\n{text}\n\n"
    with open(path, "a") as f:
        f.write(block)
    return ToolResult(
        tool_name=name,
        success=True,
        output=f"Note appended to topic '{safe}'",
        metadata={"path": path, "topic": safe},
        side_effect_summary=f"appended to {path}",
    )


def _verify(context: SelfActionContext, args: dict[str, Any]) -> ToolResult:
    name = "self.verify"
    claim = str(args.get("claim", "")).strip()
    if not claim:
        return ToolResult(tool_name=name, success=False, output="",
                          error="claim is required")
    if context.web_executor is None:
        return ToolResult(tool_name=name, success=False, output="",
                          error="web_executor not configured; cannot verify online")
    try:
        result = context.web_executor({"query": claim})
    except Exception as exc:
        return ToolResult(
            tool_name=name, success=False, output="",
            error=f"web.search failed: {type(exc).__name__}: {exc}",
        )
    return ToolResult(
        tool_name=name,
        success=result.success,
        output=result.output,
        error=result.error,
        metadata={"claim": claim, **(result.metadata or {})},
        side_effect_summary=(
            result.side_effect_summary or "verified claim via web search"
        ),
    )


def _alert_owner(context: SelfActionContext, args: dict[str, Any]) -> ToolResult:
    name = "self.alert_owner"
    reason = str(args.get("reason", "")).strip()
    detail = str(args.get("detail", "")).strip()
    if not reason or not detail:
        return ToolResult(tool_name=name, success=False, output="",
                          error="reason and detail are required")
    owner = (context.owner_chat_id or "").strip()
    if not owner:
        return ToolResult(
            tool_name=name,
            success=True,
            output="owner_chat_id not configured; alert skipped",
            metadata={"skipped": "no_owner_configured"},
        )
    if owner == (context.current_user_chat_id or "").strip():
        return ToolResult(
            tool_name=name,
            success=True,
            output="current user is the owner; alert skipped",
            metadata={"skipped": "owner_is_user"},
        )
    token = (context.telegram_token or "").strip()
    if not token:
        return ToolResult(tool_name=name, success=False, output="",
                          error="telegram_token not configured; cannot alert")
    text = f"[Nūr alert] {reason}\n\n{detail}"
    if len(text) > _MAX_ALERT_LEN:
        text = text[: _MAX_ALERT_LEN - 3] + "..."
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = httpx.post(
            url, json={"chat_id": owner, "text": text}, timeout=10.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return ToolResult(
            tool_name=name, success=False, output="",
            error=f"telegram send failed: {type(exc).__name__}: {exc}",
        )
    return ToolResult(
        tool_name=name,
        success=True,
        output=f"Owner alerted: {reason}",
        metadata={"owner_chat_id": owner, "reason": reason},
        side_effect_summary="sent telegram message to owner",
    )


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def create_handlers(
    get_context: Callable[[], SelfActionContext | None],
) -> dict[str, ToolHandler]:
    """Return handlers that read the latest context at call time.

    The pipeline attaches a fresh ``SelfActionContext`` to the executor
    once it knows the session info; until then every handler returns a
    structured failure result rather than raising.
    """

    def bind(fn: Callable[[SelfActionContext, dict[str, Any]], ToolResult]) -> ToolHandler:
        def handler(args: dict[str, Any]) -> ToolResult:
            ctx = get_context() or SelfActionContext()
            return fn(ctx, args)
        return handler

    return {
        "self.snapshot_state": bind(_snapshot_state),
        "self.log_event": bind(_log_event),
        "self.note": bind(_note),
        "self.verify": bind(_verify),
        "self.alert_owner": bind(_alert_owner),
    }
