"""Console channel — stdin/stdout interactive loop."""

from __future__ import annotations

import asyncio
import logging
import select
import sys

from runtime.sessions.manager import SessionManager

log = logging.getLogger(__name__)

QUIT_COMMANDS = frozenset(("/quit", "/exit", "exit", "quit"))

# How often the read loop wakes to check ``_running``. Low enough that SIGINT
# and ``/quit`` feel instant, high enough that idle CPU use is negligible.
_POLL_INTERVAL = 0.2


class ConsoleChannel:
    """Interactive console channel.

    Reads lines from stdin by polling with ``select`` so the loop can honor
    the shutdown flag without waiting for a stuck ``input()`` to return.
    An earlier version used ``asyncio.to_thread(input)``; that left a
    worker thread blocked on stdin, which in turn blocked
    ``loop.shutdown_default_executor`` on interpreter exit and delayed
    shutdown by tens of seconds on SIGINT.
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
        """Run the interactive input loop until /quit, EOF, or stop()."""
        self._running = True
        log.info("Console channel started (type /quit to exit)")
        print("Jarvis console — type /quit to exit", flush=True)

        while self._running:
            line = await self._next_line()
            if line is None:
                break  # EOF or stop requested

            if line.strip().lower() in QUIT_COMMANDS:
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

    async def _next_line(self) -> str | None:
        """Await one line from stdin without blocking shutdown.

        Prints the prompt, then polls stdin via ``select`` on a worker
        thread with a short timeout so setting ``self._running = False``
        from anywhere on the loop takes effect within ``_POLL_INTERVAL``.
        Returns ``None`` on EOF or when stop has been requested.
        """
        print("You: ", end="", flush=True)
        while self._running:
            ready = await asyncio.to_thread(
                self._poll_stdin, _POLL_INTERVAL,
            )
            if ready:
                line = sys.stdin.readline()
                if not line:
                    return None  # EOF
                return line.rstrip("\n")
        return None

    @staticmethod
    def _poll_stdin(timeout: float) -> bool:
        """Return True iff stdin has data ready, False on timeout or error."""
        try:
            r, _, _ = select.select([sys.stdin], [], [], timeout)
        except (OSError, ValueError):
            # Stdin may be closed or not a real fd (e.g. certain test runners).
            return False
        return bool(r)

    @staticmethod
    def _read_line() -> str | None:
        """Legacy read helper kept for back-compat with any external caller.

        Not used by the main loop anymore — ``_next_line`` handles reads.
        """
        try:
            return input("You: ")
        except EOFError:
            return None
