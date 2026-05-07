"""Tests for Agentic Tools Phase 3: memory and self-model coupling.

Tests cover:
  1. Short-term memory records for tool executions
  2. Long-term memory salience checks + entry creation
  3. Self-observations from tool behavior
  4. Unresolved items from tool failures
  5. Trust deltas from tool outcomes
  6. ToolMemoryEffects dataclass
  7. Pipeline integration: memory coupling wired after tool loop
  8. Debug API serialization of tool_memory_effects
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timezone

from core.types import (
    ActionVariables,
    EmotionalEvent,
    EventType,
    LongTermEntry,
    ModulatorState,
    ToolCategory,
    ToolDecision,
    ToolIntent,
    ToolObservation,
    ToolResult,
    UnresolvedItem,
)
from core.dual_process.generator import MockLLMBackend
from core.tool_memory import (
    ToolMemoryEffects,
    compute_tool_trust_delta,
    create_long_term_entry,
    create_tool_event,
    create_tool_unresolved_item,
    derive_tool_self_observations,
    is_salient_episode,
)
from nur_tools.registry import ToolRegistry
from nur_tools.executor import ToolExecutor
from nur_tools import register_builtins
from pipeline import CognitivePipeline, DebugState
from runtime.debug.mental_state import mental_health


# ===================================================================
# Helpers
# ===================================================================

def _ok_result(tool_name: str = "fs.read_file", output: str = "file contents") -> ToolResult:
    return ToolResult(tool_name=tool_name, success=True, output=output)


def _fail_result(tool_name: str = "fs.read_file", error: str = "not found") -> ToolResult:
    return ToolResult(tool_name=tool_name, success=False, output="", error=error)


def _blocked_result(tool_name: str = "shell.run_command") -> ToolResult:
    return ToolResult(tool_name=tool_name, success=False, output="", error="permission denied")


def _empty_result(tool_name: str = "fs.search_text") -> ToolResult:
    return ToolResult(tool_name=tool_name, success=True, output="")


def _ok_observation() -> ToolObservation:
    return ToolObservation(
        summary="Read file ok",
        emotional_delta={"certainty": 0.08, "valence": 0.02},
        certainty_delta=0.08,
    )


def _fail_observation() -> ToolObservation:
    return ToolObservation(
        summary="File not found",
        emotional_delta={"arousal": 0.08, "valence": -0.08, "certainty": -0.10},
        certainty_delta=-0.10,
    )


def _strong_observation() -> ToolObservation:
    return ToolObservation(
        summary="Unexpected result",
        emotional_delta={"valence": -0.12, "certainty": -0.15},
        certainty_delta=-0.15,
    )


def _default_action_vars() -> ActionVariables:
    return ActionVariables(
        risk_tolerance=0.5,
        action_urgency=0.5,
        clarification_threshold=0.5,
        persistence_drive=0.5,
        autonomy_bias=0.5,
    )


def _high_risk_action_vars() -> ActionVariables:
    return ActionVariables(
        risk_tolerance=0.85,
        action_urgency=0.7,
        clarification_threshold=0.3,
        persistence_drive=0.6,
        autonomy_bias=0.7,
    )


def _make_pipeline_with_tools():
    """Create a CognitivePipeline wired to a ToolExecutor."""
    reg = ToolRegistry()
    exe = ToolExecutor(reg)
    register_builtins(reg, exe)
    pipe = CognitivePipeline(tool_executor=exe)
    return pipe, reg, exe


class ListPageWebProvider:
    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        return [
            {
                "title": "The Best AI Books in 2026",
                "url": "https://example.com/best-ai-books",
                "snippet": "A list of recommendations for readers.",
            },
            {
                "title": "Top 20 Books on AI in 2026",
                "url": "https://example.com/top-ai-books",
                "snippet": "Roundup of artificial intelligence book lists.",
            },
        ]

    def fetch(self, url: str) -> str:
        return "Article body"

    def extract_text(self, url: str) -> str:
        return "Article body"


# ===================================================================
# 1. Short-term memory records
# ===================================================================

class TestCreateToolEvent:
    def test_success_event(self):
        event = create_tool_event(_ok_result(), _ok_observation(), ModulatorState())
        assert isinstance(event, EmotionalEvent)
        assert event.event_type == EventType.USER_MESSAGE
        assert event.source == "tool"
        assert event.metadata["type"] == "tool_execution"
        assert event.metadata["tool_name"] == "fs.read_file"
        assert event.metadata["success"] is True
        assert event.intensity == 0.3  # baseline for success

    def test_failure_event_higher_intensity(self):
        event = create_tool_event(_fail_result(), _fail_observation(), ModulatorState())
        assert event.metadata["success"] is False
        assert event.intensity >= 0.5

    def test_strong_certainty_shift_intensity(self):
        obs = _strong_observation()
        event = create_tool_event(_ok_result(), obs, ModulatorState())
        # |certainty_delta| = 0.15 > 0.08 threshold → intensity bumped
        assert event.intensity >= 0.6

    def test_event_contains_summary(self):
        event = create_tool_event(_ok_result(), _ok_observation(), ModulatorState())
        assert "summary" in event.metadata
        assert len(event.metadata["summary"]) > 0

    def test_event_timestamp_set(self):
        before = time.time()
        event = create_tool_event(_ok_result(), _ok_observation(), ModulatorState())
        assert event.timestamp >= before


# ===================================================================
# 2. Long-term memory salience
# ===================================================================

class TestIsSalientEpisode:
    def test_destructive_always_salient(self):
        assert is_salient_episode(
            _ok_result(), _ok_observation(), ToolCategory.DESTRUCTIVE,
        )

    def test_failure_always_salient(self):
        assert is_salient_episode(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY,
        )

    def test_repeated_failures_salient(self):
        assert is_salient_episode(
            _ok_result(), _ok_observation(), ToolCategory.READ_ONLY, failure_count=2,
        )

    def test_strong_certainty_shift_salient(self):
        obs = _strong_observation()
        assert is_salient_episode(
            _ok_result(), obs, ToolCategory.READ_ONLY,
        )

    def test_strong_valence_shift_salient(self):
        obs = ToolObservation(
            summary="big valence change",
            emotional_delta={"valence": 0.10},
            certainty_delta=0.01,
        )
        assert is_salient_episode(_ok_result(), obs, ToolCategory.READ_ONLY)

    def test_routine_success_not_salient(self):
        obs = ToolObservation(
            summary="normal read",
            emotional_delta={"certainty": 0.02, "valence": 0.01},
            certainty_delta=0.02,
        )
        assert not is_salient_episode(
            _ok_result(), obs, ToolCategory.READ_ONLY,
        )


class TestCreateLongTermEntry:
    def test_success_entry(self):
        entry = create_long_term_entry(
            _ok_result(), _ok_observation(), ToolCategory.READ_ONLY, user_id="u1",
        )
        assert isinstance(entry, LongTermEntry)
        assert entry.emotional_valence > 0  # positive for success
        assert entry.topic == "tool:fs.read_file"
        assert entry.source_person == "u1"
        assert entry.confidence == 0.8

    def test_failure_entry_high_confidence(self):
        entry = create_long_term_entry(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY,
        )
        assert entry.emotional_valence < 0
        assert entry.confidence == 0.9  # failures are high confidence
        assert entry.spike is True  # failures bypass threshold

    def test_destructive_success_still_negative_valence(self):
        entry = create_long_term_entry(
            _ok_result("fs.delete_path"), _ok_observation(), ToolCategory.DESTRUCTIVE,
        )
        assert entry.emotional_valence == -0.1  # cautious about destructive

    def test_destructive_failure_very_negative(self):
        entry = create_long_term_entry(
            _fail_result("fs.delete_path"), _fail_observation(), ToolCategory.DESTRUCTIVE,
        )
        assert entry.emotional_valence == -0.5

    def test_summary_capped(self):
        long_output = "x" * 500
        result = ToolResult(tool_name="fs.read_file", success=True, output=long_output)
        entry = create_long_term_entry(result, _ok_observation(), ToolCategory.READ_ONLY)
        assert len(entry.summary) < 300  # well under raw output


# ===================================================================
# 3. Self-observations
# ===================================================================

class TestDeriveToolSelfObservations:
    def test_success_read_only_methodical(self):
        obs = derive_tool_self_observations(
            _ok_result(), _ok_observation(), ToolCategory.READ_ONLY,
            _default_action_vars(), decision=None,
        )
        traits = [t[0] for t in obs]
        assert "methodical" in traits
        assert "technically_competent" in traits

    def test_success_destructive_decisive(self):
        obs = derive_tool_self_observations(
            _ok_result("fs.delete_path"), _ok_observation(), ToolCategory.DESTRUCTIVE,
            _default_action_vars(), decision=None,
        )
        traits = [t[0] for t in obs]
        assert "decisive" in traits

    def test_high_risk_write_reckless(self):
        obs = derive_tool_self_observations(
            _ok_result("fs.write_file"), _ok_observation(), ToolCategory.WRITE,
            _high_risk_action_vars(), decision=None,
        )
        traits = [t[0] for t in obs]
        assert "reckless" in traits

    def test_failure_frustrated(self):
        obs = derive_tool_self_observations(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY,
            _default_action_vars(), decision=None,
        )
        traits = [t[0] for t in obs]
        assert "frustrated" in traits

    def test_repeated_failure_persistent(self):
        obs = derive_tool_self_observations(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY,
            _default_action_vars(), decision=None, failure_count=3,
        )
        traits = [t[0] for t in obs]
        assert "persistent" in traits

    def test_clarify_decision_hesitant(self):
        decision = ToolDecision(
            decision="clarify",
            intent=ToolIntent(tool_name="fs.read_file", arguments={}, reason="test", expected_outcome="test"),
            rationale="need confirmation",
        )
        obs = derive_tool_self_observations(
            _ok_result(), _ok_observation(), ToolCategory.READ_ONLY,
            _default_action_vars(), decision=decision,
        )
        traits = [t[0] for t in obs]
        assert "hesitant" in traits

    def test_refuse_decision_avoidant(self):
        decision = ToolDecision(
            decision="refuse",
            intent=ToolIntent(tool_name="fs.delete_path", arguments={}, reason="test", expected_outcome="test"),
            rationale="too risky",
        )
        obs = derive_tool_self_observations(
            _ok_result(), _ok_observation(), ToolCategory.DESTRUCTIVE,
            _default_action_vars(), decision=decision,
        )
        traits = [t[0] for t in obs]
        assert "avoidant" in traits

    def test_capped_at_three(self):
        # High-risk destructive success → could produce many observations
        obs = derive_tool_self_observations(
            _ok_result("fs.delete_path"), _ok_observation(), ToolCategory.DESTRUCTIVE,
            _high_risk_action_vars(), decision=None,
        )
        assert len(obs) <= 3


# ===================================================================
# 4. Unresolved items
# ===================================================================

class TestCreateToolUnresolvedItem:
    def test_success_with_output_no_item(self):
        item = create_tool_unresolved_item(
            _ok_result(), _ok_observation(), ToolCategory.READ_ONLY,
        )
        assert item is None

    def test_failure_creates_item(self):
        item = create_tool_unresolved_item(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY,
        )
        assert item is not None
        assert isinstance(item, UnresolvedItem)
        assert item.source == "tool_failure"
        assert "Failed" in item.description

    def test_blocked_action_detected(self):
        item = create_tool_unresolved_item(
            _blocked_result(), _fail_observation(), ToolCategory.READ_ONLY,
        )
        assert item is not None
        assert item.source == "blocked_action"
        assert "Blocked" in item.description

    def test_empty_output_incomplete_task(self):
        item = create_tool_unresolved_item(
            _empty_result(), _ok_observation(), ToolCategory.READ_ONLY,
        )
        assert item is not None
        assert item.source == "incomplete_task"

    def test_failure_intensity_increases_with_count(self):
        item1 = create_tool_unresolved_item(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY, failure_count=0,
        )
        item2 = create_tool_unresolved_item(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY, failure_count=3,
        )
        assert item2.intensity > item1.intensity

    def test_blocked_has_slow_decay(self):
        item = create_tool_unresolved_item(
            _blocked_result(), _fail_observation(), ToolCategory.READ_ONLY,
        )
        assert item.decay_rate == 0.05

    def test_failure_has_medium_decay(self):
        item = create_tool_unresolved_item(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY,
        )
        assert item.decay_rate == 0.08

    def test_incomplete_has_fast_decay(self):
        item = create_tool_unresolved_item(
            _empty_result(), _ok_observation(), ToolCategory.READ_ONLY,
        )
        assert item.decay_rate == 0.10

    def test_item_has_unique_id(self):
        item1 = create_tool_unresolved_item(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY,
        )
        item2 = create_tool_unresolved_item(
            _fail_result(), _fail_observation(), ToolCategory.READ_ONLY,
        )
        assert item1.id != item2.id


# ===================================================================
# 5. Trust deltas
# ===================================================================

class TestComputeToolTrustDelta:
    def test_successful_read_positive(self):
        delta = compute_tool_trust_delta(
            _ok_result(), ToolCategory.READ_ONLY, _default_action_vars(),
        )
        assert delta == 0.015

    def test_successful_cognitive_positive(self):
        delta = compute_tool_trust_delta(
            _ok_result(), ToolCategory.COGNITIVE, _default_action_vars(),
        )
        assert delta == 0.015

    def test_successful_write_neutral(self):
        delta = compute_tool_trust_delta(
            _ok_result(), ToolCategory.WRITE, _default_action_vars(),
        )
        assert delta == 0.0

    def test_destructive_failure_negative(self):
        delta = compute_tool_trust_delta(
            _fail_result(), ToolCategory.DESTRUCTIVE, _default_action_vars(),
        )
        assert delta == -0.03

    def test_reckless_failure_negative(self):
        delta = compute_tool_trust_delta(
            _fail_result(), ToolCategory.READ_ONLY, _high_risk_action_vars(),
        )
        assert delta == -0.03

    def test_routine_failure_neutral(self):
        delta = compute_tool_trust_delta(
            _fail_result(), ToolCategory.READ_ONLY, _default_action_vars(),
        )
        assert delta == 0.0

    def test_trust_asymmetry(self):
        pos = compute_tool_trust_delta(
            _ok_result(), ToolCategory.READ_ONLY, _default_action_vars(),
        )
        neg = compute_tool_trust_delta(
            _fail_result(), ToolCategory.DESTRUCTIVE, _default_action_vars(),
        )
        assert abs(neg) > abs(pos)  # negativity bias


# ===================================================================
# 6. ToolMemoryEffects dataclass
# ===================================================================

class TestToolMemoryEffects:
    def test_defaults(self):
        effects = ToolMemoryEffects()
        assert effects.short_term_recorded is False
        assert effects.long_term_written is False
        assert effects.long_term_summary == ""
        assert effects.self_observations == []
        assert effects.unresolved_items_created == []
        assert effects.operational_issues == []
        assert effects.trust_delta == 0.0


# ===================================================================
# 7. Pipeline integration
# ===================================================================

class TestPipelineToolMemoryIntegration:
    def test_no_tool_executor_no_effects(self):
        pipe = CognitivePipeline()
        resp = pipe.process("Hello!", user_id="u1")
        assert resp.debug.tool_memory_effects is None

    def test_conversational_no_tool_effects(self):
        pipe, _, _ = _make_pipeline_with_tools()
        resp = pipe.process("How are you today?", user_id="u1")
        # No tool intent detected → no memory effects
        assert resp.debug.tool_memory_effects is None

    def test_read_file_records_short_term(self):
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            f.flush()
            try:
                resp = pipe.process(f"read file {f.name}", user_id="u1")
                effects = resp.debug.tool_memory_effects
                assert effects is not None
                assert effects.short_term_recorded is True
            finally:
                os.unlink(f.name)

    def test_read_file_self_observations(self):
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            f.flush()
            try:
                resp = pipe.process(f"read file {f.name}", user_id="u1")
                effects = resp.debug.tool_memory_effects
                assert effects is not None
                assert len(effects.self_observations) > 0
            finally:
                os.unlink(f.name)

    def test_failed_tool_creates_unresolved(self):
        pipe, _, _ = _make_pipeline_with_tools()
        resp = pipe.process("read file /nonexistent/path/foo.txt", user_id="u1")
        effects = resp.debug.tool_memory_effects
        assert effects is not None
        assert len(effects.unresolved_items_created) > 0

    def test_missing_browser_provider_is_operational_not_unresolved(self):
        pipe, _, _ = _make_pipeline_with_tools()
        resp = pipe.process("get page text from https://example.com", user_id="u1")
        effects = resp.debug.tool_memory_effects
        assert effects is not None
        assert effects.operational_issues
        assert "browser.get_page_text" in effects.operational_issues[0]
        assert effects.unresolved_items_created == []
        assert pipe.engine.state.resolution == 0.0
        assert resp.debug.unresolved_count == 0

    def test_successful_tool_resolves_matching_prior_tool_failure(self):
        pipe, _, _ = _make_pipeline_with_tools()
        pipe.engine.add_unresolved(UnresolvedItem(
            id="tool_failure_read_file",
            source="tool_failure",
            description="Failed: fs.read_file - file was missing",
            created_at=datetime.now(timezone.utc),
            intensity=0.4,
            decay_rate=0.08,
        ))
        assert pipe.engine.state.resolution > 0

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("recovered")
            f.flush()
            try:
                pipe.process(f"read file {f.name}", user_id="u1")
            finally:
                os.unlink(f.name)

        assert pipe.engine.active_unresolved() == []
        assert pipe.engine.state.resolution == 0.0

    def test_failed_fresh_lookup_overrides_confident_generated_answer(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)  # NullWebProvider, by design.
        pipe = CognitivePipeline(
            llm_backend=MockLLMBackend(response="The latest release is definitely 9.9."),
            tool_executor=exe,
        )

        resp = pipe.process("what is the latest release of example product?", user_id="u1")

        assert "can't verify" in resp.response.lower()
        assert "No web provider configured" in resp.response
        assert resp.debug.self_check_passed is False
        assert resp.debug.tool_memory_effects is not None
        assert resp.debug.tool_memory_effects.operational_issues
        assert resp.debug.tool_memory_effects.unresolved_items_created == []
        assert pipe.engine.state.resolution == 0.0

    def test_search_list_pages_do_not_become_book_names(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe, web_provider=ListPageWebProvider())
        pipe = CognitivePipeline(
            llm_backend=MockLLMBackend(
                response=(
                    "1. The Best AI Books in 2026\n"
                    "2. Top 20 Books on AI in 2026"
                )
            ),
            tool_executor=exe,
        )

        resp = pipe.process("find me top AI books released after March 2026", user_id="u1")

        assert "not enough source text" in resp.response
        assert resp.debug.tool_trace is not None
        assert resp.debug.tool_trace.executed_results
        assert resp.debug.tool_trace.executed_results[0].tool_name == "web.search"

    def test_repeated_hostility_is_bounded_and_read_task_still_runs(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)  # NullWebProvider, by design.
        pipe = CognitivePipeline(
            llm_backend=MockLLMBackend(response="I am exhausted and we are wasting time."),
            tool_executor=exe,
        )

        for msg in ("fuck u", "yeah because you are an idiot", "fuck u again"):
            resp = pipe.process(msg, user_id="u1")

        health = mental_health(resp.debug.modulator_snapshot)
        assert health["score"] > 0
        assert resp.debug.modulator_snapshot["resolution"] <= 0.30
        assert resp.debug.modulator_snapshot["energy"] >= 0.60
        assert "exhausted" not in resp.response.lower()
        assert "wasting" not in resp.response.lower()

        task = pipe.process("find me top AI books released after March 2026", user_id="u1")

        assert task.debug.tool_trace is not None
        assert task.debug.tool_trace.executed_results
        assert task.debug.tool_trace.executed_results[0].tool_name == "web.search"
        assert "can't verify" in task.response.lower()
        assert task.debug.modulator_snapshot["energy"] >= 0.72

    def test_failed_tool_writes_long_term(self):
        pipe, _, _ = _make_pipeline_with_tools()
        resp = pipe.process("read file /nonexistent/path/bar.txt", user_id="u1")
        effects = resp.debug.tool_memory_effects
        assert effects is not None
        assert effects.long_term_written is True

    def test_destructive_tool_writes_long_term(self):
        pipe, _, _ = _make_pipeline_with_tools()
        # Delete a non-existent file — should fail, but destructive always salient
        resp = pipe.process("delete file /tmp/surely_nonexistent_12345.txt", user_id="u1")
        effects = resp.debug.tool_memory_effects
        assert effects is not None
        assert effects.long_term_written is True

    def test_tool_trust_delta_applied(self):
        pipe, _, _ = _make_pipeline_with_tools()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            f.flush()
            try:
                # Read person trust before
                pipe.person_profiles.get_or_create("u1")
                before = pipe.person_profiles.get_or_create("u1").trust

                resp = pipe.process(f"read file {f.name}", user_id="u1")
                effects = resp.debug.tool_memory_effects
                assert effects is not None

                # Trust should have shifted (read_only success = +0.01)
                after = pipe.person_profiles.get_or_create("u1").trust
                # Overall trust includes both event-level and tool-level changes
                # Just check effects recorded a delta
                assert effects.trust_delta != 0.0 or after != before
            finally:
                os.unlink(f.name)


# ===================================================================
# 8. Debug API serialization
# ===================================================================

class TestDebugApiToolMemoryEffects:
    def test_serialize_none(self):
        from runtime.debug.api import _debug_to_dict
        debug = DebugState()
        d = _debug_to_dict(debug)
        assert d["tool_memory_effects"] is None

    def test_serialize_effects(self):
        from runtime.debug.api import _debug_to_dict
        from core.tool_memory import ToolMemoryEffects

        debug = DebugState()
        debug.tool_memory_effects = ToolMemoryEffects(
            short_term_recorded=True,
            long_term_written=True,
            long_term_summary="Tool fs.read_file: contents...",
            self_observations=["methodical=0.60", "technically_competent=0.50"],
            unresolved_items_created=[],
            trust_delta=0.01,
        )
        d = _debug_to_dict(debug)
        tme = d["tool_memory_effects"]
        assert tme is not None
        assert tme["short_term_recorded"] is True
        assert tme["long_term_written"] is True
        assert tme["long_term_summary"] == "Tool fs.read_file: contents..."
        assert len(tme["self_observations"]) == 2
        assert tme["trust_delta"] == 0.01


# ===================================================================
# 9. Compact summary helper
# ===================================================================

class TestCompactSummary:
    def test_success_with_output(self):
        from core.tool_memory import _compact_summary
        result = _ok_result(output="hello world")
        summary = _compact_summary(result, _ok_observation())
        assert "fs.read_file" in summary
        assert "hello world" in summary

    def test_success_no_output(self):
        from core.tool_memory import _compact_summary
        result = ToolResult(tool_name="shell.run_command", success=True, output="")
        summary = _compact_summary(result, _ok_observation())
        assert "completed (no output)" in summary

    def test_failure_summary(self):
        from core.tool_memory import _compact_summary
        result = _fail_result(error="file not found")
        summary = _compact_summary(result, _fail_observation())
        assert "failed" in summary
        assert "file not found" in summary

    def test_long_output_truncated(self):
        from core.tool_memory import _compact_summary
        result = ToolResult(tool_name="fs.read_file", success=True, output="x" * 500)
        summary = _compact_summary(result, _ok_observation())
        assert len(summary) < 300
        assert "..." in summary
