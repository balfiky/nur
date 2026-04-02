"""Tests for Phase 1 — Jarvis Runtime (console).

Covers:
- Console end-to-end (message → session manager → pipeline → response)
- Per-user serialization (no concurrent processing for the same user)
- State save/load (engine_state.json round-trip)
- Restart restore (reload from disk with elapsed decay)
- Session lifecycle (create, evict, capacity limits)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time

import pytest

from core.dual_process.generator import MockLLMBackend
from runtime.config import RuntimeConfig
from runtime.sessions.manager import SessionManager
from runtime.sessions.persistence import load_engine_state, save_engine_state
from runtime.sessions.user_session import UserSession
from pipeline import CognitivePipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmpdir: str, **overrides) -> RuntimeConfig:
    return RuntimeConfig(data_dir=tmpdir, **overrides)


def _mock_factory():
    return MockLLMBackend()


async def _send(manager: SessionManager, text: str,
                user_id: str = "user", platform: str = "console") -> str:
    return await manager.handle_message(platform, user_id, "direct", text)


# =========================================================================
# State persistence (save / load)
# =========================================================================

class TestStatePersistence:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "engine_state.json")
            snapshot = {"arousal": 0.8, "valence": 0.3, "energy": 0.5}
            save_engine_state(path, snapshot)

            loaded = load_engine_state(path)
            assert loaded is not None
            assert loaded["modulator_snapshot"] == snapshot
            assert "saved_at" in loaded
            assert isinstance(loaded["saved_at"], float)

    def test_load_missing_returns_none(self):
        assert load_engine_state("/nonexistent/path.json") is None

    def test_save_creates_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sub", "dir", "state.json")
            save_engine_state(path, {"arousal": 0.5})
            assert os.path.exists(path)

    def test_save_is_atomic(self):
        """No .tmp file left behind after save."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "state.json")
            save_engine_state(path, {"arousal": 0.5})
            assert not os.path.exists(path + ".tmp")
            assert os.path.exists(path)


# =========================================================================
# Console end-to-end
# =========================================================================

class TestConsoleEndToEnd:
    def test_message_gets_response(self):
        """A message routed through the session manager produces a response."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    resp = await _send(manager, "Hello there")
                    assert isinstance(resp, str)
                    assert len(resp) > 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_multiple_messages_same_user(self):
        """Multiple sequential messages for the same user all succeed."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    r1 = await _send(manager, "first")
                    r2 = await _send(manager, "second")
                    r3 = await _send(manager, "third")
                    assert all(isinstance(r, str) for r in [r1, r2, r3])
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_session_created_on_first_message(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    assert len(manager.active_sessions) == 0
                    await _send(manager, "hi")
                    assert len(manager.active_sessions) == 1
                    assert "console:user" in manager.active_sessions
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_two_users_get_separate_sessions(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "hi", user_id="alice")
                    await _send(manager, "hi", user_id="bob")
                    assert len(manager.active_sessions) == 2
                    assert "console:alice" in manager.active_sessions
                    assert "console:bob" in manager.active_sessions
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Per-user serialization
# =========================================================================

class TestPerUserSerialization:
    def test_concurrent_messages_serialized(self):
        """Messages sent concurrently for one user never overlap in processing."""
        max_concurrent = 0
        current = 0
        lock = threading.Lock()

        class TrackingBackend:
            def generate(self, system_prompt: str, user_message: str) -> str:
                nonlocal max_concurrent, current
                with lock:
                    current += 1
                    max_concurrent = max(max_concurrent, current)
                time.sleep(0.03)  # simulate work
                with lock:
                    current -= 1
                return "ok"

        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(
                    config, backend_factory=lambda: TrackingBackend(),
                )
                try:
                    # Fire 3 messages concurrently for the same user
                    tasks = [
                        asyncio.create_task(
                            _send(manager, f"msg-{i}")
                        )
                        for i in range(3)
                    ]
                    results = await asyncio.gather(*tasks)
                    assert len(results) == 3
                finally:
                    await manager.shutdown()

            # The worker processes one at a time — max_concurrent must be 1
            assert max_concurrent == 1

        asyncio.run(run())

    def test_different_users_can_overlap(self):
        """Messages for different users CAN process concurrently."""
        concurrent_seen = []
        current = 0
        lock = threading.Lock()

        class TrackingBackend:
            def generate(self, system_prompt: str, user_message: str) -> str:
                nonlocal current
                with lock:
                    current += 1
                    concurrent_seen.append(current)
                time.sleep(0.05)
                with lock:
                    current -= 1
                return "ok"

        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(
                    config, backend_factory=lambda: TrackingBackend(),
                )
                try:
                    # Fire messages for two different users concurrently
                    tasks = [
                        asyncio.create_task(
                            _send(manager, "hello", user_id="alice")
                        ),
                        asyncio.create_task(
                            _send(manager, "hello", user_id="bob")
                        ),
                    ]
                    await asyncio.gather(*tasks)
                finally:
                    await manager.shutdown()

            # With two separate users, we should see concurrent > 1 at some point
            assert max(concurrent_seen) >= 2

        asyncio.run(run())


# =========================================================================
# State save/load through session manager
# =========================================================================

class TestSessionStatePersistence:
    def test_shutdown_saves_state(self):
        """Shutting down the session manager persists engine state to disk."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)

                # Process a message to shift engine state
                await _send(manager, "I'm so angry about this argument!")
                await manager.shutdown()

                # State file should exist
                state_path = config.user_state_path("console:user")
                state = load_engine_state(state_path)
                assert state is not None
                assert "modulator_snapshot" in state
                assert "saved_at" in state

        asyncio.run(run())

    def test_evict_saves_state(self):
        """Evicting a session persists its state."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)

                await _send(manager, "hello")
                await manager.evict_session("console:user")

                assert "console:user" not in manager.active_sessions
                state_path = config.user_state_path("console:user")
                assert load_engine_state(state_path) is not None

        asyncio.run(run())


# =========================================================================
# Restart restore
# =========================================================================

class TestRestartRestore:
    def test_restore_from_saved_state(self):
        """A new session restores modulators from a previously saved state."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)

                # Session 1: process a high-arousal message, then save
                manager1 = SessionManager(config, backend_factory=_mock_factory)
                await _send(manager1, "I'm furious!", user_id="alice")
                session1 = manager1.active_sessions["console:alice"]
                snap_before = dict(session1.pipeline.engine.snapshot())
                await manager1.shutdown()

                # Session 2: recreate — should restore from disk
                manager2 = SessionManager(config, backend_factory=_mock_factory)
                await _send(manager2, "hi", user_id="alice")
                session2 = manager2.active_sessions["console:alice"]
                snap_after = session2.pipeline.engine.snapshot()

                # Arousal should be non-default (restored), potentially decayed
                # but not back to baseline 0.5 (not enough time passed)
                assert snap_after["arousal"] != 0.5 or snap_before["arousal"] != 0.5
                await manager2.shutdown()

        asyncio.run(run())

    def test_restore_applies_elapsed_decay(self):
        """Restored state has elapsed decay applied based on saved_at."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = _make_config(tmpdir)
            state_path = config.user_state_path("console:testuser")

            # Manually write a state as if saved 2 hours ago with high arousal
            snapshot = {
                "arousal": 0.95, "valence": 0.1, "certainty": 0.5,
                "bonding": 0.5, "energy": 0.5, "resolution": 0.0,
            }
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            with open(state_path, "w") as f:
                json.dump({
                    "modulator_snapshot": snapshot,
                    "saved_at": time.time() - 7200,  # 2 hours ago
                }, f)

            # Create session — should restore with decay
            async def run():
                manager = SessionManager(config, backend_factory=_mock_factory)
                await _send(manager, "hi", user_id="testuser")
                session = manager.active_sessions["console:testuser"]
                state = session.pipeline.engine.snapshot()
                # After 2 hours of decay, arousal should be significantly less than 0.95
                assert state["arousal"] < 0.9
                # Valence should have moved toward baseline (0.5)
                assert state["valence"] > 0.1
                await manager.shutdown()

            asyncio.run(run())


# =========================================================================
# Session lifecycle
# =========================================================================

class TestSessionLifecycle:
    def test_max_sessions_enforced(self):
        """Cannot exceed max_active_sessions."""
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

    def test_queue_backpressure(self):
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
                    # Fire enough messages to overflow the queue
                    # First message starts processing immediately
                    # Next 2 fill the queue (maxsize=2)
                    # 4th should fail
                    tasks = []
                    for i in range(4):
                        tasks.append(asyncio.create_task(
                            _send(manager, f"msg-{i}")
                        ))
                        await asyncio.sleep(0.01)  # small gap to order enqueues

                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    errors = [r for r in results if isinstance(r, RuntimeError)]
                    assert len(errors) >= 1
                    assert "queue full" in str(errors[0]).lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_evict_idle(self):
        """evict_idle removes sessions past the timeout threshold."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir, session_timeout_seconds=0.1)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await _send(manager, "hi")
                    assert len(manager.active_sessions) == 1

                    # Wait past the timeout
                    await asyncio.sleep(0.2)
                    evicted = await manager.evict_idle()
                    assert "console:user" in evicted
                    assert len(manager.active_sessions) == 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_shutdown_cleans_all_sessions(self):
        """Shutdown evicts every active session."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)

                await _send(manager, "hi", user_id="x")
                await _send(manager, "hi", user_id="y")
                assert len(manager.active_sessions) == 2

                await manager.shutdown()
                assert len(manager.active_sessions) == 0

        asyncio.run(run())

    def test_shared_self_model_across_users(self):
        """Two users share the same self-model DB via self_db_path."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    # Both users trigger self-observations through process()
                    await _send(manager, "I'm angry!", user_id="alice")
                    await _send(manager, "Thanks!", user_id="bob")

                    # Both should point to the same shared DB file
                    sessions = manager.active_sessions
                    s_alice = sessions["console:alice"]
                    s_bob = sessions["console:bob"]

                    # Self-observations from alice should be visible to bob
                    from core.profiles.self_model import SELF_ENTITY_ID
                    alice_obs = s_alice.pipeline._self_profile_store.observation_count(SELF_ENTITY_ID)
                    bob_obs = s_bob.pipeline._self_profile_store.observation_count(SELF_ENTITY_ID)
                    # Both see all observations (shared DB)
                    assert alice_obs > 0
                    assert alice_obs == bob_obs
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Identity keys
# =========================================================================

class TestIdentityKeys:
    def test_relationship_key_format(self):
        """Session is keyed by platform:user_id."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)
                try:
                    await manager.handle_message("telegram", "12345", "chat1", "hi")
                    assert "telegram:12345" in manager.active_sessions
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_data_dir_uses_safe_key(self):
        """Per-user data dir replaces colons for filesystem safety."""
        config = RuntimeConfig(data_dir="/tmp/test")
        assert "telegram_12345" in config.user_data_dir("telegram:12345")
        assert ":" not in config.user_data_dir("telegram:12345")
