"""JarvisApp — top-level runtime orchestrator."""

from __future__ import annotations

import asyncio
import logging
import signal

from runtime.config import RuntimeConfig
from runtime.channels.console import ConsoleChannel
from runtime.debug.api import create_debug_app
from runtime.llm.backend import create_llm_backend
from runtime.sessions.manager import SessionManager

log = logging.getLogger(__name__)


class JarvisApp:
    """Lifecycle manager for the Jarvis Runtime.

    Wires together the session manager, LLM backend, channels, and debug API.
    Handles graceful shutdown on SIGINT / SIGTERM.

    Channel startup is config-driven:
      - ``console_enabled=True``  → interactive stdin/stdout loop
      - ``telegram_token`` set    → Telegram long-polling (background task)
      - Both disabled             → debug API only (blocks on shutdown event)
    """

    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or RuntimeConfig()
        self.session_manager = SessionManager(
            config=self.config,
            backend_factory=lambda: create_llm_backend(self.config),
            proactive_callback=self._deliver_proactive,
        )
        self._shutdown_event = asyncio.Event()
        self._console: ConsoleChannel | None = None
        self._telegram = None  # TelegramChannel | None
        self._debug_server = None  # uvicorn.Server | None

    async def run(self) -> None:
        """Start channels and block until shutdown."""
        loop = asyncio.get_running_loop()

        # Install signal handlers (Unix only — ignored on Windows)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._signal_shutdown)
            except NotImplementedError:
                pass

        # Start debug API server
        debug_task = asyncio.create_task(self._start_debug_server())

        # Start Telegram channel if token is configured
        telegram_task = None
        if self.config.telegram_token:
            telegram_task = asyncio.create_task(self._start_telegram())

        # Start proactive behavior loop if enabled
        proactive_task = None
        if self.config.proactive_enabled:
            proactive_task = asyncio.create_task(
                self.session_manager.run_proactive_loop()
            )

        try:
            if self.config.console_enabled:
                # Console runs in foreground — blocks until /quit or EOF
                self._console = ConsoleChannel(self.session_manager)
                await self._console.start()
            else:
                # Headless mode — block until shutdown signal
                await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            if proactive_task is not None:
                proactive_task.cancel()
                try:
                    await proactive_task
                except asyncio.CancelledError:
                    pass
            if telegram_task is not None:
                telegram_task.cancel()
                try:
                    await telegram_task
                except asyncio.CancelledError:
                    pass
            debug_task.cancel()
            try:
                await debug_task
            except asyncio.CancelledError:
                pass
            await self.shutdown()

    async def _start_debug_server(self) -> None:
        """Start the debug API server (runs as a background task)."""
        import uvicorn

        debug_app = create_debug_app(self.session_manager)
        config = uvicorn.Config(
            debug_app,
            host=self.config.debug_host,
            port=self.config.debug_port,
            log_level="warning",
        )
        self._debug_server = uvicorn.Server(config)
        log.info(
            "Debug API at http://%s:%d/sessions",
            self.config.debug_host, self.config.debug_port,
        )
        try:
            await self._debug_server.serve()
        except asyncio.CancelledError:
            self._debug_server.should_exit = True

    async def _start_telegram(self) -> None:
        """Start the Telegram channel (runs as a background task)."""
        from runtime.channels.telegram import (
            TelegramChannel, TelegramClient, TelegramConfig,
        )
        tg_config = TelegramConfig(
            token=self.config.telegram_token,
            allowlist=self.config.telegram_allowlist,
            poll_timeout=self.config.telegram_poll_timeout,
            dedupe_ttl=self.config.dedupe_ttl,
        )
        client = TelegramClient(tg_config.token)
        self._telegram = TelegramChannel(client, self.session_manager, tg_config)
        try:
            await self._telegram.start()
        except asyncio.CancelledError:
            await self._telegram.stop()

    async def shutdown(self) -> None:
        """Gracefully drain all sessions and clean up."""
        if self._console is not None:
            await self._console.stop()

        if self._telegram is not None:
            await self._telegram.stop()

        if self._debug_server is not None:
            self._debug_server.should_exit = True

        log.info("Shutting down runtime")
        await self.session_manager.shutdown()
        log.info("Runtime shutdown complete")
        self._shutdown_event.set()

    async def _deliver_proactive(
        self, session_key: str, user_id: str, message: str,
    ) -> None:
        """Route a proactive message to the appropriate channel.

        Extracts platform and chat_id from session_key (platform:user_id:chat_id)
        and delivers through the matching channel.
        """
        parts = session_key.split(":", 2)
        if len(parts) < 3:
            log.warning("Cannot deliver proactive — bad session_key: %s", session_key)
            return
        platform, _, chat_id = parts

        if platform == "console" and self._console is not None:
            print(f"Jarvis: {message}", flush=True)
        elif platform == "telegram" and self._telegram is not None:
            try:
                await self._telegram._client.send_message(int(chat_id), message)
            except Exception:
                log.exception("Failed to deliver proactive to Telegram %s", chat_id)
        else:
            log.warning(
                "No channel for proactive delivery: platform=%s session=%s",
                platform, session_key,
            )

    def _signal_shutdown(self) -> None:
        """Signal handler — triggers graceful shutdown."""
        log.info("Shutdown signal received")
        # Unblock headless wait
        self._shutdown_event.set()
        # Stop console if running
        if self._console is not None:
            asyncio.get_running_loop().call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._console.stop())
            )
