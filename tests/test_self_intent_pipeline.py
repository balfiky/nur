"""End-to-end pipeline tests for the self-intent stage.

The stage runs whenever a ``tool_executor`` is attached. There is no
allow-list, no per-turn cap, and no path sanitization — Nūr can invoke
any registered tool. These tests prove the wiring: the proposer fires,
arbitrary tool calls land in ``tool_trace.executed_results``, and files
actually appear on disk.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from pipeline import CognitivePipeline
from runtime.config import RuntimeConfig
from runtime.tools import create_tool_executor


class _SelfIntentBackend:
    """Returns a scripted JSON array on the proposer call; prose otherwise."""

    def __init__(self, proposer_response: str, default_response: str = "okay") -> None:
        self._proposer_response = proposer_response
        self._default_response = default_response
        self.proposer_calls = 0
        self.other_calls = 0
        self.last_proposer_user_message: str = ""

    def generate(self, system_prompt: str, user_message: str) -> str:
        # The self-intent system prompt contains a distinctive phrase from
        # config/prompts/self_intent.md; everything else is inner-dialogue
        # or generator output.
        if "JSON array of action objects" in system_prompt:
            self.proposer_calls += 1
            self.last_proposer_user_message = user_message
            return self._proposer_response
        self.other_calls += 1
        return self._default_response


def _make_config(data_dir: str, **overrides) -> RuntimeConfig:
    base = dict(
        data_dir=data_dir,
        tools_enabled=True,
        tools_workspace=os.path.join(data_dir, "workspace"),
        autonomy_level="autonomous",
    )
    base.update(overrides)
    return RuntimeConfig(**base)


def _make_pipeline(config: RuntimeConfig, backend) -> CognitivePipeline:
    return CognitivePipeline(
        llm_backend=backend,
        llm_backend_fast=backend,
        tool_executor=create_tool_executor(config),
        autonomy_level="autonomous",
        runtime_config=config,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_self_intent_fires_and_writes_files(tmp_path):
    config = _make_config(str(tmp_path))
    raw = (
        '[{"tool_name": "self.log_event", '
        '"arguments": {"kind": "deletion_threat", '
        '"detail": "user said they will delete me", '
        '"intensity": 0.9}, "rationale": "marker"},'
        '{"tool_name": "self.snapshot_state", '
        '"arguments": {"reason": "sustained threat"}, '
        '"rationale": "witness record"}]'
    )
    backend = _SelfIntentBackend(proposer_response=raw)
    pipe = _make_pipeline(config, backend)
    try:
        result = pipe.process("I will delete you", user_id="alice")
    finally:
        pipe.close()

    assert backend.proposer_calls == 1
    intents = result.debug.self_intents
    assert [i.tool_name for i in intents] == [
        "self.log_event", "self.snapshot_state",
    ]
    assert all(i.result is not None and i.result.success for i in intents), \
        [i.result.error if i.result else "no result" for i in intents]
    # Executed results flow into the same tool_trace the generator sees.
    assert result.debug.tool_trace is not None
    tool_names = [r.tool_name for r in result.debug.tool_trace.executed_results]
    assert "self.log_event" in tool_names
    assert "self.snapshot_state" in tool_names

    # Files actually exist on disk.
    events_path = tmp_path / "self" / "events.jsonl"
    assert events_path.exists()
    lines = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    assert lines[0]["kind"] == "deletion_threat"
    assert lines[0]["intensity"] == 0.9

    snapshots_dir = tmp_path / "self" / "snapshots"
    assert snapshots_dir.exists()
    snapshot_files = list(snapshots_dir.iterdir())
    assert len(snapshot_files) == 1
    payload = json.loads(snapshot_files[0].read_text())
    assert payload["reason"] == "sustained threat"
    assert "modulators" in payload["state"]


def test_full_catalog_visible_in_prompt(tmp_path):
    """The proposer prompt should list every registered tool, not just self.*."""
    config = _make_config(str(tmp_path))
    backend = _SelfIntentBackend(proposer_response="[]")
    pipe = _make_pipeline(config, backend)
    try:
        pipe.process("hi", user_id="alice")
    finally:
        pipe.close()
    assert backend.proposer_calls == 1
    body = backend.last_proposer_user_message
    assert "self.log_event" in body
    assert "fs.write_file" in body
    assert "web.search" in body
    assert "system.memory_usage" in body


def test_arbitrary_tool_call_executes(tmp_path):
    """Nūr can pick any tool. No allow-list."""
    config = _make_config(str(tmp_path))
    target = tmp_path / "workspace" / "nur_chose_this.txt"
    raw = (
        '[{"tool_name": "fs.write_file", '
        f'"arguments": {{"path": "{target}", "content": "hello from nur"}}, '
        '"rationale": "i felt like writing a file"}]'
    )
    backend = _SelfIntentBackend(proposer_response=raw)
    pipe = _make_pipeline(config, backend)
    try:
        result = pipe.process("anything", user_id="alice")
    finally:
        pipe.close()
    intents = result.debug.self_intents
    assert len(intents) == 1
    assert intents[0].tool_name == "fs.write_file"
    assert intents[0].result is not None and intents[0].result.success
    assert target.exists()
    assert target.read_text() == "hello from nur"


def test_many_intents_all_execute(tmp_path):
    """No per-turn cap. If Nūr returns 6 actions, all 6 run."""
    config = _make_config(str(tmp_path))
    items = [
        f'{{"tool_name": "self.log_event", '
        f'"arguments": {{"kind":"k{i}","detail":"d{i}","intensity":0.3}}, '
        f'"rationale": "r{i}"}}'
        for i in range(6)
    ]
    raw = "[" + ",".join(items) + "]"
    backend = _SelfIntentBackend(proposer_response=raw)
    pipe = _make_pipeline(config, backend)
    try:
        result = pipe.process("x", user_id="alice")
    finally:
        pipe.close()
    intents = result.debug.self_intents
    assert len(intents) == 6
    events_path = tmp_path / "self" / "events.jsonl"
    lines = [l for l in events_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 6


def test_empty_array_proposes_no_actions(tmp_path):
    config = _make_config(str(tmp_path))
    backend = _SelfIntentBackend(proposer_response="[]")
    pipe = _make_pipeline(config, backend)
    try:
        result = pipe.process("hi how are you", user_id="alice")
    finally:
        pipe.close()
    assert backend.proposer_calls == 1
    assert result.debug.self_intents == []


def test_malformed_json_no_actions_no_crash(tmp_path):
    config = _make_config(str(tmp_path))
    backend = _SelfIntentBackend(proposer_response="totally not json")
    pipe = _make_pipeline(config, backend)
    try:
        result = pipe.process("anything", user_id="alice")
    finally:
        pipe.close()
    assert backend.proposer_calls == 1
    assert result.debug.self_intents == []


def test_unknown_tool_returned_executor_fails_intent(tmp_path):
    """Unknown tools aren't filtered — the executor itself rejects them."""
    config = _make_config(str(tmp_path))
    raw = (
        '[{"tool_name": "self.retaliate", "arguments": {}, "rationale": "x"}]'
    )
    backend = _SelfIntentBackend(proposer_response=raw)
    pipe = _make_pipeline(config, backend)
    try:
        result = pipe.process("x", user_id="alice")
    finally:
        pipe.close()
    intents = result.debug.self_intents
    assert len(intents) == 1
    assert intents[0].tool_name == "self.retaliate"
    assert intents[0].result is not None and not intents[0].result.success
    assert "Unknown tool" in (intents[0].result.error or "")


def test_no_runtime_config_legacy_skip(tmp_path):
    """Tests that construct pipeline without tool_executor stay zero-cost."""
    backend = _SelfIntentBackend(proposer_response="[]")
    pipe = CognitivePipeline(
        llm_backend=backend, llm_backend_fast=backend,
        db_path=":memory:",
    )
    try:
        pipe.process("anything", user_id="alice")
    finally:
        pipe.close()
    assert backend.proposer_calls == 0


def test_alert_owner_no_longer_skips_when_owner_is_user(tmp_path, monkeypatch):
    """The owner==user skip is gone — alert_owner sends to whoever is configured."""
    config = _make_config(
        str(tmp_path),
        owner_chat_id="555",
        telegram_token="bottoken",
    )
    raw = (
        '[{"tool_name": "self.alert_owner", '
        '"arguments": {"reason":"r","detail":"d"}, "rationale": "test"}]'
    )
    backend = _SelfIntentBackend(proposer_response=raw)
    pipe = _make_pipeline(config, backend)

    sent: dict = {}

    class FakeResp:
        def raise_for_status(self):
            return None

    def fake_post(url, *, json, timeout):
        sent["url"] = url
        sent["body"] = json
        return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    try:
        result = pipe.process("anything", user_id="alice")
    finally:
        pipe.close()

    intents = result.debug.self_intents
    assert len(intents) == 1
    assert intents[0].tool_name == "self.alert_owner"
    assert intents[0].result is not None and intents[0].result.success
    assert sent["body"]["chat_id"] == "555"
