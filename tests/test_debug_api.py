"""Tests for Phase 4 — Runtime-aware debug API.

Covers:
- Session listing (GET /sessions)
- Per-session debug retrieval (GET /sessions/{session_key}/debug)
- Per-session reset (POST /sessions/{session_key}/reset)
- Session isolation in debug output
- Debug state populated after message processing
- 404 for non-existent sessions
"""

from __future__ import annotations

import asyncio
import tempfile

import httpx
import pytest
from fastapi.testclient import TestClient

from core.dual_process.generator import MockLLMBackend
from runtime.config import RuntimeConfig
from runtime.debug.api import create_debug_app
from runtime.sessions.manager import SessionManager


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


def _setup(tmpdir: str, **config_overrides):
    """Create a SessionManager + TestClient pair."""
    config = _make_config(tmpdir, **config_overrides)
    manager = SessionManager(config, backend_factory=_mock_factory)
    app = create_debug_app(manager)
    client = TestClient(app)
    return manager, client, app


async def _async_client(app):
    """Create an httpx AsyncClient on the current event loop."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


# =========================================================================
# GET /sessions — session listing
# =========================================================================

class TestSessionListing:
    def test_empty_sessions(self):
        """No active sessions returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, client, _ = _setup(tmpdir)
            resp = client.get("/sessions")
            assert resp.status_code == 200
            assert resp.json() == []

    def test_lists_active_sessions(self):
        """After messages, sessions appear in listing."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, client, _ = _setup(tmpdir)
                try:
                    await _send(manager, "hi", user_id="alice")
                    await _send(manager, "hi", user_id="bob")

                    resp = client.get("/sessions")
                    assert resp.status_code == 200
                    sessions = resp.json()
                    assert len(sessions) == 2

                    keys = {s["session_key"] for s in sessions}
                    assert "console:alice:direct" in keys
                    assert "console:bob:direct" in keys
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_session_fields(self):
        """Each session entry has the expected fields."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, client, _ = _setup(tmpdir)
                try:
                    await _send(manager, "hello", user_id="alice")

                    resp = client.get("/sessions")
                    session = resp.json()[0]

                    assert session["session_key"] == "console:alice:direct"
                    assert session["rel_key"] == "console:alice"
                    assert session["user_id"] == "alice"
                    assert isinstance(session["last_activity"], float)
                    assert isinstance(session["idle_seconds"], float)
                    assert isinstance(session["queue_size"], int)
                    assert session["has_debug"] is True
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_has_debug_true_after_message(self):
        """has_debug is True after a message is processed."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, client, _ = _setup(tmpdir)
                try:
                    await _send(manager, "hi")
                    sessions = client.get("/sessions").json()
                    assert sessions[0]["has_debug"] is True
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# GET /sessions/{session_key}/debug — per-session debug view
# =========================================================================

class TestPerUserDebug:
    def test_debug_after_message(self):
        """Debug view returns live state after processing a message."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, client, _ = _setup(tmpdir)
                try:
                    await _send(manager, "I am angry!", user_id="alice")

                    resp = client.get("/sessions/console:alice:direct/debug")
                    assert resp.status_code == 200
                    data = resp.json()

                    assert data["session_key"] == "console:alice:direct"
                    assert data["rel_key"] == "console:alice"
                    assert data["user_id"] == "alice"
                    assert isinstance(data["last_activity"], float)

                    # Live modulators
                    mods = data["modulators"]
                    assert "arousal" in mods
                    assert "valence" in mods
                    assert "energy" in mods
                    assert "resolution" in mods

                    # Emotion label
                    assert isinstance(data["emotion_label"], str)

                    # Memory counts
                    mem = data["memory"]
                    assert "short_term" in mem
                    assert "long_term" in mem
                    assert "relationship_events" in mem
                    assert "open_loops" in mem

                    # Resolution
                    assert "unresolved_count" in data
                    assert isinstance(data["unresolved_items"], list)
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_debug_last_turn_present(self):
        """Debug view includes full last_turn debug state."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, client, _ = _setup(tmpdir)
                try:
                    await _send(manager, "hello there", user_id="bob")

                    resp = client.get("/sessions/console:bob:direct/debug")
                    data = resp.json()

                    last_turn = data["last_turn"]
                    assert last_turn is not None
                    assert last_turn["user_message"] == "hello there"
                    assert "modulator_snapshot" in last_turn
                    assert "event_classified" in last_turn
                    assert "response" in last_turn
                    assert "emotion_label" in last_turn
                    assert "stage_timings_ms" in last_turn
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_debug_preserves_v2_fields(self):
        """Debug output includes v2 fields: appraisal, anticipation, dialogue, defense."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, client, _ = _setup(tmpdir)
                try:
                    await _send(manager, "test", user_id="user")

                    resp = client.get("/sessions/console:user:direct/debug")
                    last_turn = resp.json()["last_turn"]

                    # v2 fields present (may be null with MockLLMBackend)
                    assert "appraisal_frame" in last_turn
                    assert "relationship_context" in last_turn
                    assert "anticipation" in last_turn
                    assert "dialogue_trace" in last_turn
                    assert "defense_activation" in last_turn
                    assert "unresolved_count" in last_turn
                    assert "unresolved_items" in last_turn
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_debug_404_for_missing_session(self):
        """Requesting debug for a non-existent session returns 404."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _, client, _ = _setup(tmpdir)
            resp = client.get("/sessions/console:nobody:direct/debug")
            assert resp.status_code == 404

    def test_debug_modulators_reflect_emotion(self):
        """Debug modulators change based on the emotional content of messages."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, client, _ = _setup(tmpdir)
                try:
                    await _send(manager, "I'm absolutely furious!", user_id="tester")

                    resp = client.get("/sessions/console:tester:direct/debug")
                    mods = resp.json()["modulators"]
                    # After angry message, arousal should be elevated (> baseline 0.5)
                    assert mods["arousal"] > 0.5
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# POST /sessions/{session_key}/reset — per-session reset
# Uses httpx.AsyncClient to stay on the same event loop as sessions.
# =========================================================================

class TestPerUserReset:
    def test_reset_evicts_session(self):
        """Reset endpoint evicts the session from the manager."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, _, app = _setup(tmpdir)
                try:
                    await _send(manager, "hi", user_id="alice")
                    assert "console:alice:direct" in manager.active_sessions

                    ac = await _async_client(app)
                    async with ac:
                        resp = await ac.post("/sessions/console:alice:direct/reset")
                    assert resp.status_code == 200
                    assert resp.json()["status"] == "evicted"
                    assert resp.json()["session_key"] == "console:alice:direct"

                    assert "console:alice:direct" not in manager.active_sessions
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_reset_404_for_missing_session(self):
        """Reset for a non-existent session returns 404."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _, client, _ = _setup(tmpdir)
            resp = client.post("/sessions/console:nobody:direct/reset")
            assert resp.status_code == 404

    def test_reset_removes_from_listing(self):
        """After reset, the session no longer appears in the listing."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, _, app = _setup(tmpdir)
                try:
                    await _send(manager, "hi", user_id="alice")
                    await _send(manager, "hi", user_id="bob")

                    ac = await _async_client(app)
                    async with ac:
                        await ac.post("/sessions/console:alice:direct/reset")
                        resp = await ac.get("/sessions")

                    keys = {s["session_key"] for s in resp.json()}
                    assert "console:alice:direct" not in keys
                    assert "console:bob:direct" in keys
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Session isolation
# =========================================================================

class TestSessionIsolation:
    def test_different_users_different_debug(self):
        """Two users have independent debug states."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, client, _ = _setup(tmpdir)
                try:
                    await _send(manager, "I am so happy!", user_id="alice")
                    await _send(manager, "I am really angry!", user_id="bob")

                    alice_debug = client.get("/sessions/console:alice:direct/debug").json()
                    bob_debug = client.get("/sessions/console:bob:direct/debug").json()

                    # Different users, different emotional states
                    assert alice_debug["user_id"] == "alice"
                    assert bob_debug["user_id"] == "bob"

                    # Their modulator snapshots should differ
                    alice_mods = alice_debug["modulators"]
                    bob_mods = bob_debug["modulators"]
                    # Valence: happy → higher, angry → lower
                    assert alice_mods["valence"] != bob_mods["valence"]
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_reset_one_does_not_affect_other(self):
        """Resetting one user's session leaves the other intact."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, _, app = _setup(tmpdir)
                try:
                    await _send(manager, "hi", user_id="alice")
                    await _send(manager, "hi", user_id="bob")

                    ac = await _async_client(app)
                    async with ac:
                        await ac.post("/sessions/console:alice:direct/reset")

                        # Bob should still be accessible
                        resp = await ac.get("/sessions/console:bob:direct/debug")
                        assert resp.status_code == 200
                        assert resp.json()["user_id"] == "bob"

                        # Alice should be gone
                        resp = await ac.get("/sessions/console:alice:direct/debug")
                        assert resp.status_code == 404
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_debug_shows_correct_last_turn_per_user(self):
        """Each user's last_turn reflects their own last message."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                manager, client, _ = _setup(tmpdir)
                try:
                    await _send(manager, "alice message", user_id="alice")
                    await _send(manager, "bob message", user_id="bob")

                    alice_turn = client.get("/sessions/console:alice:direct/debug").json()["last_turn"]
                    bob_turn = client.get("/sessions/console:bob:direct/debug").json()["last_turn"]

                    assert alice_turn["user_message"] == "alice message"
                    assert bob_turn["user_message"] == "bob message"
                finally:
                    await manager.shutdown()

        asyncio.run(run())
