"""End-to-end pipeline tests for the self-intent stage.

Constructs a real ``CognitivePipeline`` with a scripted LLM backend that
returns a self-intent JSON array on the proposer call and plain text on
all other calls. Verifies:

* the proposer call fires and intents are validated against the catalog,
* executed self-actions appear in ``debug.tool_trace.executed_results``,
* files are actually written under ``data/self/``,
* the stage is silently skipped when self_intent is disabled or no tools.
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

    def generate(self, system_prompt: str, user_message: str) -> str:
        # The self-intent system prompt contains a distinctive phrase from
        # config/prompts/self_intent.md; everything else is inner-dialogue
        # or generator output.
        if "JSON array of action objects" in system_prompt:
            self.proposer_calls += 1
            return self._proposer_response
        self.other_calls += 1
        return self._default_response


def _make_config(data_dir: str, **overrides) -> RuntimeConfig:
    base = dict(
        data_dir=data_dir,
        tools_enabled=True,
        tools_workspace=os.path.join(data_dir, "workspace"),
        autonomy_level="autonomous",
        self_intent_enabled=True,
        max_self_intents_per_turn=3,
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
    # tool_trace may exist from artifact path detection, but no self.* there.
    if result.debug.tool_trace is not None:
        names = [r.tool_name for r in result.debug.tool_trace.executed_results]
        assert not any(n.startswith("self.") for n in names)


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


def test_unknown_tool_names_dropped(tmp_path):
    config = _make_config(str(tmp_path))
    raw = (
        '[{"tool_name": "self.retaliate", "arguments": {}, "rationale": "x"},'
        '{"tool_name": "self.log_event", '
        '"arguments": {"kind":"k","detail":"d"}, "rationale": "k"}]'
    )
    backend = _SelfIntentBackend(proposer_response=raw)
    pipe = _make_pipeline(config, backend)
    try:
        result = pipe.process("x", user_id="alice")
    finally:
        pipe.close()
    assert [i.tool_name for i in result.debug.self_intents] == ["self.log_event"]


def test_disabled_in_config_skips_stage(tmp_path):
    config = _make_config(str(tmp_path), self_intent_enabled=False)
    backend = _SelfIntentBackend(proposer_response="[]")
    pipe = _make_pipeline(config, backend)
    try:
        pipe.process("anything", user_id="alice")
    finally:
        pipe.close()
    # The proposer LLM call should never have been issued.
    assert backend.proposer_calls == 0


def test_zero_budget_skips_stage(tmp_path):
    config = _make_config(str(tmp_path), max_self_intents_per_turn=0)
    backend = _SelfIntentBackend(proposer_response="[]")
    pipe = _make_pipeline(config, backend)
    try:
        pipe.process("anything", user_id="alice")
    finally:
        pipe.close()
    assert backend.proposer_calls == 0


def test_no_runtime_config_legacy_skip(tmp_path):
    """Tests that construct pipeline without runtime_config keep zero-cost path."""
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


def test_owner_alert_skipped_when_owner_is_user(tmp_path, monkeypatch):
    config = _make_config(
        str(tmp_path),
        owner_chat_id="555",
        telegram_token="bottoken",
    )
    # Force the session-key path so current_user_chat_id is 555.
    # The pipeline constructor doesn't know chat_id, but the executor's
    # context already has it from RuntimeConfig.owner_chat_id; the test
    # imitates SessionManager by setting it post-construction.
    raw = (
        '[{"tool_name": "self.alert_owner", '
        '"arguments": {"reason":"r","detail":"d"}, "rationale": "test"}]'
    )
    backend = _SelfIntentBackend(proposer_response=raw)
    pipe = _make_pipeline(config, backend)

    # Simulate the SessionManager wiring: owner IS the current user.
    ctx = getattr(pipe._tool_executor, "_self_action_context", None)
    assert ctx is not None
    ctx.current_user_chat_id = "555"

    def fail_post(*args, **kwargs):
        raise AssertionError("alert_owner should be skipped when owner == user")

    import httpx
    monkeypatch.setattr(httpx, "post", fail_post)

    try:
        result = pipe.process("anything", user_id="alice")
    finally:
        pipe.close()

    intents = result.debug.self_intents
    assert len(intents) == 1
    assert intents[0].tool_name == "self.alert_owner"
    assert intents[0].result is not None
    assert intents[0].result.success
    assert intents[0].result.metadata.get("skipped") == "owner_is_user"
