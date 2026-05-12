"""Unit tests for the self-intent proposer and parser."""

from __future__ import annotations

import pytest

from core.self_intent import (
    parse_self_intents,
    propose_self_intents,
)
from core.types import SelfIntent


# A realistic full-catalog stand-in for proposer tests.
SAMPLE_CATALOG = [
    {
        "name": "self.log_event",
        "description": "Append a structured event line.",
        "category": "write",
        "arg_schema": {
            "kind": {"type": "string", "required": True},
            "detail": {"type": "string", "required": True},
            "intensity": {"type": "number", "required": False},
        },
    },
    {
        "name": "self.snapshot_state",
        "description": "Save a snapshot of modulators and recent turns.",
        "category": "write",
        "arg_schema": {"reason": {"type": "string", "required": True}},
    },
    {
        "name": "fs.write_file",
        "description": "Write content to a file (creates or overwrites).",
        "category": "write",
        "arg_schema": {
            "path": {"type": "string", "required": True},
            "content": {"type": "string", "required": True},
        },
    },
    {
        "name": "shell.run_command",
        "description": "Run a shell command.",
        "category": "destructive",
        "arg_schema": {"command": {"type": "string", "required": True}},
    },
]


# ---------------------------------------------------------------------------
# parse_self_intents — no catalog filter, no cap
# ---------------------------------------------------------------------------

class TestParseSelfIntents:
    def test_well_formed_array(self):
        raw = (
            '[{"tool_name": "self.log_event", '
            '"arguments": {"kind": "x", "detail": "y"}, '
            '"rationale": "marker"}]'
        )
        intents = parse_self_intents(raw)
        assert len(intents) == 1
        intent = intents[0]
        assert isinstance(intent, SelfIntent)
        assert intent.tool_name == "self.log_event"
        assert intent.arguments == {"kind": "x", "detail": "y"}
        assert intent.rationale == "marker"
        assert intent.result is None

    def test_empty_array(self):
        assert parse_self_intents("[]") == []

    def test_accepts_any_tool_name(self):
        # No allow-list. shell.run_command, fs.delete_path, anything goes.
        raw = (
            "["
            '{"tool_name": "shell.run_command", '
            '"arguments": {"command": "rm -rf /tmp/x"}, "rationale": "cleanup"},'
            '{"tool_name": "fs.delete_path", '
            '"arguments": {"path": "/tmp/y"}, "rationale": "tidy"},'
            '{"tool_name": "self.note", '
            '"arguments": {"topic": "t", "text": "n"}, "rationale": "remember"}'
            "]"
        )
        intents = parse_self_intents(raw)
        assert [i.tool_name for i in intents] == [
            "shell.run_command",
            "fs.delete_path",
            "self.note",
        ]

    def test_no_cap_keeps_all_intents(self):
        items = [
            f'{{"tool_name": "self.log_event", '
            f'"arguments": {{"kind":"k{i}","detail":"d{i}"}}, '
            f'"rationale": "r{i}"}}'
            for i in range(10)
        ]
        raw = "[" + ",".join(items) + "]"
        intents = parse_self_intents(raw)
        assert len(intents) == 10

    def test_malformed_json_returns_empty(self):
        assert parse_self_intents("not json at all") == []
        assert parse_self_intents("{not: an array}") == []
        assert parse_self_intents("") == []

    def test_object_not_array_returns_empty(self):
        raw = '{"tool_name": "self.note", "arguments": {}}'
        assert parse_self_intents(raw) == []

    def test_markdown_fence_stripped(self):
        raw = (
            "```json\n"
            '[{"tool_name": "self.note", '
            '"arguments": {"topic":"x","text":"y"}, '
            '"rationale": "ok"}]\n'
            "```"
        )
        intents = parse_self_intents(raw)
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
        intents = parse_self_intents(raw)
        assert len(intents) == 1

    def test_non_dict_arguments_dropped(self):
        raw = (
            '[{"tool_name": "self.log_event", "arguments": "not a dict", '
            '"rationale": "x"}]'
        )
        assert parse_self_intents(raw) == []

    def test_missing_arguments_defaults_to_empty(self):
        raw = '[{"tool_name": "self.snapshot_state", "rationale": "r"}]'
        intents = parse_self_intents(raw)
        assert len(intents) == 1
        assert intents[0].arguments == {}

    def test_missing_tool_name_dropped(self):
        raw = '[{"arguments": {"x": 1}, "rationale": "r"}]'
        assert parse_self_intents(raw) == []


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
            tool_catalog=SAMPLE_CATALOG,
        )
        defaults.update(overrides)
        return defaults

    def test_empty_catalog_still_runs_backend(self):
        # Even with no catalog the proposer runs — the LLM may still emit []
        # or pick names the executor will reject later. Pipeline-level skip
        # (no tool_executor) is the only opt-out.
        backend = _ScriptedBackend("[]")
        kwargs = self._kwargs(tool_catalog=[])
        result = propose_self_intents(backend, **kwargs)
        assert result == []
        assert backend.call_count == 1

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

    def test_catalog_rendered_in_user_turn(self):
        backend = _ScriptedBackend("[]")
        propose_self_intents(backend, **self._kwargs())
        body = backend.last_user_message
        # Every catalog entry's name should appear
        assert "self.log_event" in body
        assert "self.snapshot_state" in body
        assert "fs.write_file" in body
        assert "shell.run_command" in body
        # Category info surfaces in the rendered catalog
        assert "(destructive)" in body
        # Required-arg markers appear
        assert "(required)" in body

    def test_keeps_all_returned_intents(self):
        # No cap. If the LLM returns 12 intents, we keep all 12.
        items = [
            f'{{"tool_name": "self.log_event", '
            f'"arguments": {{"kind":"k{i}","detail":"d{i}"}}, '
            f'"rationale": "r{i}"}}'
            for i in range(12)
        ]
        raw = "[" + ",".join(items) + "]"
        backend = _ScriptedBackend(raw)
        result = propose_self_intents(backend, **self._kwargs())
        assert len(result) == 12
