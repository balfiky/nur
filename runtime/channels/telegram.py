"""Telegram channel — long-polling adapter with allowlist, dedupe, and typing."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

from runtime.debug.explain import explain_turn
from runtime.debug.persona_view import build_persona_view
from runtime.debug.relationship_view import build_relationship_view
from runtime.sessions.manager import SessionManager

log = logging.getLogger(__name__)

# Typing action expires after 5 s on Telegram; resend every 4 s.
_TYPING_INTERVAL = 4.0


def is_telegram_token_pollable(token: str) -> bool:
    """Return true for tokens that are shaped enough to start polling.

    Admin validation may store draft tokens so operators can fix them later,
    but the runtime should not hammer Telegram with obviously malformed values.
    A real Bot API token starts with a numeric bot id, then a colon, then a
    non-empty secret.
    """
    value = (token or "").strip()
    if not value or any(ch.isspace() for ch in value):
        return False
    bot_id, sep, secret = value.partition(":")
    return bool(sep and bot_id.isdigit() and secret)


# ---------------------------------------------------------------------------
# Dedupe cache
# ---------------------------------------------------------------------------

class DedupeCache:
    """TTL cache of recently seen Telegram update IDs."""

    def __init__(self, ttl: float = 60.0) -> None:
        self._seen: dict[int, float] = {}
        self._ttl = ttl

    def is_duplicate(self, update_id: int) -> bool:
        self._evict_expired()
        return update_id in self._seen

    def mark(self, update_id: int) -> None:
        self._seen[update_id] = time.time()

    def _evict_expired(self) -> None:
        cutoff = time.time() - self._ttl
        self._seen = {k: v for k, v in self._seen.items() if v > cutoff}

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._seen)


# ---------------------------------------------------------------------------
# Thin Telegram Bot API client
# ---------------------------------------------------------------------------

class TelegramClient:
    """Minimal async wrapper around the Telegram Bot API (httpx)."""

    def __init__(self, token: str, timeout: float = 60.0) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._http = httpx.AsyncClient(timeout=self._timeout)

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def get_updates(
        self, offset: int | None = None, timeout: int = 30,
    ) -> list[dict]:
        params: dict = {"timeout": timeout, "allowed_updates": '["message"]'}
        if offset is not None:
            params["offset"] = offset
        resp = await self._http.get(
            f"{self._base}/getUpdates", params=params,
            timeout=timeout + 10,
        )
        resp.raise_for_status()
        return resp.json()["result"]

    async def send_message(self, chat_id: int, text: str) -> dict:
        resp = await self._http.post(
            f"{self._base}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        resp.raise_for_status()
        return resp.json().get("result", {})

    async def send_typing(self, chat_id: int) -> None:
        try:
            await self._http.post(
                f"{self._base}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
            )
        except Exception:
            pass  # typing indicator is best-effort


# ---------------------------------------------------------------------------
# Telegram channel
# ---------------------------------------------------------------------------

@dataclass
class TelegramConfig:
    """Telegram-specific configuration."""
    token: str = ""
    allowlist: set[str] = field(default_factory=set)
    poll_timeout: int = 30
    dedupe_ttl: float = 60.0


class TelegramChannel:
    """Telegram long-polling channel.

    Features:
    - Allowlist by numeric user ID (empty = allow all)
    - Per-update dedupe with TTL cache
    - Typing indicators resent every 4 s while Nūr processes
    - Commands: /help, /status, /mental, /mood, /new, /reset, /debug
    """

    def __init__(
        self,
        client: TelegramClient,
        session_manager: SessionManager,
        config: TelegramConfig,
    ) -> None:
        self._client = client
        self._manager = session_manager
        self._config = config
        self._allowlist = config.allowlist
        self._dedupe = DedupeCache(ttl=config.dedupe_ttl)
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Run the long-polling loop until stopped."""
        self._running = True
        await self._client.start()
        log.info("Telegram channel started (polling)")

        offset: int | None = None
        while self._running:
            try:
                updates = await self._client.get_updates(
                    offset=offset, timeout=self._config.poll_timeout,
                )
                for update in updates:
                    update_id = update["update_id"]
                    offset = update_id + 1
                    await self.handle_update(update)
            except asyncio.CancelledError:
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {401, 403}:
                    log.error(
                        "Telegram polling stopped: bot token was rejected with HTTP %s",
                        exc.response.status_code,
                    )
                    self._running = False
                    break
                if self._running:
                    log.exception("Telegram polling error")
                    await asyncio.sleep(1)
            except Exception:
                if self._running:
                    log.exception("Telegram polling error")
                    await asyncio.sleep(1)

    async def stop(self) -> None:
        self._running = False
        await self._client.close()

    # ------------------------------------------------------------------
    # Update handling (public for testability)
    # ------------------------------------------------------------------

    async def handle_update(self, update: dict) -> None:
        """Process a single Telegram update."""
        update_id = update.get("update_id")
        if update_id is None:
            return

        # Dedupe
        if self._dedupe.is_duplicate(update_id):
            log.debug("Duplicate update %d dropped", update_id)
            return
        self._dedupe.mark(update_id)

        # Extract message
        msg = update.get("message")
        if msg is None:
            return
        if "text" not in msg:
            return  # text messages only

        from_user = msg.get("from")
        if from_user is None:
            return
        user_id = str(from_user["id"])
        chat_id: int = msg["chat"]["id"]
        text: str = msg["text"]

        # Allowlist
        if self._allowlist and user_id not in self._allowlist:
            log.debug("User %s not in allowlist, ignoring", user_id)
            return

        # Commands
        if text.startswith("/"):
            await self._handle_command(text, user_id, chat_id)
            return

        # Regular message — process with typing indicator
        await self._process_message(user_id, chat_id, text)

    # ------------------------------------------------------------------
    # Message processing with typing
    # ------------------------------------------------------------------

    async def _process_message(
        self, user_id: str, chat_id: int, text: str,
    ) -> None:
        typing_task = asyncio.create_task(self._typing_loop(chat_id))
        try:
            response = await self._manager.handle_message(
                "telegram", user_id, str(chat_id), text,
            )
            await self._client.send_message(chat_id, response)
        except RuntimeError as exc:
            await self._client.send_message(chat_id, f"[busy] {exc}")
        except Exception:
            log.exception("Error processing Telegram message from %s", user_id)
            await self._client.send_message(
                chat_id, "[error] Something went wrong.",
            )
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

    async def _typing_loop(self, chat_id: int) -> None:
        """Send typing indicators every 4 s until cancelled."""
        try:
            while True:
                await self._client.send_typing(chat_id)
                await asyncio.sleep(_TYPING_INTERVAL)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _handle_command(
        self, text: str, user_id: str, chat_id: int,
    ) -> None:
        cmd = text.split()[0].lower().split("@")[0]  # strip @botname suffix

        if cmd in {"/help", "/commands"}:
            await self._cmd_help(chat_id)
        elif cmd == "/status":
            await self._cmd_status(user_id, chat_id)
        elif cmd in {"/mental", "/mood"}:
            await self._cmd_mental(user_id, chat_id)
        elif cmd == "/new":
            await self._cmd_new(user_id, chat_id)
        elif cmd == "/reset":
            await self._cmd_reset(user_id, chat_id)
        elif cmd == "/debug":
            await self._cmd_debug(user_id, chat_id)
        elif cmd == "/state":
            await self._cmd_state(user_id, chat_id)
        elif cmd == "/why":
            await self._cmd_why(user_id, chat_id)
        elif cmd == "/memory":
            await self._cmd_memory(user_id, chat_id)
        elif cmd == "/loops":
            await self._cmd_loops(user_id, chat_id)
        elif cmd == "/repair":
            await self._cmd_repair(user_id, chat_id)
        elif cmd == "/persona":
            await self._cmd_persona(text, user_id, chat_id)
        elif cmd == "/start":
            await self._client.send_message(
                chat_id,
                "Hello. Send me a message.\n\n" + _telegram_help_text(),
            )
        else:
            await self._client.send_message(
                chat_id,
                f"Unknown command: {cmd}\n\n" + _telegram_help_text(),
            )

    async def _cmd_help(self, chat_id: int) -> None:
        await self._client.send_message(chat_id, _telegram_help_text())

    async def _cmd_status(self, user_id: str, chat_id: int) -> None:
        session_key = f"telegram:{user_id}:{chat_id}"
        session = self._manager.active_sessions.get(session_key)
        if session is None:
            await self._client.send_message(chat_id, "No active session.")
            return

        snap = session.pipeline.engine.snapshot()
        label = session.pipeline.engine.to_emotion_label()
        lines = [f"Emotion: {label}"]
        for mod, val in snap.items():
            bar = "█" * int(val * 10) + "░" * (10 - int(val * 10))
            lines.append(f"  {mod:11s} {bar} {val:.2f}")
        await self._client.send_message(chat_id, "\n".join(lines))

    async def _cmd_mental(self, user_id: str, chat_id: int) -> None:
        session = await self._manager.ensure_session(
            "telegram",
            user_id,
            str(chat_id),
        )
        snap = session.pipeline.engine.snapshot()
        label = session.pipeline.engine.to_emotion_label()
        active_loops = session.pipeline.engine.active_unresolved()
        stability = _mental_stability_score(snap)
        lines = [
            f"Mental state: {label}",
            f"Mental health: {_mental_health_label(stability)} ({stability}/100)",
        ]
        for mod in (
            "arousal",
            "valence",
            "certainty",
            "bonding",
            "energy",
            "resolution",
        ):
            val = snap.get(mod, 0.0)
            lines.append(f"  {mod:11s} {_bar(val)} {val:.2f}")
        lines.append(f"Open loops: {len(active_loops)}")

        last_debug = session.last_debug
        if last_debug is not None:
            lines.append(
                "Last self-check: "
                f"{'passed' if last_debug.self_check_passed else 'failed'}"
            )
            if last_debug.response_strategy:
                lines.append(f"Strategy: {last_debug.response_strategy}")
            if last_debug.tool_trace is not None:
                lines.append(f"Tool executions: {last_debug.tool_trace.loop_count}")

        await self._client.send_message(chat_id, "\n".join(lines))

    async def _cmd_reset(self, user_id: str, chat_id: int) -> None:
        session_key = f"telegram:{user_id}:{chat_id}"
        session = self._manager.active_sessions.get(session_key)
        if session is None:
            await self._client.send_message(chat_id, "No active session to reset.")
            return

        await self._manager.evict_session(session_key)
        for path in (
            self._manager.config.session_history_path(session_key),
        ):
            _remove_file_if_exists(path)
            _remove_file_if_exists(path + ".tmp")
        await self._client.send_message(
            chat_id, "Session digested, state saved, session closed.",
        )

    async def _cmd_new(self, user_id: str, chat_id: int) -> None:
        """Close the current Telegram chat session and discard hot state."""
        session_key = f"telegram:{user_id}:{chat_id}"
        rel_key = f"telegram:{user_id}"

        if session_key in self._manager.active_sessions:
            await self._manager.evict_session(session_key)

        removed = False
        for path in (
            self._manager.config.session_state_path(session_key),
            self._manager.config.session_history_path(session_key),
            self._manager.config.user_state_path(rel_key),
        ):
            removed = _remove_file_if_exists(path) or removed
            removed = _remove_file_if_exists(path + ".tmp") or removed

        await self._manager.ensure_session("telegram", user_id, str(chat_id))

        message = "Started a new conversation. Relationship memory remains available."
        if not removed:
            message = "Started a new conversation."
        await self._client.send_message(chat_id, message)

    async def _cmd_debug(self, user_id: str, chat_id: int) -> None:
        session_key = f"telegram:{user_id}:{chat_id}"
        session = self._manager.active_sessions.get(session_key)
        if session is None:
            await self._client.send_message(
                chat_id,
                "Debug: no active session. Send a message first, or use /mental "
                "to create and inspect one.",
            )
            return

        last_debug = session.last_debug
        lines = [
            f"Debug session: {session_key}",
            f"Emotion: {session.pipeline.engine.to_emotion_label()}",
            f"Last turn: {'yes' if last_debug else 'none'}",
        ]
        if last_debug is not None:
            lines.append(
                "Self-check: "
                f"{'passed' if last_debug.self_check_passed else 'failed'}"
            )
            if last_debug.response_strategy:
                lines.append(f"Strategy: {last_debug.response_strategy}")
            if last_debug.tool_trace is not None:
                lines.append(f"Tool loops: {last_debug.tool_trace.loop_count}")
        await self._client.send_message(chat_id, "\n".join(lines))

    async def _cmd_state(self, user_id: str, chat_id: int) -> None:
        session = self._active_session(user_id, chat_id)
        if session is None:
            await self._client.send_message(chat_id, "No active session. Send a message first.")
            return

        last_debug = session.last_debug
        snap = session.pipeline.engine.snapshot()
        label = session.pipeline.engine.to_emotion_label()
        strategy = getattr(last_debug, "response_strategy", "") if last_debug else ""
        open_loop_count = 0
        if last_debug is not None:
            view = build_relationship_view(last_debug)
            open_loop_count = len(view.get("open_loops", []))

        lines = [
            "Nūr state:",
            f"emotion: {label}",
        ]
        if strategy:
            lines.append(f"strategy: {strategy}")
        lines.append(
            " · ".join(
                f"{mod} {snap.get(mod, 0.0):.2f}"
                for mod in ("arousal", "valence", "bonding", "energy", "resolution")
            )
        )
        lines.append(f"open loops: {open_loop_count}")
        await self._client.send_message(chat_id, "\n".join(lines))

    async def _cmd_why(self, user_id: str, chat_id: int) -> None:
        session = self._active_session(user_id, chat_id)
        if session is None:
            await self._client.send_message(chat_id, "No active session. Send a message first.")
            return
        if session.last_debug is None:
            await self._client.send_message(chat_id, "No turn explanation is available yet.")
            return

        explanation = explain_turn(session.last_debug)
        lines = [
            "Why this response:",
            explanation.get("interpretation", ""),
            explanation.get("strategy", ""),
            explanation.get("memory", ""),
            explanation.get("life_history", ""),
            explanation.get("tools", ""),
        ]
        await self._client.send_message(chat_id, "\n".join(line for line in lines if line))

    async def _cmd_memory(self, user_id: str, chat_id: int) -> None:
        session = self._active_session(user_id, chat_id)
        if session is None:
            await self._client.send_message(chat_id, "No active session. Send a message first.")
            return
        if session.last_debug is None:
            await self._client.send_message(chat_id, "No turn memory summary is available yet.")
            return

        debug = session.last_debug
        view = build_relationship_view(debug)
        relationship = getattr(debug, "relationship_context", None)
        summary = getattr(relationship, "summary", "") if relationship else ""
        lines = ["What I remember right now:"]
        if summary:
            lines.append(f"relationship: {summary}")
        lines.append(f"open loops: {len(view.get('open_loops', []))}")
        lines.append(f"recent relationship events: {len(view.get('recent_relationship_events', []))}")
        memory_used = view.get("memory_used", {})
        lines.append(f"long-term memories used: {memory_used.get('long_term_count', 0)}")
        lines.append(f"semantic memories used: {memory_used.get('semantic_count', 0)}")
        await self._client.send_message(chat_id, "\n".join(lines))

    async def _cmd_loops(self, user_id: str, chat_id: int) -> None:
        session = self._active_session(user_id, chat_id)
        if session is None:
            await self._client.send_message(chat_id, "No active session. Send a message first.")
            return
        if session.last_debug is None:
            await self._client.send_message(chat_id, "No open-loop summary is available yet.")
            return

        loops = build_relationship_view(session.last_debug).get("open_loops", [])
        if not loops:
            await self._client.send_message(chat_id, "No active relationship loops.")
            return
        lines = ["Active loops:"]
        for loop in loops[:5]:
            topic = loop.get("topic") or loop.get("loop_kind") or "relationship"
            description = loop.get("description") or ""
            intensity = float(loop.get("intensity") or 0.0)
            status = loop.get("status") or "open"
            lines.append(f"- {topic}: {description} ({status}, intensity {intensity:.2f})")
        await self._client.send_message(chat_id, "\n".join(lines))

    async def _cmd_repair(self, user_id: str, chat_id: int) -> None:
        session = self._active_session(user_id, chat_id)
        if session is None:
            await self._client.send_message(chat_id, "No active session. Send a message first.")
            return
        if session.last_debug is None:
            await self._client.send_message(chat_id, "No repair context is available yet.")
            return

        view = build_relationship_view(session.last_debug)
        events = view.get("recent_relationship_events", [])
        loops = view.get("open_loops", [])
        latest: dict[str, dict] = {}
        for event in events:
            kind = event.get("event_kind", "")
            if kind in {"rupture", "repair", "commitment", "recurring_tension"} and kind not in latest:
                latest[kind] = event

        lines = ["Repair context:"]
        for kind in ("rupture", "repair", "recurring_tension", "commitment"):
            event = latest.get(kind)
            if event:
                topic = event.get("topic") or "relationship"
                lines.append(f"{kind.replace('_', ' ')}: {topic}")
        lines.append(f"open loops remain: {len(loops)}")
        await self._client.send_message(chat_id, "\n".join(lines))

    async def _cmd_persona(self, text: str, user_id: str, chat_id: int) -> None:
        session = self._active_session(user_id, chat_id)
        if session is None:
            await self._client.send_message(chat_id, "No active session. Send a message first.")
            return

        parts = text.split(maxsplit=1)
        section = parts[1].strip().lower() if len(parts) > 1 else "summary"
        if section not in {"summary", "emotions", "emotion", "perception", "life", "skills", "tools", "memory", "all"}:
            section = "summary"
        view = build_persona_view(session=session, session_key=f"telegram:{user_id}:{chat_id}")

        if section in {"emotions", "emotion"}:
            message = _format_persona_emotions(view)
        elif section == "perception":
            message = _format_persona_perception(view)
        elif section == "life":
            message = _format_persona_life(view)
        elif section in {"skills", "tools"}:
            message = _format_persona_skills_tools(view)
        elif section == "memory":
            message = _format_persona_memory(view)
        elif section == "all":
            message = "\n\n".join([
                _format_persona_summary(view),
                _format_persona_perception(view),
                _format_persona_life(view),
                _format_persona_memory(view),
                _format_persona_skills_tools(view),
            ])
        else:
            message = _format_persona_summary(view)
        await self._client.send_message(chat_id, message)

    def _active_session(self, user_id: str, chat_id: int):
        session_key = f"telegram:{user_id}:{chat_id}"
        return self._manager.active_sessions.get(session_key)


def _telegram_help_text() -> str:
    return "\n".join([
        "Commands:",
        "/help - show this command list",
        "/status - show active emotional modulators",
        "/mental or /mood - create/inspect current mental state",
        "/new - start a fresh hot conversation",
        "/reset - digest, save, and close the active session",
        "/debug - show last-turn debug summary",
        "/state - show compact cognitive state",
        "/why - explain the last response",
        "/memory - summarize current-turn memory context",
        "/loops - list active relationship loops",
        "/repair - show recent rupture/repair context",
        "/persona - unified persona state; add emotions, perception, life, memory, skills, or all",
    ])


def _format_persona_summary(view: dict) -> str:
    emotions = view.get("emotions", {}) or {}
    relationship = view.get("relationship", {}) or {}
    perception = view.get("perception", {}) or {}
    life = view.get("life", {}) or {}
    skills_tools = view.get("skills_tools", {}) or {}
    drivers = ", ".join((emotions.get("drivers") or [])[:3]) or "balanced state"
    lines = [
        "Persona:",
        f"emotion: {emotions.get('simple_label') or emotions.get('primary') or 'neutral'}"
        f" ({emotions.get('primary') or 'neutral'})",
        f"drivers: {drivers}",
        f"strategy: {relationship.get('strategy') or 'none'}",
        f"perception: {perception.get('summary') or 'No perception recorded.'}",
        f"open loops: {relationship.get('open_loop_count', 0)}",
        f"life pressures: {len(life.get('active_pressures') or {})}",
        f"skills/tools: {skills_tools.get('enabled_skill_count', 0)} skill(s), "
        f"{skills_tools.get('tools_used', 0)} tool(s) used",
    ]
    return "\n".join(lines)


def _format_persona_emotions(view: dict) -> str:
    emotions = view.get("emotions", {}) or {}
    mods = emotions.get("modulators", {}) or {}
    secondary = ", ".join(emotions.get("secondary") or []) or "none"
    lines = [
        "Persona emotions:",
        f"primary: {emotions.get('primary') or 'neutral'}",
        f"simple: {emotions.get('simple_label') or 'neutral'}",
        f"secondary: {secondary}",
        f"intensity: {float(emotions.get('intensity') or 0.0):.2f}",
        f"confidence: {float(emotions.get('confidence') or 0.0):.2f}",
        "drivers: " + (", ".join(emotions.get("drivers") or []) or "balanced state"),
    ]
    for name in ("arousal", "valence", "certainty", "bonding", "energy", "resolution"):
        item = mods.get(name, {}) or {}
        lines.append(
            f"{name}: {float(item.get('value') or 0.0):.2f}"
            f" ({item.get('level') or 'medium'})"
        )
    return "\n".join(lines)


def _format_persona_perception(view: dict) -> str:
    perception = view.get("perception", {}) or {}
    lines = [
        "Persona perception:",
        perception.get("summary") or "No perception recorded.",
        f"target: {perception.get('target') or 'none'}",
        f"social move: {perception.get('social_move') or 'none'}",
        f"intent: {perception.get('intent') or 'none'}",
        f"vulnerability: {float(perception.get('vulnerability') or 0.0):.2f}",
        f"action need: {float(perception.get('action_need') or 0.0):.2f}",
    ]
    return "\n".join(lines)


def _format_persona_life(view: dict) -> str:
    life = view.get("life", {}) or {}
    pressures = life.get("active_pressures") or {}
    effects = life.get("effects") or {}
    pressure_text = ", ".join(
        f"{key.replace('_pressure', '')} {float(value):+.2f}"
        for key, value in pressures.items()
    ) or "none"
    effect_text = ", ".join(str(key) for key in effects.keys()) or "none"
    lines = [
        "Persona life:",
        f"context available: {'yes' if life.get('context_available') else 'no'}",
        f"beliefs: {life.get('belief_count', 0)}",
        f"drives: {life.get('drive_count', 0)}",
        f"recent evolution: {life.get('recent_evolution_count', 0)}",
        f"active pressures: {pressure_text}",
        f"bounded effects: {effect_text}",
    ]
    return "\n".join(lines)


def _format_persona_memory(view: dict) -> str:
    memory = view.get("memory", {}) or {}
    lines = [
        "Persona memory:",
        f"relationship used: {'yes' if memory.get('relationship_context_used') else 'no'}",
        f"open loops: {memory.get('open_loop_count', 0)}",
        f"recent relationship events: {memory.get('recent_event_count', 0)}",
        f"long-term memories: {memory.get('long_term_count', 0)}",
        f"semantic memories: {memory.get('semantic_count', 0)}",
    ]
    for summary in (memory.get("long_term_summaries") or [])[:2]:
        if summary:
            lines.append(f"long-term: {summary}")
    for summary in (memory.get("semantic_summaries") or [])[:2]:
        if summary:
            lines.append(f"semantic: {summary}")
    return "\n".join(lines)


def _format_persona_skills_tools(view: dict) -> str:
    skills_tools = view.get("skills_tools", {}) or {}
    skills = skills_tools.get("enabled_skills") or []
    tool_names = skills_tools.get("tool_names") or []
    lines = [
        "Persona skills/tools:",
        f"enabled skills: {skills_tools.get('enabled_skill_count', 0)}",
        f"tools considered: {skills_tools.get('tools_considered', 0)}",
        f"tools used: {skills_tools.get('tools_used', 0)}",
        skills_tools.get("summary") or "No tools were considered.",
    ]
    for skill in skills[:3]:
        name = skill.get("name") or skill.get("id") or "skill"
        lines.append(f"skill: {name}")
    if tool_names:
        lines.append("used: " + ", ".join(tool_names[:5]))
    return "\n".join(lines)


def _remove_file_if_exists(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        log.warning("Could not remove Telegram session state file: %s", path)
        return False


def _bar(value: float) -> str:
    filled = max(0, min(10, int(value * 10)))
    return "█" * filled + "░" * (10 - filled)


def _mental_stability_score(snapshot: dict[str, float]) -> int:
    arousal = snapshot.get("arousal", 0.5)
    valence = snapshot.get("valence", 0.5)
    certainty = snapshot.get("certainty", 0.5)
    energy = snapshot.get("energy", 1.0)
    resolution = snapshot.get("resolution", 0.0)
    strain = (
        abs(arousal - 0.5) * 0.7
        + abs(valence - 0.5) * 0.9
        + (1.0 - certainty) * 0.5
        + (1.0 - energy) * 0.8
        + resolution * 0.9
    )
    return max(0, min(100, round(100 * (1.0 - min(1.0, strain / 2.2)))))


def _mental_health_label(score: int) -> str:
    if score >= 80:
        return "stable"
    if score >= 60:
        return "strained"
    if score >= 40:
        return "distressed"
    return "critical"
