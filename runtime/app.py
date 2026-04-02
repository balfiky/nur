"""JarvisApp — top-level runtime orchestrator."""

from __future__ import annotations

import asyncio
import logging
import signal

from runtime.config import RuntimeConfig
from runtime.channels.console import ConsoleChannel
from runtime.llm.backend import create_llm_backend
from runtime.sessions.manager import SessionManager

log = logging.getLogger(__name__)


class JarvisApp:
    """Lifecycle manager for the Jarvis Runtime.

    Wires together the session manager, LLM backend, and channels.
    Handles graceful shutdown on SIGINT / SIGTERM.
    """

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()
        self.session_manager = SessionManager(
            config=self.config,
            backend_factory=create_llm_backend,
        )
        self._shutdown_event = asyncio.Event()
        self._console: ConsoleChannel | None = None

    async def run(self) -> None:
        """Start the console channel and block until shutdown."""
        loop = asyncio.get_running_loop()

        # Install signal handlers (Unix only — ignored on Windows)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._signal_shutdown)
            except NotImplementedError:
                pass

        self._console = ConsoleChannel(self.session_manager)

        try:
            await self._console.start()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully drain all sessions and clean up."""
        if self._console is not None:
            await self._console.stop()

        log.info("Shutting down runtime")
        await self.session_manager.shutdown()
        log.info("Runtime shutdown complete")
        self._shutdown_event.set()

    def _signal_shutdown(self) -> None:
        """Signal handler — triggers graceful shutdown."""
        log.info("Shutdown signal received")
        if self._console is not None:
            asyncio.get_running_loop().call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._console.stop())
            )
