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

    Sessions are keyed by **session_key** (``platform:user_id:chat_id``)
    so that the same user in a DM and a group chat gets separate active
    sessions. Persistent relational storage (DBs) is keyed by
    **rel_key** (``platform:user_id``) — the accumulated relationship.
    Hot engine state snapshots are keyed by **session_key** so multiple
    chat contexts do not overwrite one another on shutdown.

    A per-user ``asyncio.Lock`` serializes pipeline access across sessions
    for the same user, preventing concurrent SQLite writes to the shared
    per-user database.

    Timer-driven inactivity timeout: each session gets a per-session idle
    timer (via ``loop.call_later``).  When it fires, the session is evicted
    automatically — *unless* in-flight or queued work is still active.
    """

    def __init__(
        self,
        config: RuntimeConfig,
        backend_factory: Callable[[], LLMBackend] | None = None,
    ) -> None:
        self.config = config
        self._backend_factory = backend_factory
        self._sessions: dict[str, UserSession] = {}
        self._idle_timers: dict[str, asyncio.TimerHandle] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
        self._accepting = True

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
        """Route a message to the correct session and return the response.

        Identity keys (per spec section 6.0):
          relationship: ``platform:user_id``           — persistent state
          session:      ``platform:user_id:chat_id``   — active session
        """
        if not self._accepting:
            raise RuntimeError("Runtime is shutting down — not accepting messages")

        rel_key = f"{platform}:{user_id}"
        session_key = f"{platform}:{user_id}:{chat_id}"

        session = await self._get_or_create(session_key, rel_key, user_id)
        # Reset idle timer on acceptance (before processing starts)
        self._reset_idle_timer(session_key)
        try:
            return await session.send(text)
        finally:
            # Reset again on completion so the timeout counts from last activity
            self._reset_idle_timer(session_key)

    @property
    def active_sessions(self) -> dict[str, UserSession]:
        """Read-only view of active sessions (for debug / inspection)."""
        return dict(self._sessions)

    # ------------------------------------------------------------------
    # Idle timers (per-session, timer-driven)
    # ------------------------------------------------------------------

    def _start_idle_timer(self, session_key: str) -> None:
        """Schedule an idle-timeout eviction for *session_key*."""
        self._cancel_idle_timer(session_key)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop — unit-test edge case
        handle = loop.call_later(
            self.config.session_timeout_seconds,
            lambda sk=session_key: asyncio.ensure_future(self._timeout_evict(sk)),
        )
        self._idle_timers[session_key] = handle

    def _cancel_idle_timer(self, session_key: str) -> None:
        handle = self._idle_timers.pop(session_key, None)
        if handle is not None:
            handle.cancel()

    def _reset_idle_timer(self, session_key: str) -> None:
        """Cancel and restart the idle timer for *session_key*."""
        if session_key in self._sessions:
            self._start_idle_timer(session_key)

    def _cancel_all_idle_timers(self) -> None:
        for handle in self._idle_timers.values():
            handle.cancel()
        self._idle_timers.clear()

    async def _timeout_evict(self, session_key: str) -> None:
        """Called by the idle timer — evict the session if truly idle.

        Does NOT evict if:
        - session has queued messages
        - session is currently processing a message
        Instead reschedules the timer.
        """
        async with self._lock:
            session = self._sessions.get(session_key)
        if session is None:
            return
        # Guard: don't evict while work is in-flight or queued
        if not session._queue.empty() or session._processing:
            log.debug(
                "Timeout for %s but work active — rescheduling", session_key,
            )
            self._start_idle_timer(session_key)
            return
        log.info("Inactivity timeout for %s", session_key)
        await self.evict_session(session_key)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def _get_or_create(
        self,
        session_key: str,
        rel_key: str,
        user_id: str,
    ) -> UserSession:
        """Return existing session or create a new one.

        Enforces max_active_sessions — raises RuntimeError at capacity.
        """
        async with self._lock:
            if session_key in self._sessions:
                return self._sessions[session_key]

            if len(self._sessions) >= self.config.max_active_sessions:
                raise RuntimeError(
                    f"Active session limit reached ({self.config.max_active_sessions})"
                )

            session = self._create_session(session_key, rel_key, user_id)
            session.start()
            self._sessions[session_key] = session
            self._start_idle_timer(session_key)
            log.info("Session created: %s", session_key)
            return session

    def _get_or_create_user_lock(self, rel_key: str) -> asyncio.Lock:
        """Return the per-user lock, creating one if needed."""
        if rel_key not in self._user_locks:
            self._user_locks[rel_key] = asyncio.Lock()
        return self._user_locks[rel_key]

    def _create_session(
        self,
        session_key: str,
        rel_key: str,
        user_id: str,
    ) -> UserSession:
        """Build a UserSession with pipeline, restoring session state if available."""
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

        # Restore per-session engine state from disk if present. Fall back to the
        # legacy per-user path so older runtime state still restores once.
        state_path = self.config.session_state_path(session_key)
        saved = load_engine_state(state_path)
        if saved is None:
            saved = load_engine_state(self.config.user_state_path(rel_key))
        if saved is not None:
            pipeline.restore_state(
                saved["modulator_snapshot"],
                saved_at=saved.get("saved_at"),
            )
            log.info("Restored state for %s (saved_at=%.0f)", session_key,
                     saved.get("saved_at", 0))

        return UserSession(
            rel_key=rel_key,
            user_id=user_id,
            pipeline=pipeline,
            state_path=state_path,
            max_queue=self.config.max_queue_per_user,
            session_key=session_key,
            user_lock=self._get_or_create_user_lock(rel_key),
        )

    async def evict_session(self, session_key: str) -> None:
        """Evict a session: cancel timer, drain, end, save state, close pipeline."""
        self._cancel_idle_timer(session_key)
        async with self._lock:
            session = self._sessions.pop(session_key, None)
        if session is None:
            return
        log.info("Evicting session: %s", session_key)
        await session.drain_and_close()

    async def evict_idle(self) -> list[str]:
        """Evict sessions that have been idle longer than the timeout.

        Returns the list of evicted session keys.
        Note: with timer-driven eviction this is a fallback / manual sweep.
        """
        now = time.time()
        timeout = self.config.session_timeout_seconds
        to_evict: list[str] = []

        async with self._lock:
            for session_key, session in self._sessions.items():
                if now - session.last_activity > timeout:
                    to_evict.append(session_key)

        for session_key in to_evict:
            await self.evict_session(session_key)

        return to_evict

    async def shutdown(self) -> None:
        """Gracefully shut down: stop intake → cancel timers → drain all sessions."""
        # 1. Stop accepting new messages
        self._accepting = False

        # 2. Cancel all idle timers (we're evicting everything now)
        self._cancel_all_idle_timers()

        # 3. Drain and close every active session
        async with self._lock:
            keys = list(self._sessions.keys())

        log.info("Shutting down %d active session(s)", len(keys))
        for session_key in keys:
            await self.evict_session(session_key)
