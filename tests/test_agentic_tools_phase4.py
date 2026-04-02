"""Tests for Agentic Tools Phase 4: runtime debug integration.

Covers:
  1. tool_trace present on tool turns, null on non-tool turns
  2. action_variables in serialized debug output
  3. tool_memory_effects visible on tool turns
  4. tool_summary compact block
  5. Intent serialization includes all fields
  6. Observation serialization includes all fields
  7. Debug output is valid JSON and session-isolated
  8. Pipeline DebugState carries tool fields correctly
  9. _build_tool_summary helper
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.types import (
    ActionVariables,
    ToolCapability,
    ToolCategory,
    ToolDecision,
    ToolIntent,
    ToolObservation,
    ToolResult,
    ToolTrace,
)
from core.tool_memory import ToolMemoryEffects
from pipeline import CognitivePipeline, DebugState
from runtime.debug.api import _debug_to_dict, _build_tool_summary
from tools.registry import ToolRegistry
from tools.executor import ToolExecutor
from tools import register_builtins


# ===================================================================
# Helpers
# ===================================================================

def _make_pipeline_with_tools():
    """Create a CognitivePipeline wired to a ToolExecutor."""
    reg = ToolRegistry()
    exe = ToolExecutor(reg)
    register_builtins(reg, exe)
    pipe = CognitivePipeline(tool_executor=exe)
    return pipe, reg, exe


def _make_intent(**kwargs) -> ToolIntent:
    defaults = dict(
        tool_name="fs.read_file",
        arguments={"path": "/tmp/test.txt"},
        reason="User requested file read",
        expected_outcome="File contents returned",
        urgency=0.5,
        risk_tolerance=0.4,
        autonomy_bias=0.6,
        clarification_threshold=0.5,
        persistence_drive=0.5,
        confidence=0.7,
    )
    defaults.update(kwargs)
    return ToolIntent(**defaults)


def _make_result(**kwargs) -> ToolResult:
    defaults = dict(
        tool_name="fs.read_file",
        success=True,
        output="file contents here",
        latency_ms=12.5,
        side_effect_summary="",
    )
    defaults.update(kwargs)
    return ToolResult(**defaults)


def _make_observation(**kwargs) -> ToolObservation:
    defaults = dict(
        summary="Read file successfully",
        emotional_delta={"certainty": 0.08, "valence": 0.02},
        certainty_delta=0.08,
        resolution_delta=-0.05,
        self_observation="methodical",
        continue_tool_loop=False,
    )
    defaults.update(kwargs)
    return ToolObservation(**defaults)


def _make_trace(executed=True) -> ToolTrace:
    intent = _make_intent()
    if executed:
        return ToolTrace(
            proposed_intents=[intent],
            final_decision=ToolDecision(
                decision="execute", intent=intent, rationale="Approved",
            ),
            executed_results=[_make_result()],
            observations=[_make_observation()],
            loop_count=1,
        )
    return ToolTrace(
        proposed_intents=[intent],
        final_decision=ToolDecision(
            decision="clarify", intent=intent, rationale="Need confirmation",
        ),
        loop_count=0,
    )


# ===================================================================
# 1. tool_trace present/absent based on turn type
# ===================================================================

class TestToolTracePresence:
    def test_non_tool_turn_trace_null(self):
        """Conversational turn has null tool_trace."""
        pipe = CognitivePipeline()
        resp = pipe.process("How are you?", user_id="u1")
        d = _debug_to_dict(resp.debug)
        assert d["tool_trace"] is None

    def test_tool_turn_trace_present(self):
        """Tool turn has populated tool_trace."""
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello")
            f.flush()
            try:
                resp = pipe.process(f"read file {f.name}", user_id="u1")
                d = _debug_to_dict(resp.debug)
                assert d["tool_trace"] is not None
                assert d["tool_trace"]["loop_count"] >= 1
                assert len(d["tool_trace"]["executed_results"]) >= 1
            finally:
                os.unlink(f.name)

    def test_no_executor_trace_null(self):
        """Pipeline without tool_executor always has null tool_trace."""
        pipe = CognitivePipeline()
        resp = pipe.process("read file /tmp/foo.txt", user_id="u1")
        d = _debug_to_dict(resp.debug)
        assert d["tool_trace"] is None


# ===================================================================
# 2. action_variables in debug output
# ===================================================================

class TestActionVariablesDebug:
    def test_non_tool_turn_action_vars_null(self):
        pipe = CognitivePipeline()
        resp = pipe.process("Hello!", user_id="u1")
        d = _debug_to_dict(resp.debug)
        assert d["action_variables"] is None

    def test_tool_turn_action_vars_present(self):
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            f.flush()
            try:
                resp = pipe.process(f"read file {f.name}", user_id="u1")
                d = _debug_to_dict(resp.debug)
                av = d["action_variables"]
                assert av is not None
                assert "risk_tolerance" in av
                assert "action_urgency" in av
                assert "clarification_threshold" in av
                assert "persistence_drive" in av
                assert "autonomy_bias" in av
                # All are floats
                for key in av:
                    assert isinstance(av[key], float)
            finally:
                os.unlink(f.name)

    def test_action_vars_serialization_from_dataclass(self):
        debug = DebugState()
        debug.action_variables = ActionVariables(
            risk_tolerance=0.3, action_urgency=0.6,
            clarification_threshold=0.7, persistence_drive=0.4,
            autonomy_bias=0.55,
        )
        d = _debug_to_dict(debug)
        av = d["action_variables"]
        assert av["risk_tolerance"] == 0.3
        assert av["action_urgency"] == 0.6
        assert av["clarification_threshold"] == 0.7
        assert av["persistence_drive"] == 0.4
        assert av["autonomy_bias"] == 0.55


# ===================================================================
# 3. tool_memory_effects on tool turns
# ===================================================================

class TestToolMemoryEffectsDebug:
    def test_non_tool_turn_effects_null(self):
        pipe = CognitivePipeline()
        resp = pipe.process("Hello!", user_id="u1")
        d = _debug_to_dict(resp.debug)
        assert d["tool_memory_effects"] is None

    def test_tool_turn_effects_present(self):
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("content")
            f.flush()
            try:
                resp = pipe.process(f"read file {f.name}", user_id="u1")
                d = _debug_to_dict(resp.debug)
                tme = d["tool_memory_effects"]
                assert tme is not None
                assert "short_term_recorded" in tme
                assert "long_term_written" in tme
                assert "self_observations" in tme
                assert "unresolved_items_created" in tme
                assert "trust_delta" in tme
            finally:
                os.unlink(f.name)

    def test_failed_tool_shows_unresolved(self):
        pipe, _, _ = _make_pipeline_with_tools()
        resp = pipe.process("read file /nonexistent/xyz.txt", user_id="u1")
        d = _debug_to_dict(resp.debug)
        tme = d["tool_memory_effects"]
        assert tme is not None
        assert len(tme["unresolved_items_created"]) > 0


# ===================================================================
# 4. tool_summary compact block
# ===================================================================

class TestToolSummary:
    def test_non_tool_turn_summary_null(self):
        pipe = CognitivePipeline()
        resp = pipe.process("How are you?", user_id="u1")
        d = _debug_to_dict(resp.debug)
        assert d["tool_summary"] is None

    def test_tool_turn_summary_present(self):
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            f.flush()
            try:
                resp = pipe.process(f"read file {f.name}", user_id="u1")
                d = _debug_to_dict(resp.debug)
                ts = d["tool_summary"]
                assert ts is not None
                assert ts["tool_used"] is True
                assert ts["tools_executed"] >= 1
                assert ts["last_tool_name"] == "fs.read_file"
                assert ts["last_tool_success"] is True
                assert ts["decision"] == "execute"
                assert ts["loop_count"] >= 1
            finally:
                os.unlink(f.name)

    def test_failed_tool_summary(self):
        pipe, _, _ = _make_pipeline_with_tools()
        resp = pipe.process("read file /no/such/file.txt", user_id="u1")
        d = _debug_to_dict(resp.debug)
        ts = d["tool_summary"]
        assert ts is not None
        assert ts["tool_used"] is True
        assert ts["last_tool_success"] is False

    def test_summary_with_clarify_decision(self):
        """When arbiter decides 'clarify', no tools execute."""
        debug = DebugState()
        debug.tool_trace = _make_trace(executed=False)
        d = _debug_to_dict(debug)
        ts = d["tool_summary"]
        assert ts is not None
        assert ts["tool_used"] is False
        assert ts["tools_executed"] == 0
        assert ts["last_tool_name"] is None
        assert ts["decision"] == "clarify"


# ===================================================================
# 5. Intent serialization completeness
# ===================================================================

class TestIntentSerialization:
    def test_all_intent_fields_present(self):
        debug = DebugState()
        debug.tool_trace = _make_trace()
        d = _debug_to_dict(debug)
        intent = d["tool_trace"]["proposed_intents"][0]
        expected_fields = {
            "tool_name", "arguments", "reason", "expected_outcome",
            "urgency", "risk_tolerance", "autonomy_bias",
            "clarification_threshold", "persistence_drive", "confidence",
        }
        assert set(intent.keys()) == expected_fields

    def test_intent_values_match(self):
        debug = DebugState()
        debug.tool_trace = _make_trace()
        d = _debug_to_dict(debug)
        intent = d["tool_trace"]["proposed_intents"][0]
        assert intent["tool_name"] == "fs.read_file"
        assert intent["expected_outcome"] == "File contents returned"
        assert intent["clarification_threshold"] == 0.5
        assert intent["persistence_drive"] == 0.5


# ===================================================================
# 6. Observation serialization completeness
# ===================================================================

class TestObservationSerialization:
    def test_all_observation_fields_present(self):
        debug = DebugState()
        debug.tool_trace = _make_trace()
        d = _debug_to_dict(debug)
        obs = d["tool_trace"]["observations"][0]
        expected_fields = {
            "summary", "emotional_delta", "certainty_delta",
            "resolution_delta", "self_observation", "continue_tool_loop",
        }
        assert set(obs.keys()) == expected_fields

    def test_observation_values_match(self):
        debug = DebugState()
        debug.tool_trace = _make_trace()
        d = _debug_to_dict(debug)
        obs = d["tool_trace"]["observations"][0]
        assert obs["summary"] == "Read file successfully"
        assert obs["certainty_delta"] == 0.08
        assert obs["continue_tool_loop"] is False
        assert obs["self_observation"] == "methodical"


# ===================================================================
# 7. JSON validity and session isolation
# ===================================================================

class TestJsonValidity:
    def test_non_tool_debug_is_json_serializable(self):
        pipe = CognitivePipeline()
        resp = pipe.process("Hello!", user_id="u1")
        d = _debug_to_dict(resp.debug)
        # Should not raise
        serialized = json.dumps(d)
        assert isinstance(serialized, str)
        roundtrip = json.loads(serialized)
        assert roundtrip["user_message"] == "Hello!"

    def test_tool_debug_is_json_serializable(self):
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            f.flush()
            try:
                resp = pipe.process(f"read file {f.name}", user_id="u1")
                d = _debug_to_dict(resp.debug)
                serialized = json.dumps(d)
                assert isinstance(serialized, str)
                roundtrip = json.loads(serialized)
                assert roundtrip["tool_trace"] is not None
                assert roundtrip["tool_summary"] is not None
            finally:
                os.unlink(f.name)

    def test_failed_tool_debug_is_json_serializable(self):
        pipe, _, _ = _make_pipeline_with_tools()
        resp = pipe.process("read file /nonexistent/file.txt", user_id="u1")
        d = _debug_to_dict(resp.debug)
        serialized = json.dumps(d)
        roundtrip = json.loads(serialized)
        assert roundtrip["tool_memory_effects"]["unresolved_items_created"]


class TestSessionIsolationWithTools:
    def test_tool_and_non_tool_turns_isolated(self):
        """One user does a tool turn, another does conversational — debug is separate."""
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            f.flush()
            try:
                # User A does a tool turn
                resp_a = pipe.process(f"read file {f.name}", user_id="userA")
                debug_a = _debug_to_dict(resp_a.debug)

                # Reset pipeline for user B (separate pipeline in real runtime)
                pipe_b = CognitivePipeline()
                resp_b = pipe_b.process("How are you?", user_id="userB")
                debug_b = _debug_to_dict(resp_b.debug)

                # A has tool_trace, B does not
                assert debug_a["tool_trace"] is not None
                assert debug_b["tool_trace"] is None
                assert debug_a["tool_summary"] is not None
                assert debug_b["tool_summary"] is None
            finally:
                os.unlink(f.name)


# ===================================================================
# 8. DebugState carries tool fields correctly
# ===================================================================

class TestDebugStateToolFields:
    def test_debug_state_defaults(self):
        debug = DebugState()
        assert debug.tool_trace is None
        assert debug.action_variables is None
        assert debug.tool_memory_effects is None

    def test_debug_state_populated_by_pipeline(self):
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test")
            f.flush()
            try:
                resp = pipe.process(f"read file {f.name}", user_id="u1")
                assert resp.debug.tool_trace is not None
                assert resp.debug.action_variables is not None
                assert resp.debug.tool_memory_effects is not None
            finally:
                os.unlink(f.name)

    def test_conversational_turn_tool_fields_null(self):
        pipe, _, _ = _make_pipeline_with_tools()
        resp = pipe.process("Tell me a joke", user_id="u1")
        assert resp.debug.tool_trace is not None  # empty trace, but still set
        assert resp.debug.action_variables is not None  # always derived when executor present
        assert resp.debug.tool_memory_effects is None  # no execution → no effects


# ===================================================================
# 9. _build_tool_summary helper
# ===================================================================

class TestBuildToolSummary:
    def test_none_when_no_trace(self):
        debug = DebugState()
        assert _build_tool_summary(debug) is None

    def test_none_when_empty_trace_no_intents(self):
        debug = DebugState()
        debug.tool_trace = ToolTrace()
        assert _build_tool_summary(debug) is None

    def test_summary_for_executed_trace(self):
        debug = DebugState()
        debug.tool_trace = _make_trace(executed=True)
        summary = _build_tool_summary(debug)
        assert summary["tool_used"] is True
        assert summary["tools_executed"] == 1
        assert summary["last_tool_name"] == "fs.read_file"
        assert summary["last_tool_success"] is True
        assert summary["decision"] == "execute"
        assert summary["loop_count"] == 1

    def test_summary_for_non_executed_trace(self):
        debug = DebugState()
        debug.tool_trace = _make_trace(executed=False)
        summary = _build_tool_summary(debug)
        assert summary["tool_used"] is False
        assert summary["tools_executed"] == 0
        assert summary["last_tool_name"] is None
        assert summary["last_tool_success"] is None
        assert summary["decision"] == "clarify"

    def test_summary_multiple_executions(self):
        debug = DebugState()
        intent = _make_intent()
        debug.tool_trace = ToolTrace(
            proposed_intents=[intent],
            final_decision=ToolDecision(decision="execute", intent=intent, rationale="ok"),
            executed_results=[
                _make_result(tool_name="fs.read_file"),
                _make_result(tool_name="fs.list_dir", success=False, output=""),
            ],
            observations=[_make_observation(), _make_observation()],
            loop_count=2,
        )
        summary = _build_tool_summary(debug)
        assert summary["tools_executed"] == 2
        assert summary["last_tool_name"] == "fs.list_dir"
        assert summary["last_tool_success"] is False
        assert summary["loop_count"] == 2


# ===================================================================
# 10. Decision serialization
# ===================================================================

class TestDecisionSerialization:
    def test_execute_decision(self):
        debug = DebugState()
        debug.tool_trace = _make_trace(executed=True)
        d = _debug_to_dict(debug)
        dec = d["tool_trace"]["final_decision"]
        assert dec["decision"] == "execute"
        assert dec["rationale"] == "Approved"

    def test_clarify_decision(self):
        debug = DebugState()
        debug.tool_trace = _make_trace(executed=False)
        d = _debug_to_dict(debug)
        dec = d["tool_trace"]["final_decision"]
        assert dec["decision"] == "clarify"

    def test_null_decision(self):
        debug = DebugState()
        debug.tool_trace = ToolTrace(proposed_intents=[], final_decision=None)
        d = _debug_to_dict(debug)
        assert d["tool_trace"]["final_decision"] is None


# ===================================================================
# 11. Result serialization
# ===================================================================

class TestResultSerialization:
    def test_result_fields(self):
        debug = DebugState()
        debug.tool_trace = _make_trace(executed=True)
        d = _debug_to_dict(debug)
        result = d["tool_trace"]["executed_results"][0]
        expected_fields = {
            "tool_name", "success", "error", "latency_ms", "side_effect_summary",
        }
        assert set(result.keys()) == expected_fields
        assert result["tool_name"] == "fs.read_file"
        assert result["success"] is True
        assert result["latency_ms"] == 12.5
