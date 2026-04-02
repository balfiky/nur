"""Per-user session — owns a CognitivePipeline and serializes access."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from pipeline import CognitivePipeline, DebugState
from runtime.sessions.persistence import load_engine_state, save_engine_state

log = logging.getLogger(__name__)


@dataclass
class MessageEnvelope:
    """A message waiting to be processed, with a future for the result."""
    text: str
    user_id: str
    future: asyncio.Future


class UserSession:
    """Manages a single user's pipeline and serializes message processing.

    Messages are enqueued and processed one at a time by a background worker
    that runs the synchronous pipeline in a worker thread via asyncio.to_thread.

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
        max_queue: int = 3,
        *,
        session_key: str | None = None,
        user_lock: asyncio.Lock | None = None,
    ) -> None:
        self.session_key = session_key or rel_key
        self.rel_key = rel_key
        self.user_id = user_id
        self.pipeline = pipeline
        self.state_path = state_path
        self.last_activity: float = time.time()

        self._queue: asyncio.Queue[MessageEnvelope] = asyncio.Queue(maxsize=max_queue)
        self._worker_task: asyncio.Task | None = None
        self._stopped = False
        self._processing = False
        self._active_work_count = 0
        self._user_lock = user_lock
        self.last_debug: DebugState | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background worker that drains the message queue."""
        self._stopped = False
        self._worker_task = asyncio.get_running_loop().create_task(
            self._worker(), name=f"session-worker-{self.session_key}",
        )

    async def drain_and_close(self) -> None:
        """Drain remaining messages, end session, save state, close pipeline."""
        self._stopped = True

        # Let the queue drain naturally (worker finishes current + pending)
        if self._worker_task is not None and not self._worker_task.done():
            # Signal the worker to stop after draining
            await self._queue.join()
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        # End session + save state in worker thread (with user lock if present)
        if self._user_lock is not None:
            async with self._user_lock:
                await asyncio.to_thread(self._end_and_save)
        else:
            await asyncio.to_thread(self._end_and_save)

    def _end_and_save(self) -> None:
        """Synchronous: end session, persist state, close pipeline."""
        try:
            self.pipeline.end_session(user_id=self.user_id)
        except Exception:
            log.exception("Error ending session for %s", self.session_key)
        try:
            save_engine_state(self.state_path, self.pipeline.engine.snapshot())
        except Exception:
            log.exception("Error saving state for %s", self.session_key)
        try:
            self.pipeline.close()
        except Exception:
            log.exception("Error closing pipeline for %s", self.session_key)

    def save_state(self) -> None:
        """Save current engine state without ending the session."""
        save_engine_state(self.state_path, self.pipeline.engine.snapshot())

    @property
    def has_active_work(self) -> bool:
        """Whether any in-flight work is currently mutating this session."""
        return self._active_work_count > 0

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
        """Enqueue a message and wait for the response.

        Raises RuntimeError if the queue is full (backpressure).
        Updates last_activity on acceptance (enqueue), not just on completion.
        """
        if self._stopped:
            raise RuntimeError(f"Session {self.session_key} is shutting down")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        envelope = MessageEnvelope(text=text, user_id=self.user_id, future=future)

        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            raise RuntimeError(
                f"Message queue full for {self.session_key} "
                f"(max {self._queue.maxsize})"
            )

        # Mark acceptance time — keeps session alive during long processing
        self.last_activity = time.time()

        return await future

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        """Process messages one at a time from the queue."""
        while True:
            try:
                envelope = await self._queue.get()
            except asyncio.CancelledError:
                break

            self.mark_work_started()
            try:
                if self._user_lock is not None:
                    async with self._user_lock:
                        result = await asyncio.to_thread(
                            self.pipeline.process, envelope.text, envelope.user_id,
                        )
                else:
                    result = await asyncio.to_thread(
                        self.pipeline.process, envelope.text, envelope.user_id,
                    )
                self.last_debug = result.debug
                if not envelope.future.cancelled():
                    envelope.future.set_result(result.response)
            except asyncio.CancelledError:
                if not envelope.future.done():
                    envelope.future.cancel()
                break
            except Exception as exc:
                if not envelope.future.done():
                    envelope.future.set_exception(exc)
            finally:
                self.mark_work_finished()
                self._queue.task_done()
                self.last_activity = time.time()
