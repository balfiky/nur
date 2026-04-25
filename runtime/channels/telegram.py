"""Telegram channel — long-polling adapter with allowlist, dedupe, and typing."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

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
    - Commands: /status, /new, /reset, /debug
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

        if cmd == "/status":
            await self._cmd_status(user_id, chat_id)
        elif cmd == "/new":
            await self._cmd_new(user_id, chat_id)
        elif cmd == "/reset":
            await self._cmd_reset(user_id, chat_id)
        elif cmd == "/debug":
            await self._cmd_debug(user_id, chat_id)
        elif cmd == "/start":
            await self._client.send_message(chat_id, "Hello. Send me a message.")
        else:
            await self._client.send_message(chat_id, f"Unknown command: {cmd}")

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

    async def _cmd_reset(self, user_id: str, chat_id: int) -> None:
        session_key = f"telegram:{user_id}:{chat_id}"
        session = self._manager.active_sessions.get(session_key)
        if session is None:
            await self._client.send_message(chat_id, "No active session to reset.")
            return

        await self._manager.evict_session(session_key)
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
            self._manager.config.user_state_path(rel_key),
        ):
            removed = _remove_file_if_exists(path) or removed
            removed = _remove_file_if_exists(path + ".tmp") or removed

        message = "Started a new conversation. Long-term memory is unchanged."
        if not removed:
            message = "Started a new conversation."
        await self._client.send_message(chat_id, message)

    async def _cmd_debug(self, user_id: str, chat_id: int) -> None:
        await self._client.send_message(
            chat_id,
            "Debug API not available yet (Phase 4).",
        )


def _remove_file_if_exists(path: str) -> bool:
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        log.warning("Could not remove Telegram session state file: %s", path)
        return False
