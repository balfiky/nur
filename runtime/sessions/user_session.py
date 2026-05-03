"""Per-user session — owns a CognitivePipeline and serializes access."""

from __future__ import annotations

import asyncio
from concurrent.futures import Executor
from contextlib import suppress
from functools import partial
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, TypeVar

from pipeline import CognitivePipeline, DebugState
from runtime.learning_intake import PendingLearningIntake
from runtime.sessions.persistence import (
    delete_conversation_history,
    save_conversation_history,
    save_engine_state,
)

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class MessageEnvelope:
    """A message waiting to be processed, with a future for the result."""
    text: str
    user_id: str
    future: asyncio.Future


class _PendingQueueView:
    """Lightweight queue facade for debug/manager checks.

    The runtime still needs queue-like visibility (`qsize`, `empty`, `maxsize`)
    for idle eviction and debugging, but actual message execution is serialized
    directly via an async lock instead of a background worker.
    """

    def __init__(self, session: "UserSession", maxsize: int) -> None:
        self._session = session
        self.maxsize = maxsize

    def qsize(self) -> int:
        return self._session._queued_message_count

    def empty(self) -> bool:
        return self.qsize() == 0

    def full(self) -> bool:
        return self.qsize() >= self.maxsize


class UserSession:
    """Manages a single user's pipeline and serializes message processing.

    Messages are accepted with bounded backpressure and then processed one at a
    time via an async lock. The synchronous pipeline still runs in a worker
    thread, but without the extra background worker/future handoff layer.

    Parameters
    ----------
    session_key : str
        Full session identity (platform:user_id:chat_id).
    rel_key : str
        Relationship identity (platform:user_id) — used for storage paths.
    user_lock : asyncio.Lock | None
        Shared per-user lock that serializes DB access across sessions
        for the same user (different chat contexts).
    """

    def __init__(
        self,
        rel_key: str,
        user_id: str,
        pipeline: CognitivePipeline,
        state_path: str,
        history_path: str,
        max_queue: int = 3,
        *,
        executor: Executor | None = None,
        session_key: str | None = None,
        user_lock: asyncio.Lock | None = None,
    ) -> None:
        self.session_key = session_key or rel_key
        self.rel_key = rel_key
        self.user_id = user_id
        self.pipeline = pipeline
        self.state_path = state_path
        self.history_path = history_path
        self.last_activity: float = time.time()

        self._max_queue = max_queue
        self._queue = _PendingQueueView(self, maxsize=max_queue)
        self._worker_task: asyncio.Task | None = None
        self._stopped = False
        self._processing = False
        self._active_work_count = 0
        self._pending_message_count = 0
        self._active_message_count = 0
        self._send_lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._drained.set()
        self._executor = executor
        self._user_lock = user_lock
        self.last_debug: DebugState | None = None
        self.turn_index: int = 0
        self.pending_learning_intake: PendingLearningIntake | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Mark the session as active and ready to accept work."""
        self._stopped = False
        if self._pending_message_count == 0:
            self._drained.set()

    async def drain_and_close(self, *, end_conversation: bool = True) -> None:
        """Wait for accepted messages, then either end or suspend + close."""
        self._stopped = True

        await self._drained.wait()

        if end_conversation:
            await self._run_reliably(self._end_and_save_async(), name="session-close")
        else:
            await self._run_reliably(self._save_and_close_async(), name="session-suspend")

    def _end_and_save(self) -> None:
        """Synchronous: end session, persist state, close pipeline."""
        try:
            self.pipeline.end_session(user_id=self.user_id)
            delete_conversation_history(self.history_path)
        except Exception:
            log.exception("Error ending session for %s", self.session_key)
        try:
            state = self.pipeline.engine.export_state()
            save_engine_state(
                self.state_path,
                state["modulator_snapshot"],
                unresolved_items=state["unresolved_items"],
                extra_state=self._session_extra_state(),
            )
        except Exception:
            log.exception("Error saving state for %s", self.session_key)
        try:
            self.pipeline.close()
        except Exception:
            log.exception("Error closing pipeline for %s", self.session_key)

    def _save_and_close(self) -> None:
        """Synchronous: persist hot state/transcript and close without digestion."""
        try:
            self._save_hot_state()
        except Exception:
            log.exception("Error saving hot state for %s", self.session_key)
        try:
            self.pipeline.close()
        except Exception:
            log.exception("Error closing pipeline for %s", self.session_key)

    def _save_hot_state(self) -> None:
        state = self.pipeline.engine.export_state()
        save_engine_state(
            self.state_path,
            state["modulator_snapshot"],
            unresolved_items=state["unresolved_items"],
            extra_state=self._session_extra_state(),
        )
        save_conversation_history(
            self.history_path,
            self.pipeline.export_conversation_history(),
        )

    def save_state(self) -> None:
        """Save current engine state without ending the session."""
        state = self.pipeline.engine.export_state()
        save_engine_state(
            self.state_path,
            state["modulator_snapshot"],
            unresolved_items=state["unresolved_items"],
            extra_state=self._session_extra_state(),
        )

    def _session_extra_state(self) -> dict[str, object]:
        return {
            "turn_index": self.turn_index,
            "pending_learning_intake": (
                self.pending_learning_intake.to_dict()
                if self.pending_learning_intake is not None
                else None
            ),
        }

    def restore_session_extra_state(self, state: dict | None) -> None:
        if not isinstance(state, dict):
            return
        try:
            self.turn_index = int(state.get("turn_index", self.turn_index))
        except (TypeError, ValueError):
            pass
        self.pending_learning_intake = PendingLearningIntake.from_dict(
            state.get("pending_learning_intake")
        )

    @property
    def has_active_work(self) -> bool:
        """Whether any in-flight work is currently mutating this session."""
        return self._active_work_count > 0

    @property
    def _queued_message_count(self) -> int:
        return max(0, self._pending_message_count - self._active_message_count)

    def mark_work_started(self) -> None:
        """Mark a unit of session work as active."""
        self._active_work_count += 1
        self._processing = True

    def mark_work_finished(self) -> None:
        """Mark a unit of session work as finished."""
        self._active_work_count = max(0, self._active_work_count - 1)
        self._processing = self._active_work_count > 0

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def send(self, text: str) -> str:
        """Serialize one message through the pipeline with bounded backlog.

        `max_queue` still means "max queued behind the active turn", so total
        accepted concurrent sends is `max_queue + 1` including the in-flight one.
        """
        if self._stopped:
            raise RuntimeError(f"Session {self.session_key} is shutting down")

        if self._pending_message_count >= self._max_queue + 1:
            raise RuntimeError(
                f"Message queue full for {self.session_key} "
                f"(max {self._queue.maxsize})"
            )

        self._pending_message_count += 1
        self._drained.clear()
        self.last_activity = time.time()

        try:
            result = await self._run_reliably(
                self._process_message_async(text),
                name="session-send",
            )
            return result
        finally:
            self._pending_message_count = max(0, self._pending_message_count - 1)
            if self._pending_message_count == 0:
                self._drained.set()

    async def end_session(self) -> Any:
        """End the current conversational session without closing the pipeline."""
        return await self._run_reliably(
            self._end_session_async(),
            name="session-end",
        )

    async def record_runtime_turn(self) -> None:
        """Persist a non-generated runtime turn such as deterministic intake."""
        await self._run_reliably(
            self._record_runtime_turn_async(),
            name="session-runtime-turn",
        )

    async def apply_rest(self, hours: float) -> None:
        """Apply rest to the session pipeline under the same serialization lock."""
        await self._run_reliably(
            self._apply_rest_async(hours),
            name="session-rest",
        )

    async def _run_reliably(self, awaitable: Awaitable[T], *, name: str) -> T:
        """Await session work via a child task to avoid top-level await stalls."""
        task = asyncio.create_task(
            awaitable,
            name=f"{name}-{self.session_key}",
        )
        try:
            while not task.done():
                await asyncio.sleep(0.001)
        except asyncio.CancelledError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            raise
        return task.result()

    async def _run_blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run synchronous pipeline work on the runtime-owned executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(fn, *args, **kwargs),
        )

    async def _end_and_save_async(self) -> None:
        """Run end-of-session persistence under the per-user lock."""
        if self._user_lock is not None:
            async with self._user_lock:
                await self._run_blocking(self._end_and_save)
        else:
            await self._run_blocking(self._end_and_save)

    async def _save_and_close_async(self) -> None:
        """Run hot-session persistence under the per-user lock."""
        if self._user_lock is not None:
            async with self._user_lock:
                await self._run_blocking(self._save_and_close)
        else:
            await self._run_blocking(self._save_and_close)

    async def _record_runtime_turn_async(self) -> None:
        """Advance turn bookkeeping for work handled outside generation."""
        async with self._send_lock:
            self.mark_work_started()
            try:
                if self._user_lock is not None:
                    async with self._user_lock:
                        self.turn_index += 1
                        await self._run_blocking(self._save_hot_state)
                else:
                    self.turn_index += 1
                    await self._run_blocking(self._save_hot_state)
                self.last_activity = time.time()
            finally:
                self.mark_work_finished()

    async def _end_session_async(self) -> Any:
        """Worker-thread wrapper for session digestion without closing."""
        self.mark_work_started()
        try:
            if self._user_lock is not None:
                async with self._user_lock:
                    result = await self._run_blocking(
                        self.pipeline.end_session,
                        self.user_id,
                    )
            else:
                result = await self._run_blocking(
                    self.pipeline.end_session,
                    self.user_id,
                )
            delete_conversation_history(self.history_path)
            self.last_activity = time.time()
            return result
        finally:
            self.mark_work_finished()

    async def _apply_rest_async(self, hours: float) -> None:
        """Worker-thread wrapper for rest application."""
        self.mark_work_started()
        try:
            if self._user_lock is not None:
                async with self._user_lock:
                    await self._run_blocking(self.pipeline.apply_rest, hours)
            else:
                await self._run_blocking(self.pipeline.apply_rest, hours)
            self.last_activity = time.time()
        finally:
            self.mark_work_finished()

    async def _process_message_async(self, text: str) -> str:
        """Worker-thread wrapper for a single message turn."""
        async with self._send_lock:
            self._active_message_count += 1
            self.mark_work_started()
            try:
                if self._user_lock is not None:
                    async with self._user_lock:
                        result = await self._run_blocking(
                            self.pipeline.process, text, self.user_id,
                        )
                else:
                    result = await self._run_blocking(
                        self.pipeline.process, text, self.user_id,
                    )
                self.turn_index += 1
                await self._run_blocking(self._save_hot_state)
                self.last_debug = result.debug
                self.last_activity = time.time()
                return result.response
            finally:
                self._active_message_count = max(0, self._active_message_count - 1)
                self.mark_work_finished()
