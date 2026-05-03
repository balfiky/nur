"""Session manager — routes messages to per-user sessions."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from functools import partial
import logging
import os
import time
from typing import Any, Callable

from core.dual_process.generator import LLMBackend
from core.types import UnresolvedItem
from pipeline import CognitivePipeline
from runtime.config import RuntimeConfig
from runtime.learning_intake import (
    LearningIntakeError,
    PendingLearningIntake,
    ingest_learning_from_message,
)
from runtime.sessions.persistence import load_conversation_history, load_engine_state
from runtime.sessions.user_session import UserSession

log = logging.getLogger(__name__)


def _append_learning_note(response: str, note: str) -> str:
    """Attach a runtime learning receipt without hiding the normal reply."""
    if not note:
        return response
    response = (response or "").rstrip()
    if not response:
        return note
    return f"{response}\n\n{note}"


def _looks_like_pending_learning_text(text: str) -> bool:
    stripped = (text or "").strip()
    if len(stripped) < 200:
        return False
    lower = stripped.lower()
    if lower.startswith(("learn", "study", "digest", "internalize", "absorb")):
        return False
    words = stripped.split()
    return len(words) >= 30


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
        tool_executor_factory: Callable[[], Any] | None = None,
        proactive_callback: Callable[[str, str, str], Any] | None = None,
    ) -> None:
        self.config = config
        self._backend_factory = backend_factory
        self._tool_executor_factory = tool_executor_factory
        self._sessions: dict[str, UserSession] = {}
        self._idle_timers: dict[str, asyncio.TimerHandle] = {}
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._learning_lock = asyncio.Lock()
        self._lock = asyncio.Lock()
        self._accepting = True
        self._executor = ThreadPoolExecutor(
            max_workers=max(4, min(self.config.max_active_sessions + 1, 32)),
            thread_name_prefix="nur-runtime",
        )
        self._executor_shutdown = False
        # Phase 8: proactive callback — async (session_key, user_id, message) -> None
        self._proactive_callback = proactive_callback

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
            # Running the turn in its own task avoids a rare top-level await
            # stall around worker-thread execution in some runtime contexts.
            send_task = asyncio.create_task(
                session.send(text),
                name=f"session-send-{session_key}",
            )
            try:
                while not send_task.done():
                    await asyncio.sleep(0.001)
            except asyncio.CancelledError:
                send_task.cancel()
                with suppress(asyncio.CancelledError):
                    await send_task
                raise
            response = send_task.result()
            learning_note = await self._maybe_run_learning_intake(session, user_id, text)
            if learning_note:
                session.pipeline.last_intake_receipt = learning_note
                return _append_learning_note(response, learning_note)
            return response
        finally:
            # Reset again on completion so the timeout counts from last activity
            self._reset_idle_timer(session_key)

    async def ensure_session(
        self,
        platform: str,
        user_id: str,
        chat_id: str,
    ) -> UserSession:
        """Return an existing session or create a clean one without sending a turn."""
        rel_key = f"{platform}:{user_id}"
        session_key = f"{platform}:{user_id}:{chat_id}"
        session = await self._get_or_create(session_key, rel_key, user_id)
        self._reset_idle_timer(session_key)
        return session

    async def _run_blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run synchronous runtime work on the manager-owned executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(fn, *args, **kwargs),
        )

    async def _maybe_run_learning_intake(
        self,
        session: UserSession,
        user_id: str,
        text: str,
    ) -> str:
        """Persist explicit "learn from this" requests into Life History."""
        pending_inline = False
        if session.pending_learning_intake is not None:
            pending = session.pending_learning_intake
            if pending.expired(session.turn_index):
                session.pending_learning_intake = None
            elif _looks_like_pending_learning_text(text):
                pending_inline = True
            else:
                session.pending_learning_intake = None
        try:
            async with self._learning_lock:
                kwargs: dict[str, Any] = {"actor": user_id}
                if pending_inline:
                    kwargs["pending_inline_text"] = True
                result = await self._run_blocking(
                    ingest_learning_from_message,
                    self.config,
                    text,
                    **kwargs,
                )
        except LearningIntakeError as exc:
            if "URL or a longer pasted text" in str(exc):
                session.pending_learning_intake = PendingLearningIntake(
                    created_turn_index=session.turn_index,
                    expires_after_turns=max(1, int(self.config.pending_intake_ttl_turns)),
                )
            return f"Learning intake failed: {exc}"
        except Exception:
            log.exception("Learning intake failed")
            return "Learning intake failed: unexpected runtime error."
        if result is None:
            return ""
        session.pending_learning_intake = None
        return result.confirmation_text()

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

    @staticmethod
    def _session_has_active_work(session: UserSession) -> bool:
        """Return whether a session currently has in-flight work.

        Prefer the explicit active-work counter added on UserSession.
        Fall back to ``_processing`` for tests that use light mocks.
        """
        descriptor = getattr(type(session), "has_active_work", None)
        if isinstance(descriptor, property):
            return bool(session.has_active_work)
        return bool(getattr(session, "_processing", False))

    @staticmethod
    def _mark_session_work_started(session: UserSession) -> None:
        """Mark a unit of session work as active.

        Uses the real UserSession helpers when available, but still works
        with mock sessions used in focused unit tests.
        """
        marker = getattr(type(session), "mark_work_started", None)
        if callable(marker):
            session.mark_work_started()
        else:
            session._processing = True

    @staticmethod
    def _mark_session_work_finished(session: UserSession) -> None:
        """Mark a unit of session work as finished."""
        marker = getattr(type(session), "mark_work_finished", None)
        if callable(marker):
            session.mark_work_finished()
        else:
            session._processing = False

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
        if not session._queue.empty() or self._session_has_active_work(session):
            log.debug(
                "Timeout for %s but work active — rescheduling", session_key,
            )
            self._start_idle_timer(session_key)
            return
        log.info("Inactivity timeout for %s", session_key)
        await self.evict_session(session_key, end_conversation=False)

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
        tool_executor = (
            self._tool_executor_factory()
            if self._tool_executor_factory is not None
            else None
        )

        pipeline = CognitivePipeline(
            llm_backend=backend,
            llm_backend_fast=backend,
            db_path=self.config.user_db_path(rel_key),
            self_db_path=self.config.shared_db_path,
            tool_executor=tool_executor,
            autonomy_level=self.config.autonomy_level,
            coherence_min_score=self.config.coherence_min_score,
            coherence_max_regenerations=self.config.coherence_max_regenerations,
            life_history_provider=self._life_history_context_provider,
            life_history_snapshot_provider=self._life_history_snapshot_provider,
            skill_provider=self._skill_context_provider,
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
                unresolved_items=[
                    UnresolvedItem.from_dict(item)
                    for item in saved.get("unresolved_items", [])
                ],
            )
            log.info("Restored state for %s (saved_at=%.0f)", session_key,
                     saved.get("saved_at", 0))

        history_path = self.config.session_history_path(session_key)
        history = load_conversation_history(history_path)
        if history:
            pipeline.restore_conversation_history(history)
            log.info("Restored %d hot transcript item(s) for %s", len(history), session_key)

        session = UserSession(
            rel_key=rel_key,
            user_id=user_id,
            pipeline=pipeline,
            state_path=state_path,
            history_path=history_path,
            max_queue=self.config.max_queue_per_user,
            executor=self._executor,
            session_key=session_key,
            user_lock=self._get_or_create_user_lock(rel_key),
        )
        if saved is not None:
            session.restore_session_extra_state(saved)
        return session

    def _life_history_context_provider(self, query_text: str = "") -> dict[str, Any]:
        """Load shared identity-level life context for prompt generation."""
        from runtime.life_history import LifeHistoryStore, life_history_db_path

        if not os.path.exists(life_history_db_path(self.config)):
            return {}
        try:
            with LifeHistoryStore(self.config) as store:
                context = store.prompt_context()
                query = str(query_text or "").strip()
                if not query:
                    return context
                relevant = store.retrieve_relevant(query, limit=5)
                context["beliefs"] = relevant.get("beliefs", context.get("beliefs", []))
                context["recent_evolution"] = relevant.get(
                    "recent_evolution",
                    context.get("recent_evolution", []),
                )
                context["self_traits"] = relevant.get("self_traits", [])
                context["relevance_query"] = query
                return context
        except Exception:
            log.exception("Failed to load life history context")
            return {}

    def _life_history_snapshot_provider(self) -> dict[str, Any]:
        """Load unabridged identity-level state for deterministic identity replies."""
        from runtime.life_history import LifeHistoryStore, life_history_db_path

        if not os.path.exists(life_history_db_path(self.config)):
            return {}
        try:
            with LifeHistoryStore(self.config) as store:
                return {
                    "evolution_events": store.list_evolution(limit=10),
                    "beliefs": store.list_beliefs(limit=10),
                    "drives": store.list_drives(),
                    "counts": store.overview().get("counts", {}),
                }
        except Exception:
            log.exception("Failed to load life history snapshot")
            return {}

    def _skill_context_provider(self) -> dict[str, Any]:
        """Load enabled imported skill guidance for prompt generation."""
        from runtime.skills import enabled_skill_context

        try:
            return enabled_skill_context(self.config)
        except Exception:
            log.exception("Failed to load enabled skill context")
            return {}

    async def evict_session(self, session_key: str, *, end_conversation: bool = True) -> None:
        """Evict a session: cancel timer, drain, save state, close pipeline."""
        self._cancel_idle_timer(session_key)
        async with self._lock:
            session = self._sessions.pop(session_key, None)
        if session is None:
            return
        log.info("Evicting session: %s", session_key)
        await session.drain_and_close(end_conversation=end_conversation)

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
            await self.evict_session(session_key, end_conversation=False)

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
            await self.evict_session(session_key, end_conversation=False)
        if not self._executor_shutdown:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor_shutdown = True

    # ------------------------------------------------------------------
    # Proactive behavior loop (Phase 8)
    # ------------------------------------------------------------------

    async def run_proactive_loop(self) -> None:
        """Periodically evaluate proactive triggers for all idle sessions.

        Bounded: runs at config.proactive_check_interval, respects per-session
        limits (density_reference, recovery_seconds, idle_threshold). Does not bypass the
        cognitive pipeline — responses are generated through Nūr.
        """
        interval = self.config.proactive_check_interval
        while self._accepting:
            await asyncio.sleep(interval)
            if not self._accepting:
                break
            await self._proactive_sweep()

    async def _proactive_sweep(self) -> None:
        """Check all active sessions for proactive opportunities."""
        async with self._lock:
            snapshot = list(self._sessions.items())

        for session_key, session in snapshot:
            if self._session_has_active_work(session):
                continue
            if not session._queue.empty():
                continue
            idle = time.time() - session.last_activity
            if idle < self.config.proactive_idle_threshold:
                continue
            try:
                await self._run_proactive(session_key, session)
            except Exception:
                log.exception("Proactive check failed for %s", session_key)

    async def _run_proactive(
        self, session_key: str, session: UserSession,
    ) -> None:
        """Evaluate and optionally act on proactive triggers for a session.

        Acquires the per-user lock so proactive execution never overlaps
        with normal message processing or another chat context for the
        same user — same serialization guarantee as UserSession._worker.
        """
        pipeline = session.pipeline
        user_lock = session._user_lock

        self._mark_session_work_started(session)
        try:
            # Acquire per-user lock — same as _worker does for normal turns
            if user_lock is not None:
                async with user_lock:
                    result = await self._run_blocking(
                        pipeline.process_proactive,
                        session.user_id,
                        density_reference=self.config.proactive_density_reference,
                        idle_threshold=self.config.proactive_idle_threshold,
                        recovery_seconds=self.config.proactive_recovery_seconds,
                    )
            else:
                result = await self._run_blocking(
                    pipeline.process_proactive,
                    session.user_id,
                    density_reference=self.config.proactive_density_reference,
                    idle_threshold=self.config.proactive_idle_threshold,
                    recovery_seconds=self.config.proactive_recovery_seconds,
                )
            if result is None:
                return

            # Store debug state
            session.last_debug = result.debug
            session.last_activity = time.time()
            self._reset_idle_timer(session_key)

            log.info("Proactive message for %s: %s", session_key, result.response[:80])

            # Deliver via callback
            if self._proactive_callback is not None:
                try:
                    cb_result = self._proactive_callback(
                        session_key, session.user_id, result.response,
                    )
                    if asyncio.iscoroutine(cb_result):
                        await cb_result
                except Exception:
                    log.exception("Proactive callback failed for %s", session_key)
        finally:
            self._mark_session_work_finished(session)
