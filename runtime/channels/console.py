"""Console channel — stdin/stdout interactive loop."""

from __future__ import annotations

import asyncio
import logging
import sys

from runtime.sessions.manager import SessionManager

log = logging.getLogger(__name__)

QUIT_COMMANDS = frozenset(("/quit", "/exit", "exit", "quit"))


class ConsoleChannel:
    """Interactive console channel.

    Reads lines from stdin in a worker thread (non-blocking for the event
    loop) and prints responses to stdout.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        user_id: str = "user",
        platform: str = "console",
        chat_id: str = "direct",
    ) -> None:
        self._manager = session_manager
        self._user_id = user_id
        self._platform = platform
        self._chat_id = chat_id
        self._running = False

    async def start(self) -> None:
        """Run the interactive input loop until quit or EOF."""
        self._running = True
        log.info("Console channel started (type /quit to exit)")
        print("Jarvis console — type /quit to exit", flush=True)

        while self._running:
            try:
                line = await asyncio.to_thread(self._read_line)
            except EOFError:
                break

            if line is None or line.strip().lower() in QUIT_COMMANDS:
                break

            text = line.strip()
            if not text:
                continue

            try:
                response = await self._manager.handle_message(
                    self._platform, self._user_id, self._chat_id, text,
                )
                print(f"Jarvis: {response}", flush=True)
            except RuntimeError as exc:
                print(f"[error] {exc}", file=sys.stderr, flush=True)
            except Exception:
                log.exception("Error processing message")
                print("[error] Internal error", file=sys.stderr, flush=True)

    async def stop(self) -> None:
        self._running = False

    @staticmethod
    def _read_line() -> str | None:
        """Blocking stdin read — runs in a worker thread."""
        try:
            return input("You: ")
        except EOFError:
            return None
