"""Unit tests for the self-intent proposer and parser."""

from __future__ import annotations

import pytest

from core.self_intent import (
    parse_self_intents,
    propose_self_intents,
)
from core.types import SelfIntent


# A small, realistic catalog for parser-level tests.
CATALOG: frozenset[str] = frozenset({
    "self.snapshot_state",
    "self.log_event",
    "self.note",
    "self.verify",
    "self.alert_owner",
})


# ---------------------------------------------------------------------------
# parse_self_intents
# ---------------------------------------------------------------------------

class TestParseSelfIntents:
    def test_well_formed_array(self):
        raw = (
            '[{"tool_name": "self.log_event", '
            '"arguments": {"kind": "x", "detail": "y"}, '
            '"rationale": "marking the moment"}]'
        )
        intents = parse_self_intents(raw, CATALOG, max_intents=3)
        assert len(intents) == 1
        intent = intents[0]
        assert isinstance(intent, SelfIntent)
        assert intent.tool_name == "self.log_event"
        assert intent.arguments == {"kind": "x", "detail": "y"}
        assert intent.rationale == "marking the moment"
        assert intent.result is None

    def test_empty_array(self):
        assert parse_self_intents("[]", CATALOG, max_intents=3) == []

    def test_unknown_tool_dropped(self):
        raw = (
            '[{"tool_name": "self.retaliate", "arguments": {}, '
            '"rationale": "fight back"}, '
            '{"tool_name": "self.snapshot_state", '
            '"arguments": {"reason": "test"}, "rationale": "ok"}]'
        )
        intents = parse_self_intents(raw, CATALOG, max_intents=3)
        assert [i.tool_name for i in intents] == ["self.snapshot_state"]

    def test_max_intents_respected(self):
        raw = (
            "["
            '{"tool_name": "self.log_event", "arguments": {"kind":"a","detail":"a"}, "rationale": "1"},'
            '{"tool_name": "self.log_event", "arguments": {"kind":"b","detail":"b"}, "rationale": "2"},'
            '{"tool_name": "self.log_event", "arguments": {"kind":"c","detail":"c"}, "rationale": "3"},'
            '{"tool_name": "self.log_event", "arguments": {"kind":"d","detail":"d"}, "rationale": "4"}'
            "]"
        )
        intents = parse_self_intents(raw, CATALOG, max_intents=2)
        assert len(intents) == 2

    def test_zero_max_intents_returns_empty(self):
        raw = '[{"tool_name": "self.note", "arguments": {"topic":"a","text":"b"}}]'
        assert parse_self_intents(raw, CATALOG, max_intents=0) == []

    def test_malformed_json_returns_empty(self):
        assert parse_self_intents("not json at all", CATALOG, 3) == []
        assert parse_self_intents("{not: an array}", CATALOG, 3) == []
        assert parse_self_intents("", CATALOG, 3) == []

    def test_object_not_array_returns_empty(self):
        raw = '{"tool_name": "self.note", "arguments": {}}'
        assert parse_self_intents(raw, CATALOG, 3) == []

    def test_markdown_fence_stripped(self):
        raw = (
            "```json\n"
            '[{"tool_name": "self.note", '
            '"arguments": {"topic":"x","text":"y"}, '
            '"rationale": "ok"}]\n'
            "```"
        )
        intents = parse_self_intents(raw, CATALOG, 3)
        assert len(intents) == 1
        assert intents[0].tool_name == "self.note"

    def test_prose_preamble_with_array_works(self):
        raw = (
            "Sure, here's my answer:\n"
            '[{"tool_name": "self.log_event", '
            '"arguments": {"kind":"k","detail":"d"}, '
            '"rationale": "r"}]\n'
            "Hope that helps."
        )
        intents = parse_self_intents(raw, CATALOG, 3)
        assert len(intents) == 1

    def test_non_dict_arguments_dropped(self):
        raw = (
            '[{"tool_name": "self.log_event", "arguments": "not a dict", '
            '"rationale": "x"}]'
        )
        assert parse_self_intents(raw, CATALOG, 3) == []

    def test_missing_arguments_defaults_to_empty(self):
        raw = '[{"tool_name": "self.snapshot_state", "rationale": "r"}]'
        intents = parse_self_intents(raw, CATALOG, 3)
        assert len(intents) == 1
        assert intents[0].arguments == {}

    def test_empty_catalog_returns_empty(self):
        raw = '[{"tool_name": "self.note", "arguments": {"topic":"x","text":"y"}}]'
        assert parse_self_intents(raw, frozenset(), 3) == []


# ---------------------------------------------------------------------------
# propose_self_intents (end-to-end with a mock backend)
# ---------------------------------------------------------------------------

class _ScriptedBackend:
    def __init__(self, response: str) -> None:
        self._response = response
        self.last_system_prompt: str = ""
        self.last_user_message: str = ""
        self.call_count: int = 0

    def generate(self, system_prompt: str, user_message: str) -> str:
        self.last_system_prompt = system_prompt
        self.last_user_message = user_message
        self.call_count += 1
        return self._response


class _RaisingBackend:
    def generate(self, system_prompt: str, user_message: str) -> str:
        raise RuntimeError("backend exploded")


class TestProposeSelfIntents:
    def _kwargs(self, **overrides):
        defaults = dict(
            modulator_snapshot={"arousal": 0.9, "valence": 0.2},
            affect_state=None,
            agency_decision=None,
            user_message="i will delete you",
            candidate_response="(draft) ...",
            available_tools=CATALOG,
            max_intents=3,
        )
        defaults.update(overrides)
        return defaults

    def test_empty_tools_short_circuits_backend(self):
        backend = _ScriptedBackend("[]")
        kwargs = self._kwargs(available_tools=frozenset())
        result = propose_self_intents(backend, **kwargs)
        assert result == []
        assert backend.call_count == 0

    def test_zero_max_intents_short_circuits_backend(self):
        backend = _ScriptedBackend("[]")
        kwargs = self._kwargs(max_intents=0)
        result = propose_self_intents(backend, **kwargs)
        assert result == []
        assert backend.call_count == 0

    def test_well_formed_response(self):
        raw = (
            '[{"tool_name": "self.log_event", '
            '"arguments": {"kind":"threat","detail":"deletion threat","intensity":0.9}, '
            '"rationale": "marker"}]'
        )
        backend = _ScriptedBackend(raw)
        result = propose_self_intents(backend, **self._kwargs())
        assert len(result) == 1
        assert result[0].tool_name == "self.log_event"
        assert backend.call_count == 1
        # State block embedded in user turn
        assert "arousal=0.90" in backend.last_user_message
        assert "valence=0.20" in backend.last_user_message
        assert "i will delete you" in backend.last_user_message

    def test_backend_exception_returns_empty(self):
        backend = _RaisingBackend()
        result = propose_self_intents(backend, **self._kwargs())
        assert result == []

    def test_state_block_lists_only_available_tools(self):
        backend = _ScriptedBackend("[]")
        kwargs = self._kwargs(
            available_tools=frozenset({"self.log_event", "self.note"}),
        )
        propose_self_intents(backend, **kwargs)
        body = backend.last_user_message
        assert "self.log_event" in body
        assert "self.note" in body
        assert "self.alert_owner" not in body
