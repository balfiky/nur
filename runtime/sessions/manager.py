"""Session manager — routes messages to per-user sessions."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable

from core.dual_process.generator import LLMBackend
from pipeline import CognitivePipeline
from runtime.config import RuntimeConfig
from runtime.sessions.persistence import load_engine_state
from runtime.sessions.user_session import UserSession

log = logging.getLogger(__name__)


class SessionManager:
    """Manages per-user sessions with backpressure and lifecycle support.

    Each user gets a dedicated CognitivePipeline and a serialized message
    queue.  Sessions are created lazily on first message and can be evicted
    on timeout or shutdown.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        backend_factory: Callable[[], LLMBackend] | None = None,
    ) -> None:
        self.config = config
        self._backend_factory = backend_factory
        self._sessions: dict[str, UserSession] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_message(
        self,
        platform: str,
        user_id: str,
        chat_id: str,
        text: str,
    ) -> str:
        """Route a message to the correct user session and return the response.

        Identity keys (per spec):
          relationship: platform:user_id
          session:      platform:user_id:chat_id
        """
        rel_key = f"{platform}:{user_id}"

        session = await self._get_or_create(rel_key, user_id)
        return await session.send(text)

    @property
    def active_sessions(self) -> dict[str, UserSession]:
        """Read-only view of active sessions (for debug / inspection)."""
        return dict(self._sessions)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _get_or_create(self, rel_key: str, user_id: str) -> UserSession:
        """Return existing session or create a new one.

        Enforces max_active_sessions — raises RuntimeError at capacity.
        """
        async with self._lock:
            if rel_key in self._sessions:
                return self._sessions[rel_key]

            if len(self._sessions) >= self.config.max_active_sessions:
                raise RuntimeError(
                    f"Active session limit reached ({self.config.max_active_sessions})"
                )

            session = self._create_session(rel_key, user_id)
            session.start()
            self._sessions[rel_key] = session
            log.info("Session created: %s", rel_key)
            return session

    def _create_session(self, rel_key: str, user_id: str) -> UserSession:
        """Build a UserSession with pipeline, restore state if available."""
        data_dir = self.config.user_data_dir(rel_key)
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.config.shared_db_path), exist_ok=True)

        # Create LLM backend (one per pipeline for thread safety)
        backend = self._backend_factory() if self._backend_factory else None

        pipeline = CognitivePipeline(
            llm_backend=backend,
            llm_backend_fast=backend,
            db_path=self.config.user_db_path(rel_key),
            self_db_path=self.config.shared_db_path,
        )

        # Restore engine state from disk if present
        state_path = self.config.user_state_path(rel_key)
        saved = load_engine_state(state_path)
        if saved is not None:
            pipeline.restore_state(
                saved["modulator_snapshot"],
                saved_at=saved.get("saved_at"),
            )
            log.info("Restored state for %s (saved_at=%.0f)", rel_key,
                     saved.get("saved_at", 0))

        return UserSession(
            rel_key=rel_key,
            user_id=user_id,
            pipeline=pipeline,
            state_path=state_path,
            max_queue=self.config.max_queue_per_user,
        )

    async def evict_session(self, rel_key: str) -> None:
        """Evict a session: drain, end, save state, close pipeline."""
        async with self._lock:
            session = self._sessions.pop(rel_key, None)
        if session is None:
            return
        log.info("Evicting session: %s", rel_key)
        await session.drain_and_close()

    async def evict_idle(self) -> list[str]:
        """Evict sessions that have been idle longer than the timeout.

        Returns the list of evicted relationship keys.
        """
        now = time.time()
        timeout = self.config.session_timeout_seconds
        to_evict: list[str] = []

        async with self._lock:
            for rel_key, session in self._sessions.items():
                if now - session.last_activity > timeout:
                    to_evict.append(rel_key)

        for rel_key in to_evict:
            await self.evict_session(rel_key)

        return to_evict

    async def shutdown(self) -> None:
        """Gracefully shut down all active sessions."""
        async with self._lock:
            keys = list(self._sessions.keys())

        log.info("Shutting down %d active session(s)", len(keys))
        for rel_key in keys:
            await self.evict_session(rel_key)
