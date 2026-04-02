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
    """

    def __init__(
        self,
        rel_key: str,
        user_id: str,
        pipeline: CognitivePipeline,
        state_path: str,
        max_queue: int = 3,
    ) -> None:
        self.rel_key = rel_key
        self.user_id = user_id
        self.pipeline = pipeline
        self.state_path = state_path
        self.last_activity: float = time.time()

        self._queue: asyncio.Queue[MessageEnvelope] = asyncio.Queue(maxsize=max_queue)
        self._worker_task: asyncio.Task | None = None
        self._stopped = False
        self.last_debug: DebugState | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background worker that drains the message queue."""
        self._stopped = False
        self._worker_task = asyncio.get_running_loop().create_task(
            self._worker(), name=f"session-worker-{self.rel_key}",
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

        # End session + save state in worker thread
        await asyncio.to_thread(self._end_and_save)

    def _end_and_save(self) -> None:
        """Synchronous: end session, persist state, close pipeline."""
        try:
            self.pipeline.end_session(user_id=self.user_id)
        except Exception:
            log.exception("Error ending session for %s", self.rel_key)
        try:
            save_engine_state(self.state_path, self.pipeline.engine.snapshot())
        except Exception:
            log.exception("Error saving state for %s", self.rel_key)
        try:
            self.pipeline.close()
        except Exception:
            log.exception("Error closing pipeline for %s", self.rel_key)

    def save_state(self) -> None:
        """Save current engine state without ending the session."""
        save_engine_state(self.state_path, self.pipeline.engine.snapshot())

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def send(self, text: str) -> str:
        """Enqueue a message and wait for the response.

        Raises RuntimeError if the queue is full (backpressure).
        """
        if self._stopped:
            raise RuntimeError(f"Session {self.rel_key} is shutting down")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        envelope = MessageEnvelope(text=text, user_id=self.user_id, future=future)

        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            raise RuntimeError(
                f"Message queue full for {self.rel_key} "
                f"(max {self._queue.maxsize})"
            )

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

            try:
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
                self._queue.task_done()
                self.last_activity = time.time()
