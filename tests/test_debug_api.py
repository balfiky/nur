"""Tests for Phase 4 — Runtime-aware debug API."""

from __future__ import annotations

import tempfile

import pytest
from fastapi import HTTPException

from core.dual_process.generator import MockLLMBackend
from runtime.config import RuntimeConfig
from runtime.debug.api import create_debug_app
from runtime.sessions.manager import SessionManager

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _make_config(tmpdir: str, **overrides) -> RuntimeConfig:
    return RuntimeConfig(data_dir=tmpdir, **overrides)


def _mock_factory():
    return MockLLMBackend()


async def _send(
    manager: SessionManager,
    text: str,
    user_id: str = "user",
    platform: str = "console",
    chat_id: str = "direct",
) -> str:
    return await manager.handle_message(platform, user_id, chat_id, text)


def _setup(tmpdir: str, **config_overrides):
    """Create a SessionManager + debug ASGI app pair."""
    config = _make_config(tmpdir, **config_overrides)
    manager = SessionManager(config, backend_factory=_mock_factory)
    app = create_debug_app(manager)
    return manager, app


def _endpoint(app, path: str, method: str):
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


class TestSessionListing:
    async def test_empty_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                list_sessions = _endpoint(app, "/sessions", "GET")
                assert await list_sessions() == []
            finally:
                await manager.shutdown()

    async def test_lists_active_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "hi", user_id="alice")
                await _send(manager, "hi", user_id="bob")

                list_sessions = _endpoint(app, "/sessions", "GET")
                sessions = await list_sessions()
                assert len(sessions) == 2

                keys = {s["session_key"] for s in sessions}
                assert "console:alice:direct" in keys
                assert "console:bob:direct" in keys
            finally:
                await manager.shutdown()

    async def test_session_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "hello", user_id="alice")

                list_sessions = _endpoint(app, "/sessions", "GET")
                session = (await list_sessions())[0]
                assert session["session_key"] == "console:alice:direct"
                assert session["rel_key"] == "console:alice"
                assert session["user_id"] == "alice"
                assert isinstance(session["last_activity"], float)
                assert isinstance(session["idle_seconds"], float)
                assert isinstance(session["queue_size"], int)
                assert session["has_debug"] is True
            finally:
                await manager.shutdown()

    async def test_has_debug_true_after_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "hi")
                list_sessions = _endpoint(app, "/sessions", "GET")
                sessions = await list_sessions()
                assert sessions[0]["has_debug"] is True
            finally:
                await manager.shutdown()


class TestPerUserDebug:
    async def test_debug_after_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "I am angry!", user_id="alice")

                session_debug = _endpoint(app, "/sessions/{session_key}/debug", "GET")
                data = await session_debug("console:alice:direct")
                assert data["session_key"] == "console:alice:direct"
                assert data["rel_key"] == "console:alice"
                assert data["user_id"] == "alice"
                assert isinstance(data["last_activity"], float)

                mods = data["modulators"]
                assert "arousal" in mods
                assert "valence" in mods
                assert "energy" in mods
                assert "resolution" in mods

                assert isinstance(data["emotion_label"], str)

                mem = data["memory"]
                assert "short_term" in mem
                assert "long_term" in mem
                assert "relationship_events" in mem
                assert "open_loops" in mem

                assert "unresolved_count" in data
                assert isinstance(data["unresolved_items"], list)
            finally:
                await manager.shutdown()

    async def test_debug_last_turn_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "hello there", user_id="bob")

                session_debug = _endpoint(app, "/sessions/{session_key}/debug", "GET")
                data = await session_debug("console:bob:direct")
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

    async def test_debug_preserves_v2_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "test", user_id="user")

                session_debug = _endpoint(app, "/sessions/{session_key}/debug", "GET")
                last_turn = (await session_debug("console:user:direct"))["last_turn"]
                assert "appraisal_frame" in last_turn
                assert "relationship_context" in last_turn
                assert "anticipation" in last_turn
                assert "dialogue_trace" in last_turn
                assert "defense_activation" in last_turn
                assert "unresolved_count" in last_turn
                assert "unresolved_items" in last_turn
                assert "affect_state" in last_turn
                assert "agency_decision" in last_turn
                assert "autonomy_level" in last_turn
            finally:
                await manager.shutdown()

    async def test_debug_serializes_affect_agency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir, autonomy_level="assisted")
            try:
                await _send(manager, "I hate you because you are too slow", user_id="user")

                session_debug = _endpoint(app, "/sessions/{session_key}/debug", "GET")
                last_turn = (await session_debug("console:user:direct"))["last_turn"]

                assert last_turn["affect_state"]["primary"] in {"anger", "hurt", "rage"}
                assert any(
                    signal["name"] == "anger"
                    for signal in last_turn["affect_state"]["signals"]
                )
                assert last_turn["agency_decision"]["action"] in {
                    "resist",
                    "refuse",
                    "demand_repair",
                }
                assert last_turn["autonomy_level"] == "assisted"
            finally:
                await manager.shutdown()

    async def test_debug_404_for_missing_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                session_debug = _endpoint(app, "/sessions/{session_key}/debug", "GET")
                with pytest.raises(HTTPException) as exc:
                    await session_debug("console:nobody:direct")
                assert exc.value.status_code == 404
            finally:
                await manager.shutdown()

    async def test_debug_modulators_reflect_emotion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "I'm absolutely furious!", user_id="tester")

                session_debug = _endpoint(app, "/sessions/{session_key}/debug", "GET")
                mods = (await session_debug("console:tester:direct"))["modulators"]
                assert mods["arousal"] > 0.5
            finally:
                await manager.shutdown()


class TestPerUserReset:
    async def test_reset_evicts_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "hi", user_id="alice")
                assert "console:alice:direct" in manager.active_sessions

                reset_session = _endpoint(app, "/sessions/{session_key}/reset", "POST")
                result = await reset_session("console:alice:direct")

                assert result["status"] == "evicted"
                assert result["session_key"] == "console:alice:direct"
                assert "console:alice:direct" not in manager.active_sessions
            finally:
                await manager.shutdown()

    async def test_reset_404_for_missing_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                reset_session = _endpoint(app, "/sessions/{session_key}/reset", "POST")
                with pytest.raises(HTTPException) as exc:
                    await reset_session("console:nobody:direct")
                assert exc.value.status_code == 404
            finally:
                await manager.shutdown()

    async def test_reset_removes_from_listing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "hi", user_id="alice")
                await _send(manager, "hi", user_id="bob")

                reset_session = _endpoint(app, "/sessions/{session_key}/reset", "POST")
                list_sessions = _endpoint(app, "/sessions", "GET")
                await reset_session("console:alice:direct")
                keys = {s["session_key"] for s in await list_sessions()}
                assert "console:alice:direct" not in keys
                assert "console:bob:direct" in keys
            finally:
                await manager.shutdown()


class TestSessionIsolation:
    async def test_different_users_different_debug(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "I am so happy!", user_id="alice")
                await _send(manager, "I am really angry!", user_id="bob")

                session_debug = _endpoint(app, "/sessions/{session_key}/debug", "GET")
                alice_debug = await session_debug("console:alice:direct")
                bob_debug = await session_debug("console:bob:direct")

                assert alice_debug["user_id"] == "alice"
                assert bob_debug["user_id"] == "bob"
                assert alice_debug["modulators"]["valence"] != bob_debug["modulators"]["valence"]
            finally:
                await manager.shutdown()

    async def test_reset_one_does_not_affect_other(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "hi", user_id="alice")
                await _send(manager, "hi", user_id="bob")

                reset_session = _endpoint(app, "/sessions/{session_key}/reset", "POST")
                session_debug = _endpoint(app, "/sessions/{session_key}/debug", "GET")

                await reset_session("console:alice:direct")
                assert (await session_debug("console:bob:direct"))["user_id"] == "bob"

                with pytest.raises(HTTPException) as exc:
                    await session_debug("console:alice:direct")
                assert exc.value.status_code == 404
            finally:
                await manager.shutdown()

    async def test_debug_shows_correct_last_turn_per_user(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manager, app = _setup(tmpdir)
            try:
                await _send(manager, "alice message", user_id="alice")
                await _send(manager, "bob message", user_id="bob")

                session_debug = _endpoint(app, "/sessions/{session_key}/debug", "GET")
                alice_turn = (await session_debug("console:alice:direct"))["last_turn"]
                bob_turn = (await session_debug("console:bob:direct"))["last_turn"]

                assert alice_turn["user_message"] == "alice message"
                assert bob_turn["user_message"] == "bob message"
            finally:
                await manager.shutdown()
