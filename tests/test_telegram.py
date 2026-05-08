"""Tests for Phase 2 — Telegram channel adapter.

Covers:
- Message normalization (extract user_id, chat_id, text from updates)
- Allowlist rejection
- Dedupe behavior (TTL cache)
- Command handling (/status, /reset, /debug)
- Typing indicator behavior
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time

import httpx

from tests._fakes import MockLLMBackend
from core.types import OpenLoop, RelationshipContext, RelationshipEvent
from runtime.channels.telegram import (
    DedupeCache,
    TelegramChannel,
    TelegramConfig,
    is_telegram_token_pollable,
)
from runtime.config import RuntimeConfig
from runtime.sessions.manager import SessionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmpdir: str, **overrides) -> RuntimeConfig:
    return RuntimeConfig(data_dir=tmpdir, **overrides)


def _mock_factory():
    return MockLLMBackend()


def _make_update(
    update_id: int = 1,
    user_id: int = 100,
    chat_id: int = 100,
    text: str = "hello",
    *,
    first_name: str = "Test",
) -> dict:
    """Build a minimal Telegram update dict."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": user_id, "is_bot": False, "first_name": first_name},
            "chat": {"id": chat_id, "type": "private"},
            "date": int(time.time()),
            "text": text,
        },
    }


class MockTelegramClient:
    """Records API calls instead of hitting the Telegram servers."""

    def __init__(self) -> None:
        self.sent_messages: list[dict] = []
        self.sent_typings: list[dict] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send_message(self, chat_id: int, text: str) -> dict:
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return {"message_id": len(self.sent_messages)}

    async def send_typing(self, chat_id: int) -> None:
        self.sent_typings.append({"chat_id": chat_id, "time": time.time()})


class RejectingTelegramClient(MockTelegramClient):
    """Raises a fixed Telegram HTTP error from getUpdates."""

    def __init__(self, status_code: int) -> None:
        super().__init__()
        self.status_code = status_code
        self.update_calls = 0

    async def get_updates(
        self, offset: int | None = None, timeout: int = 30,
    ) -> list[dict]:
        self.update_calls += 1
        request = httpx.Request("GET", "https://api.telegram.org/botx/getUpdates")
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("rejected", request=request, response=response)


def _make_channel(
    tmpdir: str,
    client: MockTelegramClient | None = None,
    allowlist: set[str] | None = None,
    dedupe_ttl: float = 60.0,
) -> tuple[TelegramChannel, SessionManager, MockTelegramClient]:
    """Create a TelegramChannel backed by mocks."""
    config = _make_config(tmpdir)
    manager = SessionManager(config, backend_factory=_mock_factory)
    mock_client = client or MockTelegramClient()
    tg_config = TelegramConfig(
        token="fake-token",
        allowlist=allowlist or set(),
        dedupe_ttl=dedupe_ttl,
    )
    channel = TelegramChannel(mock_client, manager, tg_config)
    return channel, manager, mock_client


def _raise_if_processed(*_args, **_kwargs):
    raise AssertionError("introspection command called pipeline.process")


def _relationship_context() -> RelationshipContext:
    return RelationshipContext(
        summary="Open loops: deadline. Recent arc: rupture and repair.",
        active_loops=[
            OpenLoop(
                loop_kind="tension",
                source_person="100",
                topic="deadline",
                description="unresolved tension about deadline",
                intensity=0.7,
                related_key="deadline",
            )
        ],
        recent_events=[
            RelationshipEvent(
                event_kind="repair",
                source_person="100",
                topic="deadline",
                summary="Repair around deadline",
                valence=0.6,
                intensity=0.6,
                confidence=0.8,
                related_key="deadline",
            ),
            RelationshipEvent(
                event_kind="rupture",
                source_person="100",
                topic="deadline",
                summary="Rupture around deadline",
                valence=-0.7,
                intensity=0.7,
                confidence=0.8,
                related_key="deadline",
            ),
        ],
        open_loop_count=1,
    )


# =========================================================================
# DedupeCache
# =========================================================================

class TestDedupeCache:
    def test_first_time_not_duplicate(self):
        cache = DedupeCache(ttl=10.0)
        assert not cache.is_duplicate(1)

    def test_marked_is_duplicate(self):
        cache = DedupeCache(ttl=10.0)
        cache.mark(1)
        assert cache.is_duplicate(1)

    def test_different_ids_independent(self):
        cache = DedupeCache(ttl=10.0)
        cache.mark(1)
        assert not cache.is_duplicate(2)

    def test_expired_entry_not_duplicate(self):
        cache = DedupeCache(ttl=0.05)
        cache.mark(1)
        time.sleep(0.06)
        assert not cache.is_duplicate(1)

    def test_len_reflects_active_entries(self):
        cache = DedupeCache(ttl=10.0)
        cache.mark(1)
        cache.mark(2)
        assert len(cache) == 2


class TestTelegramTokenShape:
    def test_pollable_token_requires_numeric_bot_id_and_secret(self):
        assert is_telegram_token_pollable("123456:abc")
        assert not is_telegram_token_pollable("not-a-real-token")
        assert not is_telegram_token_pollable("bot123:abc")
        assert not is_telegram_token_pollable("123456:")
        assert not is_telegram_token_pollable("123 456:abc")


class TestPollingFailures:
    def test_auth_rejection_stops_polling(self):
        """Invalid real-looking tokens should not flood logs forever."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                client = RejectingTelegramClient(status_code=401)
                channel, manager, _ = _make_channel(tmpdir, client=client)
                try:
                    await channel.start()
                    assert client.update_calls == 1
                finally:
                    await channel.stop()
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Message normalization
# =========================================================================

class TestMessageNormalization:
    def test_text_message_processed(self):
        """A normal text message produces a response."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="hi there"))
                    assert len(client.sent_messages) == 1
                    assert client.sent_messages[0]["chat_id"] == 100
                    assert len(client.sent_messages[0]["text"]) > 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_non_text_message_ignored(self):
        """Updates without a text field are ignored."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    update = _make_update()
                    del update["message"]["text"]
                    update["message"]["photo"] = [{"file_id": "abc"}]
                    await channel.handle_update(update)
                    assert len(client.sent_messages) == 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_missing_message_field_ignored(self):
        """Updates without a message field (e.g. edited_message) are ignored."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update({"update_id": 1})
                    assert len(client.sent_messages) == 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_missing_from_field_ignored(self):
        """Messages without a from field are ignored."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    update = _make_update()
                    del update["message"]["from"]
                    await channel.handle_update(update)
                    assert len(client.sent_messages) == 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_user_id_passed_as_string(self):
        """Numeric Telegram user IDs become strings in the session manager."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(
                        _make_update(user_id=42, chat_id=42)
                    )
                    assert "telegram:42:42" in manager.active_sessions
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Allowlist
# =========================================================================

class TestAllowlist:
    def test_allowed_user_processed(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(
                    tmpdir, allowlist={"100", "200"},
                )
                try:
                    await channel.handle_update(
                        _make_update(user_id=100)
                    )
                    assert len(client.sent_messages) == 1
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_rejected_user_ignored(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(
                    tmpdir, allowlist={"200"},
                )
                try:
                    await channel.handle_update(
                        _make_update(user_id=100)
                    )
                    assert len(client.sent_messages) == 0
                    assert len(manager.active_sessions) == 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_empty_allowlist_allows_all(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(
                    tmpdir, allowlist=set(),
                )
                try:
                    await channel.handle_update(
                        _make_update(user_id=999)
                    )
                    assert len(client.sent_messages) == 1
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Dedupe
# =========================================================================

class TestDedupe:
    def test_duplicate_update_dropped(self):
        """Same update_id sent twice → only one response."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    update = _make_update(update_id=10)
                    await channel.handle_update(update)
                    await channel.handle_update(update)
                    # Only one message sent — second was deduped
                    assert len(client.sent_messages) == 1
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_different_update_ids_both_processed(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(update_id=1))
                    await channel.handle_update(_make_update(update_id=2))
                    assert len(client.sent_messages) == 2
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_expired_dedupe_allows_reprocessing(self):
        """After TTL expires, same update_id is no longer blocked."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(
                    tmpdir, dedupe_ttl=0.05,
                )
                try:
                    update = _make_update(update_id=10)
                    await channel.handle_update(update)
                    assert len(client.sent_messages) == 1
                    await asyncio.sleep(0.06)
                    await channel.handle_update(update)
                    assert len(client.sent_messages) == 2
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Commands
# =========================================================================

class TestCommands:
    def test_status_no_session(self):
        """'/status' with no active session returns informational message."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/status"))
                    assert len(client.sent_messages) == 1
                    assert "no active session" in client.sent_messages[0]["text"].lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_status_with_session(self):
        """'/status' after a message shows modulator values."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    # Create a session first
                    await channel.handle_update(
                        _make_update(update_id=1, text="hello")
                    )
                    await channel.handle_update(
                        _make_update(update_id=2, text="/status")
                    )
                    status_msg = client.sent_messages[-1]["text"]
                    assert "arousal" in status_msg
                    assert "valence" in status_msg
                    assert "energy" in status_msg
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_reset_evicts_session(self):
        """'/reset' evicts the session and confirms."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(
                        _make_update(update_id=1, text="hi")
                    )
                    assert "telegram:100:100" in manager.active_sessions

                    await channel.handle_update(
                        _make_update(update_id=2, text="/reset")
                    )
                    assert "telegram:100:100" not in manager.active_sessions
                    reset_msg = client.sent_messages[-1]["text"].lower()
                    assert "session" in reset_msg
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_new_starts_fresh_active_conversation_without_restoring_hot_state(self):
        """'/new' replaces the active chat session with a clean one."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                session_key = "telegram:100:100"
                state_path = manager.config.session_state_path(session_key)
                history_path = manager.config.session_history_path(session_key)
                try:
                    await channel.handle_update(
                        _make_update(update_id=1, text="I am furious about this")
                    )
                    assert session_key in manager.active_sessions
                    assert os.path.exists(history_path)

                    await channel.handle_update(
                        _make_update(update_id=2, text="/new")
                    )

                    assert session_key in manager.active_sessions
                    assert not os.path.exists(state_path)
                    assert not os.path.exists(history_path)
                    snap = manager.active_sessions[session_key].pipeline.engine.snapshot()
                    assert snap["arousal"] == 0.5
                    assert snap["valence"] == 0.5
                    assert "new conversation" in client.sent_messages[-1]["text"].lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_mental_reports_assistant_state(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/mental"))
                    assert len(client.sent_messages) == 1
                    message = client.sent_messages[-1]["text"].lower()
                    assert "mental state" in message
                    assert "mental health" in message
                    assert "arousal" in message
                    assert "resolution" in message
                    assert "telegram:100:100" in manager.active_sessions
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_mood_alias_reports_assistant_state(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/mood"))
                    assert "mental state" in client.sent_messages[-1]["text"].lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_debug_returns_message(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/debug"))
                    assert len(client.sent_messages) == 1
                    assert "debug" in client.sent_messages[0]["text"].lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_debug_reports_active_session_summary(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(
                        _make_update(update_id=1, text="hello")
                    )
                    await channel.handle_update(
                        _make_update(update_id=2, text="/debug")
                    )
                    message = client.sent_messages[-1]["text"].lower()
                    assert "debug session" in message
                    assert "telegram:100:100" in message
                    assert "last turn: yes" in message
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_state_no_session_does_not_create_session(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/state"))
                    assert manager.active_sessions == {}
                    assert "no active session" in client.sent_messages[-1]["text"].lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_state_with_active_session_reports_compact_state_without_processing(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(update_id=1, text="hello"))
                    session = manager.active_sessions["telegram:100:100"]
                    session.pipeline.process = _raise_if_processed  # type: ignore[method-assign]

                    await channel.handle_update(_make_update(update_id=2, text="/state"))

                    message = client.sent_messages[-1]["text"].lower()
                    assert "nūr state" in message
                    assert "arousal" in message
                    assert "open loops" in message
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_why_returns_last_explanation(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(update_id=1, text="I am scared about work"))
                    await channel.handle_update(_make_update(update_id=2, text="/why"))

                    message = client.sent_messages[-1]["text"].lower()
                    assert "why this response" in message
                    assert "interpreted" in message or "selected" in message
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_memory_loops_and_repair_use_last_relationship_context(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(update_id=1, text="hello"))
                    session = manager.active_sessions["telegram:100:100"]
                    assert session.last_debug is not None
                    session.last_debug.relationship_context = _relationship_context()

                    await channel.handle_update(_make_update(update_id=2, text="/memory"))
                    memory_msg = client.sent_messages[-1]["text"].lower()
                    assert "what i remember" in memory_msg
                    assert "deadline" in memory_msg

                    await channel.handle_update(_make_update(update_id=3, text="/loops"))
                    loops_msg = client.sent_messages[-1]["text"].lower()
                    assert "active loops" in loops_msg
                    assert "deadline" in loops_msg

                    await channel.handle_update(_make_update(update_id=4, text="/repair"))
                    repair_msg = client.sent_messages[-1]["text"].lower()
                    assert "repair context" in repair_msg
                    assert "rupture" in repair_msg
                    assert "repair" in repair_msg
                    assert "open loops remain: 1" in repair_msg
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_introspection_commands_do_not_call_pipeline_process(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(update_id=1, text="hello"))
                    session = manager.active_sessions["telegram:100:100"]
                    session.pipeline.process = _raise_if_processed  # type: ignore[method-assign]
                    session.last_debug.relationship_context = _relationship_context()  # type: ignore[union-attr]

                    for update_id, command in enumerate(
                        ["/state", "/why", "/memory", "/loops", "/repair", "/persona", "/persona all"],
                        start=2,
                    ):
                        await channel.handle_update(_make_update(update_id=update_id, text=command))

                    assert len(client.sent_messages) == 8
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_persona_command_reports_unified_state(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(update_id=1, text="I am scared about work"))
                    await channel.handle_update(_make_update(update_id=2, text="/persona"))
                    summary = client.sent_messages[-1]["text"].lower()
                    assert "persona:" in summary
                    assert "emotion:" in summary
                    assert "perception:" in summary
                    assert "skills/tools:" in summary

                    await channel.handle_update(_make_update(update_id=3, text="/persona emotions"))
                    emotions = client.sent_messages[-1]["text"].lower()
                    assert "persona emotions" in emotions
                    assert "primary:" in emotions
                    assert "drivers:" in emotions

                    await channel.handle_update(_make_update(update_id=4, text="/persona life"))
                    life = client.sent_messages[-1]["text"].lower()
                    assert "persona life" in life
                    assert "active pressures" in life
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_persona_no_session_does_not_create_session(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/persona"))
                    assert manager.active_sessions == {}
                    assert "no active session" in client.sent_messages[-1]["text"].lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_help_command_lists_telegram_commands(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/help"))
                    message = client.sent_messages[0]["text"].lower()
                    assert "/status" in message
                    assert "/mental" in message
                    assert "/new" in message
                    assert "/state" in message
                    assert "/why" in message
                    assert "/memory" in message
                    assert "/loops" in message
                    assert "/repair" in message
                    assert "/persona" in message
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_unknown_command(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/foo"))
                    message = client.sent_messages[0]["text"].lower()
                    assert "unknown" in message
                    assert "/help" in message
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_command_with_bot_suffix(self):
        """'/status@mybot' is treated as '/status'."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(
                        _make_update(text="/status@JarvisBot")
                    )
                    assert "no active session" in client.sent_messages[0]["text"].lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_start_command(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/start"))
                    assert len(client.sent_messages) == 1
                    assert "/help" in client.sent_messages[0]["text"].lower()
                finally:
                    await manager.shutdown()

        asyncio.run(run())


# =========================================================================
# Typing indicators
# =========================================================================

class TestTypingIndicator:
    def test_typing_sent_during_processing(self):
        """At least one typing indicator is sent while Nūr processes."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(
                        _make_update(update_id=1, text="hello")
                    )
                    # Even for fast mock processing, typing loop fires once immediately
                    assert len(client.sent_typings) >= 1
                    assert client.sent_typings[0]["chat_id"] == 100
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_typing_stops_after_response(self):
        """After the response is sent, no more typing indicators accumulate."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(
                        _make_update(update_id=1, text="hello")
                    )
                    count_after = len(client.sent_typings)
                    await asyncio.sleep(0.1)
                    # No additional typing indicators after processing finished
                    assert len(client.sent_typings) == count_after
                finally:
                    await manager.shutdown()

        asyncio.run(run())

    def test_commands_do_not_trigger_typing(self):
        """Command handling does not start a typing indicator loop."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                channel, manager, client = _make_channel(tmpdir)
                try:
                    await channel.handle_update(_make_update(text="/status"))
                    assert len(client.sent_typings) == 0
                finally:
                    await manager.shutdown()

        asyncio.run(run())
