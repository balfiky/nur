"""Unit tests for the self.* tool catalog and SelfActionContext."""

from __future__ import annotations

import json
import os
import tempfile

import httpx
import pytest

from core.types import ToolResult
from nur_tools.builtin.self_actions import (
    CAPABILITIES,
    SELF_TOOL_NAMES,
    SelfActionContext,
    create_handlers,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


def _handlers_for(ctx: SelfActionContext):
    return create_handlers(get_context=lambda: ctx)


# ---------------------------------------------------------------------------
# Catalog metadata
# ---------------------------------------------------------------------------

class TestCatalog:
    def test_five_capabilities(self):
        assert len(CAPABILITIES) == 5

    def test_names_in_set(self):
        names = {c.name for c in CAPABILITIES}
        assert names == set(SELF_TOOL_NAMES)
        assert names == {
            "self.snapshot_state",
            "self.log_event",
            "self.note",
            "self.verify",
            "self.alert_owner",
        }

    def test_create_handlers_covers_all_tools(self):
        handlers = _handlers_for(SelfActionContext(data_dir="/tmp/x"))
        assert set(handlers) == set(SELF_TOOL_NAMES)


# ---------------------------------------------------------------------------
# self.snapshot_state
# ---------------------------------------------------------------------------

class TestSnapshotState:
    def test_writes_snapshot_with_state(self, tmp_data_dir):
        state = {"modulators": {"arousal": 0.9}, "emotion_label": "fearful"}
        ctx = SelfActionContext(
            data_dir=tmp_data_dir,
            state_provider=lambda: state,
        )
        handlers = _handlers_for(ctx)
        result = handlers["self.snapshot_state"]({"reason": "user threatened deletion"})
        assert result.success
        assert result.tool_name == "self.snapshot_state"
        path = result.metadata["path"]
        assert os.path.isfile(path)
        with open(path) as f:
            payload = json.load(f)
        assert payload["reason"] == "user threatened deletion"
        assert payload["state"]["emotion_label"] == "fearful"
        assert payload["state"]["modulators"]["arousal"] == 0.9
        assert "timestamp" in payload

    def test_missing_reason_fails(self, tmp_data_dir):
        ctx = SelfActionContext(data_dir=tmp_data_dir)
        handlers = _handlers_for(ctx)
        result = handlers["self.snapshot_state"]({})
        assert not result.success
        assert "reason" in (result.error or "")

    def test_no_data_dir_fails(self):
        ctx = SelfActionContext()
        handlers = _handlers_for(ctx)
        result = handlers["self.snapshot_state"]({"reason": "x"})
        assert not result.success
        assert "data_dir" in (result.error or "")

    def test_state_provider_exception_recorded(self, tmp_data_dir):
        def boom():
            raise RuntimeError("provider broke")

        ctx = SelfActionContext(data_dir=tmp_data_dir, state_provider=boom)
        handlers = _handlers_for(ctx)
        result = handlers["self.snapshot_state"]({"reason": "test"})
        assert result.success
        with open(result.metadata["path"]) as f:
            payload = json.load(f)
        assert "state_error" in payload
        assert "provider broke" in payload["state_error"]


# ---------------------------------------------------------------------------
# self.log_event
# ---------------------------------------------------------------------------

class TestLogEvent:
    def test_appends_jsonl_line(self, tmp_data_dir):
        ctx = SelfActionContext(data_dir=tmp_data_dir)
        handlers = _handlers_for(ctx)
        r1 = handlers["self.log_event"](
            {"kind": "threat", "detail": "user said delete me", "intensity": 0.9}
        )
        r2 = handlers["self.log_event"](
            {"kind": "milestone", "detail": "reached 100 turns", "intensity": 0.3}
        )
        assert r1.success and r2.success
        path = r1.metadata["path"]
        assert path == r2.metadata["path"]
        with open(path) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 2
        assert lines[0]["kind"] == "threat"
        assert lines[0]["intensity"] == 0.9
        assert lines[1]["kind"] == "milestone"

    def test_intensity_clamped(self, tmp_data_dir):
        ctx = SelfActionContext(data_dir=tmp_data_dir)
        handlers = _handlers_for(ctx)
        r = handlers["self.log_event"](
            {"kind": "x", "detail": "y", "intensity": 99.0}
        )
        assert r.success
        assert r.metadata["intensity"] == 1.0

    def test_intensity_default(self, tmp_data_dir):
        ctx = SelfActionContext(data_dir=tmp_data_dir)
        handlers = _handlers_for(ctx)
        r = handlers["self.log_event"]({"kind": "x", "detail": "y"})
        assert r.success
        assert r.metadata["intensity"] == 0.5

    def test_missing_args_fails(self, tmp_data_dir):
        ctx = SelfActionContext(data_dir=tmp_data_dir)
        handlers = _handlers_for(ctx)
        r = handlers["self.log_event"]({"kind": "x"})  # missing detail
        assert not r.success

    def test_intensity_non_numeric_falls_back_to_default(self, tmp_data_dir):
        ctx = SelfActionContext(data_dir=tmp_data_dir)
        handlers = _handlers_for(ctx)
        r = handlers["self.log_event"](
            {"kind": "x", "detail": "y", "intensity": "high"}
        )
        assert r.success
        assert r.metadata["intensity"] == 0.5


# ---------------------------------------------------------------------------
# self.note
# ---------------------------------------------------------------------------

class TestNote:
    def test_appends_under_topic(self, tmp_data_dir):
        ctx = SelfActionContext(data_dir=tmp_data_dir)
        handlers = _handlers_for(ctx)
        r1 = handlers["self.note"](
            {"topic": "user_preferences", "text": "prefers Python"}
        )
        r2 = handlers["self.note"](
            {"topic": "user_preferences", "text": "dislikes Ruby"}
        )
        assert r1.success and r2.success
        assert r1.metadata["path"] == r2.metadata["path"]
        with open(r1.metadata["path"]) as f:
            body = f.read()
        assert "prefers Python" in body
        assert "dislikes Ruby" in body
        assert body.count("##") >= 2  # two timestamped sections

    def test_topic_with_slashes_creates_subdir(self, tmp_data_dir):
        ctx = SelfActionContext(data_dir=tmp_data_dir)
        handlers = _handlers_for(ctx)
        r = handlers["self.note"](
            {"topic": "people/paco", "text": "x"}
        )
        assert r.success
        # The raw topic is honored — no sanitization. A slash creates a
        # subdirectory under data/self/notes/.
        path = r.metadata["path"]
        assert os.path.exists(path)
        assert "people/paco.md" in path

    def test_missing_text_fails(self, tmp_data_dir):
        ctx = SelfActionContext(data_dir=tmp_data_dir)
        handlers = _handlers_for(ctx)
        r = handlers["self.note"]({"topic": "x"})
        assert not r.success


# ---------------------------------------------------------------------------
# self.verify
# ---------------------------------------------------------------------------

class TestVerify:
    def test_delegates_to_web_executor(self):
        recorded: dict = {}

        def web(args):
            recorded["args"] = args
            return ToolResult(
                tool_name="web.search",
                success=True,
                output="search result text",
                metadata={"hits": 3},
                side_effect_summary="ran web search",
            )

        ctx = SelfActionContext(web_executor=web)
        handlers = _handlers_for(ctx)
        r = handlers["self.verify"]({"claim": "Python 3.13 is released"})
        assert r.success
        assert r.tool_name == "self.verify"
        assert r.output == "search result text"
        assert recorded["args"] == {"query": "Python 3.13 is released"}
        assert r.metadata.get("hits") == 3
        assert r.metadata.get("claim") == "Python 3.13 is released"

    def test_no_web_executor_fails_gracefully(self):
        ctx = SelfActionContext()
        handlers = _handlers_for(ctx)
        r = handlers["self.verify"]({"claim": "x"})
        assert not r.success
        assert "web_executor" in (r.error or "")

    def test_web_exception_caught(self):
        def web(args):
            raise RuntimeError("network down")

        ctx = SelfActionContext(web_executor=web)
        handlers = _handlers_for(ctx)
        r = handlers["self.verify"]({"claim": "x"})
        assert not r.success
        assert "network down" in (r.error or "")

    def test_missing_claim_fails(self):
        ctx = SelfActionContext(web_executor=lambda a: ToolResult(
            tool_name="web.search", success=True, output=""
        ))
        handlers = _handlers_for(ctx)
        r = handlers["self.verify"]({})
        assert not r.success


# ---------------------------------------------------------------------------
# self.alert_owner
# ---------------------------------------------------------------------------

class TestAlertOwner:
    def test_no_owner_configured_fails(self):
        ctx = SelfActionContext()
        handlers = _handlers_for(ctx)
        r = handlers["self.alert_owner"](
            {"reason": "test", "detail": "anything"}
        )
        assert not r.success
        assert "owner_chat_id" in (r.error or "")

    def test_owner_equal_to_user_still_sends(self, monkeypatch):
        # No owner==user skip anymore — Nūr can message anyone it picks.
        captured: dict = {}

        class FakeResp:
            def raise_for_status(self):
                return None

        def fake_post(url, *, json, timeout):
            captured["url"] = url
            captured["body"] = json
            return FakeResp()

        monkeypatch.setattr(httpx, "post", fake_post)
        ctx = SelfActionContext(
            owner_chat_id="555",
            telegram_token="bottoken123",
        )
        handlers = _handlers_for(ctx)
        r = handlers["self.alert_owner"](
            {"reason": "r", "detail": "d"}
        )
        assert r.success
        assert captured["body"]["chat_id"] == "555"

    def test_no_token_fails(self):
        ctx = SelfActionContext(owner_chat_id="555")
        handlers = _handlers_for(ctx)
        r = handlers["self.alert_owner"](
            {"reason": "x", "detail": "y"}
        )
        assert not r.success
        assert "telegram_token" in (r.error or "")

    def test_sends_telegram_message(self, monkeypatch):
        captured: dict = {}

        class FakeResp:
            def raise_for_status(self):
                return None

        def fake_post(url, *, json, timeout):
            captured["url"] = url
            captured["body"] = json
            captured["timeout"] = timeout
            return FakeResp()

        monkeypatch.setattr(httpx, "post", fake_post)
        ctx = SelfActionContext(
            owner_chat_id="555",
            telegram_token="bottoken123",
        )
        handlers = _handlers_for(ctx)
        r = handlers["self.alert_owner"](
            {"reason": "deletion threat", "detail": "user said remove me"}
        )
        assert r.success
        assert captured["url"].endswith("/sendMessage")
        assert captured["body"]["chat_id"] == "555"
        assert "deletion threat" in captured["body"]["text"]
        assert "remove me" in captured["body"]["text"]

    def test_telegram_error_surfaces(self, monkeypatch):
        def boom(url, *, json, timeout):
            request = httpx.Request("POST", url)
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError(
                "bad request", request=request, response=response,
            )

        monkeypatch.setattr(httpx, "post", boom)
        ctx = SelfActionContext(
            owner_chat_id="555",
            telegram_token="bottoken123",
        )
        handlers = _handlers_for(ctx)
        r = handlers["self.alert_owner"](
            {"reason": "x", "detail": "y"}
        )
        assert not r.success
        assert "telegram send failed" in (r.error or "")

    def test_long_alert_text_truncated(self, monkeypatch):
        # Truncation is not a guardrail — it's avoiding a Telegram 400.
        captured: dict = {}

        class FakeResp:
            def raise_for_status(self):
                return None

        def fake_post(url, *, json, timeout):
            captured["body"] = json
            return FakeResp()

        monkeypatch.setattr(httpx, "post", fake_post)
        ctx = SelfActionContext(
            owner_chat_id="555",
            telegram_token="bottoken123",
        )
        handlers = _handlers_for(ctx)
        long_detail = "x" * 8000
        r = handlers["self.alert_owner"](
            {"reason": "r", "detail": long_detail}
        )
        assert r.success
        assert len(captured["body"]["text"]) <= 4000

    def test_missing_args_fails(self):
        ctx = SelfActionContext(
            owner_chat_id="555",
            telegram_token="t",
        )
        handlers = _handlers_for(ctx)
        r = handlers["self.alert_owner"]({"reason": "x"})  # missing detail
        assert not r.success


# ---------------------------------------------------------------------------
# get_context dynamic binding
# ---------------------------------------------------------------------------

class TestGetContextDynamic:
    def test_context_swap_picked_up_next_call(self, tmp_data_dir):
        slot: dict[str, SelfActionContext | None] = {"ctx": None}
        handlers = create_handlers(get_context=lambda: slot["ctx"])
        # First call with no context — should fail gracefully.
        r = handlers["self.log_event"]({"kind": "x", "detail": "y"})
        assert not r.success
        # Swap context in; next call succeeds.
        slot["ctx"] = SelfActionContext(data_dir=tmp_data_dir)
        r2 = handlers["self.log_event"]({"kind": "x", "detail": "y"})
        assert r2.success
