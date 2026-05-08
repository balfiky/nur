"""Tests for Agentic Tools Phase 0: types, action variables, registry, debug compat."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from core.types import (
    ActionVariables,
    ModulatorState,
    ToolCapability,
    ToolCategory,
    ToolDecision,
    ToolIntent,
    ToolObservation,
    ToolResult,
    ToolTrace,
    UnresolvedItem,
)
from core.action_variables import derive_action_variables
from nur_tools.registry import ToolRegistry
from pipeline import DebugState
from tests._fakes import MockLLMBackend


# ===================================================================
# Type construction and defaults
# ===================================================================

class TestToolCategory:
    def test_enum_values(self):
        assert ToolCategory.READ_ONLY.value == "read_only"
        assert ToolCategory.WRITE.value == "write"
        assert ToolCategory.DESTRUCTIVE.value == "destructive"
        assert ToolCategory.EXTERNAL_ACTION.value == "external_action"
        assert ToolCategory.COGNITIVE.value == "cognitive"

    def test_string_enum(self):
        assert isinstance(ToolCategory.READ_ONLY, str)
        assert ToolCategory("read_only") == ToolCategory.READ_ONLY


class TestToolCapability:
    def test_minimal_construction(self):
        cap = ToolCapability(
            name="test_tool",
            description="A test tool",
            category=ToolCategory.READ_ONLY,
        )
        assert cap.name == "test_tool"
        assert cap.category == ToolCategory.READ_ONLY
        assert cap.arg_schema == {}
        assert cap.supports_streaming is False
        assert cap.requires_network is False
        assert cap.mcp_backed is False

    def test_full_construction(self):
        cap = ToolCapability(
            name="web_search",
            description="Search the web",
            category=ToolCategory.EXTERNAL_ACTION,
            arg_schema={"query": {"type": "string"}, "limit": {"type": "int"}},
            supports_streaming=True,
            requires_network=True,
            mcp_backed=True,
        )
        assert cap.requires_network is True
        assert cap.mcp_backed is True
        assert "query" in cap.arg_schema


class TestToolIntent:
    def test_defaults(self):
        intent = ToolIntent(
            tool_name="fs.read_file",
            arguments={"path": "/tmp/x"},
            reason="User asked to read",
            expected_outcome="File contents",
        )
        assert intent.urgency == 0.5
        assert intent.risk_tolerance == 0.5
        assert intent.autonomy_bias == 0.5
        assert intent.clarification_threshold == 0.5
        assert intent.persistence_drive == 0.5
        assert intent.confidence == 0.5

    def test_custom_values(self):
        intent = ToolIntent(
            tool_name="shell.run",
            arguments={"cmd": "ls"},
            reason="Check files",
            expected_outcome="File listing",
            urgency=0.9,
            risk_tolerance=0.2,
            confidence=0.8,
        )
        assert intent.urgency == 0.9
        assert intent.risk_tolerance == 0.2


class TestToolDecision:
    def test_execute(self):
        d = ToolDecision(decision="execute", rationale="Safe read")
        assert d.decision == "execute"
        assert d.intent is None

    def test_clarify(self):
        d = ToolDecision(decision="clarify", rationale="Ambiguous target")
        assert d.decision == "clarify"

    def test_with_intent(self):
        intent = ToolIntent(
            tool_name="t", arguments={}, reason="r", expected_outcome="o",
        )
        d = ToolDecision(decision="execute", intent=intent, rationale="go")
        assert d.intent is intent


class TestToolResult:
    def test_success(self):
        r = ToolResult(tool_name="fs.read_file", success=True, output="hello")
        assert r.success is True
        assert r.error is None
        assert r.metadata == {}
        assert r.latency_ms == 0.0

    def test_failure(self):
        r = ToolResult(
            tool_name="shell.run",
            success=False,
            output="",
            error="Permission denied",
            latency_ms=42.5,
        )
        assert r.success is False
        assert r.error == "Permission denied"


class TestToolObservation:
    def test_defaults(self):
        obs = ToolObservation(summary="Read succeeded")
        assert obs.emotional_delta == {}
        assert obs.certainty_delta == 0.0
        assert obs.resolution_delta == 0.0
        assert obs.self_observation is None
        assert obs.unresolved_item is None
        assert obs.continue_tool_loop is False

    def test_with_unresolved(self):
        item = UnresolvedItem(
            id="tool_fail_1",
            source="tool_failure",
            description="Shell command failed",
            created_at=datetime.now(timezone.utc),
            intensity=0.6,
            decay_rate=0.05,
        )
        obs = ToolObservation(
            summary="Command failed",
            certainty_delta=-0.1,
            resolution_delta=0.1,
            unresolved_item=item,
            continue_tool_loop=False,
        )
        assert obs.unresolved_item is not None
        assert obs.unresolved_item.source == "tool_failure"


class TestToolTrace:
    def test_empty(self):
        trace = ToolTrace()
        assert trace.proposed_intents == []
        assert trace.final_decision is None
        assert trace.executed_results == []
        assert trace.observations == []
        assert trace.loop_count == 0

    def test_populated(self):
        intent = ToolIntent(
            tool_name="fs.read_file",
            arguments={"path": "/x"},
            reason="r",
            expected_outcome="o",
        )
        result = ToolResult(tool_name="fs.read_file", success=True, output="data")
        trace = ToolTrace(
            proposed_intents=[intent],
            final_decision=ToolDecision(decision="execute", intent=intent, rationale="ok"),
            executed_results=[result],
            observations=[ToolObservation(summary="Read ok")],
            loop_count=1,
        )
        assert len(trace.proposed_intents) == 1
        assert trace.loop_count == 1


class TestActionVariables:
    def test_defaults(self):
        av = ActionVariables()
        assert av.risk_tolerance == 0.5
        assert av.action_urgency == 0.3
        assert av.clarification_threshold == 0.5
        assert av.persistence_drive == 0.5
        assert av.autonomy_bias == 0.5

    def test_clamping(self):
        av = ActionVariables(risk_tolerance=1.5, action_urgency=-0.3)
        assert av.risk_tolerance == 1.0
        assert av.action_urgency == 0.0


# ===================================================================
# Action-variable derivation
# ===================================================================

class TestDeriveActionVariables:
    def test_neutral_state(self):
        """Neutral modulators + default trust → close to defaults."""
        state = ModulatorState()  # all 0.5, energy 1.0, resolution 0.0
        av = derive_action_variables(state)
        assert 0.4 <= av.risk_tolerance <= 0.6
        assert 0.2 <= av.action_urgency <= 0.5
        assert 0.4 <= av.clarification_threshold <= 0.6
        assert 0.4 <= av.persistence_drive <= 0.7
        assert 0.4 <= av.autonomy_bias <= 0.6

    def test_high_arousal_increases_urgency(self):
        neutral = derive_action_variables(ModulatorState())
        high_arousal = derive_action_variables(ModulatorState(arousal=0.9))
        assert high_arousal.action_urgency > neutral.action_urgency

    def test_high_arousal_decreases_clarification(self):
        neutral = derive_action_variables(ModulatorState())
        high_arousal = derive_action_variables(ModulatorState(arousal=0.9))
        assert high_arousal.clarification_threshold < neutral.clarification_threshold

    def test_low_certainty_lowers_autonomy(self):
        neutral = derive_action_variables(ModulatorState())
        low_cert = derive_action_variables(ModulatorState(certainty=0.1))
        assert low_cert.autonomy_bias < neutral.autonomy_bias

    def test_low_certainty_raises_clarification(self):
        neutral = derive_action_variables(ModulatorState())
        low_cert = derive_action_variables(ModulatorState(certainty=0.1))
        assert low_cert.clarification_threshold > neutral.clarification_threshold

    def test_low_energy_lowers_persistence(self):
        neutral = derive_action_variables(ModulatorState())
        tired = derive_action_variables(ModulatorState(energy=0.1))
        assert tired.persistence_drive < neutral.persistence_drive

    def test_low_energy_lowers_urgency(self):
        neutral = derive_action_variables(ModulatorState())
        tired = derive_action_variables(ModulatorState(energy=0.1))
        assert tired.action_urgency < neutral.action_urgency

    def test_high_resolution_increases_persistence(self):
        neutral = derive_action_variables(ModulatorState(resolution=0.0))
        unfinished = derive_action_variables(ModulatorState(resolution=0.8))
        assert unfinished.persistence_drive > neutral.persistence_drive

    def test_high_resolution_increases_urgency(self):
        neutral = derive_action_variables(ModulatorState(resolution=0.0))
        unfinished = derive_action_variables(ModulatorState(resolution=0.8))
        assert unfinished.action_urgency > neutral.action_urgency

    def test_high_trust_increases_risk_tolerance(self):
        low_trust = derive_action_variables(ModulatorState(), trust=0.2)
        high_trust = derive_action_variables(ModulatorState(), trust=0.9)
        assert high_trust.risk_tolerance > low_trust.risk_tolerance

    def test_high_trust_increases_autonomy(self):
        low_trust = derive_action_variables(ModulatorState(), trust=0.2)
        high_trust = derive_action_variables(ModulatorState(), trust=0.9)
        assert high_trust.autonomy_bias > low_trust.autonomy_bias

    def test_defense_lowers_risk_tolerance(self):
        no_def = derive_action_variables(ModulatorState())
        with_def = derive_action_variables(ModulatorState(), defense_active=True)
        assert with_def.risk_tolerance < no_def.risk_tolerance

    def test_defense_lowers_autonomy(self):
        no_def = derive_action_variables(ModulatorState())
        with_def = derive_action_variables(ModulatorState(), defense_active=True)
        assert with_def.autonomy_bias < no_def.autonomy_bias

    def test_defense_lowers_persistence(self):
        no_def = derive_action_variables(ModulatorState())
        with_def = derive_action_variables(ModulatorState(), defense_active=True)
        assert with_def.persistence_drive < no_def.persistence_drive

    def test_all_values_clamped(self):
        """Extreme inputs still produce values in [0, 1]."""
        extreme = ModulatorState(
            arousal=1.0, valence=0.0, certainty=0.0,
            bonding=0.0, energy=0.0, resolution=1.0,
        )
        av = derive_action_variables(extreme, trust=0.0, defense_active=True)
        for attr in (
            "risk_tolerance", "action_urgency", "clarification_threshold",
            "persistence_drive", "autonomy_bias",
        ):
            val = getattr(av, attr)
            assert 0.0 <= val <= 1.0, f"{attr}={val} out of range"

    def test_opposite_extreme(self):
        extreme = ModulatorState(
            arousal=0.0, valence=1.0, certainty=1.0,
            bonding=1.0, energy=1.0, resolution=0.0,
        )
        av = derive_action_variables(extreme, trust=1.0, defense_active=False)
        for attr in (
            "risk_tolerance", "action_urgency", "clarification_threshold",
            "persistence_drive", "autonomy_bias",
        ):
            val = getattr(av, attr)
            assert 0.0 <= val <= 1.0, f"{attr}={val} out of range"


# ===================================================================
# Tool registry
# ===================================================================

class TestToolRegistry:
    def _make_cap(self, name: str, cat: ToolCategory = ToolCategory.READ_ONLY) -> ToolCapability:
        return ToolCapability(name=name, description=f"{name} desc", category=cat)

    def test_empty_registry(self):
        reg = ToolRegistry()
        assert len(reg) == 0
        assert reg.list_tools() == []
        assert reg.names() == []

    def test_register_and_get(self):
        reg = ToolRegistry()
        cap = self._make_cap("fs.read_file")
        reg.register(cap)
        assert reg.get("fs.read_file") is cap
        assert len(reg) == 1

    def test_get_missing(self):
        reg = ToolRegistry()
        assert reg.get("nonexistent") is None

    def test_contains(self):
        reg = ToolRegistry()
        reg.register(self._make_cap("fs.read_file"))
        assert "fs.read_file" in reg
        assert "missing" not in reg

    def test_list_tools_all(self):
        reg = ToolRegistry()
        reg.register(self._make_cap("a", ToolCategory.READ_ONLY))
        reg.register(self._make_cap("b", ToolCategory.WRITE))
        reg.register(self._make_cap("c", ToolCategory.DESTRUCTIVE))
        assert len(reg.list_tools()) == 3

    def test_list_tools_by_category(self):
        reg = ToolRegistry()
        reg.register(self._make_cap("a", ToolCategory.READ_ONLY))
        reg.register(self._make_cap("b", ToolCategory.WRITE))
        reg.register(self._make_cap("c", ToolCategory.READ_ONLY))
        read_only = reg.list_tools(category=ToolCategory.READ_ONLY)
        assert len(read_only) == 2
        assert all(t.category == ToolCategory.READ_ONLY for t in read_only)

    def test_list_tools_empty_category(self):
        reg = ToolRegistry()
        reg.register(self._make_cap("a", ToolCategory.READ_ONLY))
        assert reg.list_tools(category=ToolCategory.DESTRUCTIVE) == []

    def test_overwrite_on_duplicate_name(self):
        reg = ToolRegistry()
        cap1 = self._make_cap("tool")
        cap2 = ToolCapability(
            name="tool", description="updated", category=ToolCategory.WRITE,
        )
        reg.register(cap1)
        reg.register(cap2)
        assert len(reg) == 1
        assert reg.get("tool").description == "updated"
        assert reg.get("tool").category == ToolCategory.WRITE

    def test_names_sorted(self):
        reg = ToolRegistry()
        for name in ["c_tool", "a_tool", "b_tool"]:
            reg.register(self._make_cap(name))
        assert reg.names() == ["a_tool", "b_tool", "c_tool"]


# ===================================================================
# DebugState tool_trace compatibility
# ===================================================================

class TestDebugStateToolTrace:
    def test_default_is_none(self):
        debug = DebugState()
        assert debug.tool_trace is None

    def test_existing_fields_unchanged(self):
        """DebugState still has all v2 fields with defaults."""
        debug = DebugState()
        assert debug.anticipation is None
        assert debug.dialogue_trace is None
        assert debug.defense_activation is None
        assert debug.unresolved_count == 0
        assert debug.stage_timings_ms == {}

    def test_set_tool_trace(self):
        debug = DebugState()
        trace = ToolTrace(loop_count=2)
        debug.tool_trace = trace
        assert debug.tool_trace is trace
        assert debug.tool_trace.loop_count == 2

    def test_pipeline_process_returns_none_tool_trace(self):
        """Pipeline.process() still works; tool_trace stays None."""
        from pipeline import CognitivePipeline
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        result = pipe.process("hello")
        assert result.debug.tool_trace is None
        pipe.close()


# ===================================================================
# tools/types.py re-export layer
# ===================================================================

class TestToolsTypesReexport:
    def test_imports(self):
        from nur_tools.types import (
            ActionVariables,
            ToolCapability,
            ToolCategory,
            ToolDecision,
            ToolIntent,
            ToolObservation,
            ToolResult,
            ToolTrace,
        )
        # Verify they are the same objects as core.types
        import core.types as ct
        assert ActionVariables is ct.ActionVariables
        assert ToolCategory is ct.ToolCategory
        assert ToolTrace is ct.ToolTrace
