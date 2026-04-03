"""Tests for Phase 3 — Timeouts, shutdown, backpressure, shared DB safety.

Covers:
- Timer-driven inactivity timeout (auto-eviction without waiting for next message)
- Timer resets on activity (session stays alive while active)
- Graceful shutdown persistence (all sessions persisted on shutdown)
- Shutdown stops intake (new messages rejected)
- Queue-full backpressure (user gets busy response)
- Active-session limit (new users rejected at capacity)
- Shared DB WAL mode verification
- Shared DB concurrent writes from multiple user sessions
- Unresolved items are in-memory only (not persisted)
- Timeout semantics: in-flight, queued, and true inactivity
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import time

import pytest

from core.dual_process.generator import MockLLMBackend
from core.profiles.base import ProfileStore
from runtime.config import RuntimeConfig
from runtime.sessions.manager import SessionManager
from runtime.sessions.persistence import load_engine_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmpdir: str, **overrides) -> RuntimeConfig:
    return RuntimeConfig(data_dir=tmpdir, **overrides)


def _mock_factory():
    return MockLLMBackend()


async def _send(manager: SessionManager, text: str,
                user_id: str = "user", platform: str = "console",
                chat_id: str = "direct") -> str:
    return await manager.handle_message(platform, user_id, chat_id, text)


# =========================================================================
# Timer-driven inactivity timeout
# =========================================================================

class TestInactivityTimeout:
    def test_timeout_evicts_automatically(self):
        """Session is evicted by timer without another message arriving."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, session_timeout_seconds=0.15)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "hello")
                    assert len(manager.active_sessions) == 1

                    # Wait for the timer to fire
                    await asyncio.sleep(0.3)
                    # Give the eviction coroutine time to complete
                    await asyncio.sleep(0.05)

                    assert len(manager.active_sessions) == 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_timeout_persists_state(self):
        """Timer-driven eviction saves engine state to disk."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, session_timeout_seconds=0.15)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "I am so frustrated!")
                    state_path = config.session_state_path("console:user:direct")

                    await asyncio.sleep(0.3)
                    await asyncio.sleep(0.05)

                    # State file should exist after timeout eviction
                    state = load_engine_state(state_path)
                    assert state is not None
                    assert "modulator_snapshot" in state
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_timer_resets_on_activity(self):
        """New messages reset the idle timer — session stays alive."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, session_timeout_seconds=0.2)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "first")
                    # Wait 60% of timeout
                    await asyncio.sleep(0.12)
                    assert len(manager.active_sessions) == 1

                    # Send another message — resets the timer
                    await _send(manager, "second")
                    # Wait another 60% of timeout (total >100% of original)
                    await asyncio.sleep(0.12)

                    # Session should still be alive (timer was reset)
                    assert len(manager.active_sessions) == 1
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_multiple_sessions_independent_timers(self):
        """Each session has its own independent idle timer."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, session_timeout_seconds=0.6)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "hi", user_id="alice")
                    await asyncio.sleep(0.1)
                    await _send(manager, "hi", user_id="bob")

                    assert len(manager.active_sessions) == 2

                    # Wait for alice's timer but leave enough slack so bob's
                    # later timer should still be active despite request latency.
                    await asyncio.sleep(0.45)
                    await asyncio.sleep(0.05)

                    assert "console:alice:direct" not in manager.active_sessions
                    assert "console:bob:direct" in manager.active_sessions
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Graceful shutdown
# =========================================================================

class TestGracefulShutdown:
    def test_shutdown_persists_all_sessions(self):
        """Shutdown saves engine state for every active session."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)

                await _send(manager, "hi", user_id="alice")
                await _send(manager, "hi", user_id="bob")
                assert len(manager.active_sessions) == 2

                await manager.shutdown()

                # Both state files should exist
                for uid in ("alice", "bob"):
                    path = config.session_state_path(f"console:{uid}:direct")
                    state = load_engine_state(path)
                    assert state is not None, f"Missing state for {uid}"
                    assert "modulator_snapshot" in state

        asyncio.run(run())

    def test_shutdown_stops_intake(self):
        """After shutdown starts, new messages are rejected."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)

                await _send(manager, "hi")
                await manager.shutdown()

                with pytest.raises(RuntimeError, match="shutting down"):
                    await _send(manager, "hi", user_id="late_user")

        asyncio.run(run())

    def test_shutdown_evicts_all_sessions(self):
        """Shutdown leaves zero active sessions."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)

                await _send(manager, "hi", user_id="x")
                await _send(manager, "hi", user_id="y")
                await _send(manager, "hi", user_id="z")
                assert len(manager.active_sessions) == 3

                await manager.shutdown()
                assert len(manager.active_sessions) == 0

        asyncio.run(run())

    def test_shutdown_cancels_idle_timers(self):
        """Shutdown cancels all pending idle timers."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, session_timeout_seconds=100)
                manager = SessionManager(config, backend_factory=_mock_factory)

                await _send(manager, "hi", user_id="a")
                await _send(manager, "hi", user_id="b")
                assert len(manager._idle_timers) == 2

                await manager.shutdown()
                assert len(manager._idle_timers) == 0

        asyncio.run(run())

    def test_shutdown_drains_in_progress_work(self):
        """Shutdown waits for in-progress pipeline work to finish."""
        processing_completed = False

        class SlowBackend:
            def generate(self, system_prompt: str, user_message: str) -> str:
                nonlocal processing_completed
                time.sleep(0.1)
                processing_completed = True
                return "done"

        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(
                    config, backend_factory=lambda: SlowBackend(),
                )

                # Start a message that takes 100ms to process
                task = asyncio.create_task(_send(manager, "slow"))
                await asyncio.sleep(0.02)  # let it start processing

                # Shutdown while processing — should wait for it
                await manager.shutdown()
                assert processing_completed

        asyncio.run(run())


# =========================================================================
# Backpressure
# =========================================================================

class TestBackpressure:
    def test_queue_full_rejects_message(self):
        """Exceeding max_queue_per_user raises RuntimeError."""

        class SlowBackend:
            def generate(self, system_prompt: str, user_message: str) -> str:
                time.sleep(0.2)
                return "ok"

        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, max_queue_per_user=2)
                manager = SessionManager(
                    config, backend_factory=lambda: SlowBackend(),
                )
                try:
                    tasks = []
                    for i in range(4):
                        tasks.append(asyncio.create_task(
                            _send(manager, f"msg-{i}")
                        ))
                        await asyncio.sleep(0.01)

                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    errors = [r for r in results if isinstance(r, RuntimeError)]
                    assert len(errors) >= 1
                    assert "queue full" in str(errors[0]).lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_active_session_limit_rejects_new_user(self):
        """New user is rejected when max_active_sessions is reached."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, max_active_sessions=2)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "hi", user_id="a")
                    await _send(manager, "hi", user_id="b")
                    with pytest.raises(RuntimeError, match="limit reached"):
                        await _send(manager, "hi", user_id="c")
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_eviction_frees_session_slot(self):
        """After evicting a session, a new user can connect."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, max_active_sessions=2)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "hi", user_id="a")
                    await _send(manager, "hi", user_id="b")

                    # At capacity — c should fail
                    with pytest.raises(RuntimeError, match="limit reached"):
                        await _send(manager, "hi", user_id="c")

                    # Evict a → slot opens
                    await manager.evict_session("console:a:direct")
                    resp = await _send(manager, "hi", user_id="c")
                    assert isinstance(resp, str)
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_existing_user_not_blocked_by_session_limit(self):
        """A user who already has a session can still send when at capacity."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, max_active_sessions=2)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "hi", user_id="a")
                    await _send(manager, "hi", user_id="b")

                    # a already has a session — should work
                    resp = await _send(manager, "another message", user_id="a")
                    assert isinstance(resp, str)
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Shared DB WAL mode + busy timeout
# =========================================================================

class TestSharedDBSafety:
    def test_shared_profile_store_uses_wal(self):
        """ProfileStore with wal_mode=True enables WAL journal mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = ProfileStore(db_path=db_path, wal_mode=True)
            try:
                mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
                assert mode == "wal"
            finally:
                store.close()

    def test_shared_profile_store_has_busy_timeout(self):
        """ProfileStore with wal_mode=True sets busy_timeout."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = ProfileStore(db_path=db_path, wal_mode=True)
            try:
                timeout = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
                assert timeout == 5000
            finally:
                store.close()

    def test_regular_profile_store_no_wal(self):
        """ProfileStore without wal_mode stays on default journal mode."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = ProfileStore(db_path=db_path, wal_mode=False)
            try:
                mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
                assert mode != "wal"
            finally:
                store.close()

    def test_memory_db_ignores_wal(self):
        """In-memory DB does not attempt WAL mode."""
        store = ProfileStore(db_path=":memory:", wal_mode=True)
        try:
            mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "memory"
        finally:
            store.close()

    def test_pipeline_shared_db_gets_wal(self):
        """When pipeline has self_db_path, the shared store uses WAL."""
        from pipeline import CognitivePipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            user_db = os.path.join(tmpdir, "user.db")
            shared_db = os.path.join(tmpdir, "shared.db")
            pipeline = CognitivePipeline(
                db_path=user_db,
                self_db_path=shared_db,
            )
            try:
                mode = pipeline._self_profile_store._conn.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0]
                assert mode == "wal"

                # Per-user store should NOT use WAL
                user_mode = pipeline._person_profile_store._conn.execute(
                    "PRAGMA journal_mode"
                ).fetchone()[0]
                assert user_mode != "wal"
            finally:
                pipeline.close()

    def test_concurrent_shared_db_writes(self):
        """Multiple sessions writing to the shared self-model DB concurrently."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    # Fire messages for multiple users concurrently
                    tasks = [
                        asyncio.create_task(
                            _send(manager, "I am happy!", user_id=f"user{i}")
                        )
                        for i in range(5)
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # All should succeed — no database locked errors
                    errors = [r for r in results if isinstance(r, Exception)]
                    assert len(errors) == 0, f"Errors: {errors}"
                    assert len(manager.active_sessions) == 5
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Unresolved items — in-memory only
# =========================================================================

class TestUnresolvedItemsPersistence:
    def test_unresolved_items_are_persisted_in_saved_state(self):
        """Engine state JSON preserves unresolved items across evictions."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, session_timeout_seconds=0.1)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(
                        manager,
                        "You betrayed and deceived me completely!",
                    )
                    state_path = config.session_state_path("console:user:direct")

                    # Wait for timeout eviction
                    await asyncio.sleep(0.2)
                    await asyncio.sleep(0.05)

                    state = load_engine_state(state_path)
                    assert state is not None
                    assert "unresolved_items" in state
                    assert len(state["unresolved_items"]) > 0
                    assert {"modulator_snapshot", "saved_at", "unresolved_items"} <= set(state.keys())
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Timeout semantics: in-flight, queued, true inactivity (regression)
# =========================================================================

class TestTimeoutSemantics:
    def test_inflight_processing_prevents_eviction(self):
        """A session with in-flight pipeline work must not be evicted by timeout."""
        evicted_during_processing = False

        class SlowBackend:
            def generate(self, system_prompt: str, user_message: str) -> str:
                time.sleep(0.3)
                return "done"

        async def run():
            nonlocal evicted_during_processing
            with tempfile.TemporaryDirectory() as tmpdir:
                # Timeout shorter than processing time
                config = _make_config(tmpdir, session_timeout_seconds=0.1)
                manager = SessionManager(
                    config, backend_factory=lambda: SlowBackend(),
                )
                try:
                    # Start slow processing — takes 300ms, timeout at 100ms
                    task = asyncio.create_task(_send(manager, "slow"))

                    # Wait enough for timeout to fire (but processing is in-flight)
                    await asyncio.sleep(0.15)

                    # Session should still exist (in-flight guard)
                    if len(manager.active_sessions) == 0:
                        evicted_during_processing = True

                    # Let processing complete
                    result = await task
                    assert isinstance(result, str)
                finally:
                    await manager.shutdown()

            assert not evicted_during_processing

        asyncio.run(run())

    def test_queued_messages_prevent_eviction(self):
        """A session with queued messages must not be evicted by timeout."""

        class SlowBackend:
            def generate(self, system_prompt: str, user_message: str) -> str:
                time.sleep(0.15)
                return "ok"

        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, session_timeout_seconds=0.1)
                manager = SessionManager(
                    config, backend_factory=lambda: SlowBackend(),
                )
                try:
                    # Fire two messages — first processes, second queues
                    t1 = asyncio.create_task(_send(manager, "first"))
                    await asyncio.sleep(0.02)
                    t2 = asyncio.create_task(_send(manager, "second"))

                    # Wait for timeout to fire while queue has messages
                    await asyncio.sleep(0.12)
                    assert len(manager.active_sessions) == 1  # not evicted

                    # Let both finish
                    await asyncio.gather(t1, t2)
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_timeout_fires_after_true_inactivity_only(self):
        """Timeout evicts only after genuine inactivity (no processing, no queue)."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, session_timeout_seconds=0.15)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "hello")
                    assert len(manager.active_sessions) == 1

                    # Send another message at 60% of timeout
                    await asyncio.sleep(0.09)
                    await _send(manager, "still here")
                    assert len(manager.active_sessions) == 1

                    # Wait full timeout from last message
                    await asyncio.sleep(0.2)
                    await asyncio.sleep(0.05)

                    # Now it should be evicted
                    assert len(manager.active_sessions) == 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_timer_resets_on_acceptance_not_just_completion(self):
        """Timer resets when a message is accepted (enqueued), not only on completion."""

        class MediumBackend:
            def generate(self, system_prompt: str, user_message: str) -> str:
                time.sleep(0.1)
                return "ok"

        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                # Timeout = 0.15s, processing = 0.1s
                config = _make_config(tmpdir, session_timeout_seconds=0.15)
                manager = SessionManager(
                    config, backend_factory=lambda: MediumBackend(),
                )
                try:
                    # First message: resets timer on acceptance
                    result = await _send(manager, "first")
                    assert isinstance(result, str)

                    # Send another just before old timeout would fire
                    await asyncio.sleep(0.12)
                    # This acceptance should reset the timer
                    task = asyncio.create_task(_send(manager, "second"))
                    await asyncio.sleep(0.01)  # let enqueue happen

                    # Session must still be alive
                    assert len(manager.active_sessions) == 1

                    await task
                finally:
                    await manager.shutdown()

        asyncio.run(run())
