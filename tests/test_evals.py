"""Tests for the evaluation framework (Phase 9).

Tests the eval framework itself and runs integration scenarios
through the real pipeline with mock backends.
"""

from __future__ import annotations

import json
import time

import pytest

from evals.types import (
    AssertionKind,
    AssertionResult,
    EvalAssertion,
    EvalMetrics,
    EvalReport,
    EvalResult,
    EvalScenario,
    EvalTurn,
    ModulatorRange,
    TurnResult,
)
from evals.runner import run_scenario, run_scenarios, run_by_tag, _check_assertion
from evals.reporting import text_report, json_report
from evals.scenarios import (
    all_scenarios,
    emotional_core_scenarios,
    tool_loop_scenarios,
    task_planning_scenarios,
    proactive_scenarios,
    defense_resolution_scenarios,
    relationship_scenarios,
    calibration_scenarios,
)


# ===================================================================
# Type tests
# ===================================================================

class TestEvalTypes:
    def test_modulator_range_contains(self):
        r = ModulatorRange(name="arousal", low=0.3, high=0.7)
        assert r.contains(0.5)
        assert r.contains(0.3)
        assert r.contains(0.7)
        assert not r.contains(0.2)
        assert not r.contains(0.8)

    def test_assertion_kinds_exist(self):
        assert len(AssertionKind) >= 14

    def test_eval_scenario_defaults(self):
        s = EvalScenario(id="test", name="Test")
        assert s.turns == []
        assert s.tags == []
        assert not s.with_tools
        assert not s.check_proactive

    def test_eval_result_passed(self):
        tr = TurnResult(
            turn_index=0,
            user_message="hi",
            response="hello",
            assertion_results=[
                AssertionResult(EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY), True),
            ],
        )
        r = EvalResult(
            scenario_id="t", scenario_name="T", turn_results=[tr],
        )
        assert r.passed
        assert r.total_assertions == 1
        assert r.failed_assertions == 0

    def test_eval_result_failed(self):
        tr = TurnResult(
            turn_index=0,
            user_message="hi",
            response="",
            assertion_results=[
                AssertionResult(EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY), False),
            ],
        )
        r = EvalResult(
            scenario_id="t", scenario_name="T", turn_results=[tr],
        )
        assert not r.passed
        assert r.failed_assertions == 1

    def test_eval_report_aggregation(self):
        r1 = EvalResult(scenario_id="a", scenario_name="A", turn_results=[
            TurnResult(0, "hi", "yo", [
                AssertionResult(EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY), True),
            ]),
        ])
        r2 = EvalResult(scenario_id="b", scenario_name="B", turn_results=[
            TurnResult(0, "hi", "", [
                AssertionResult(EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY), False),
            ]),
        ])
        report = EvalReport(results=[r1, r2])
        assert report.total_scenarios == 2
        assert report.passed_scenarios == 1
        assert report.failed_scenarios == 1
        assert report.total_assertions == 2
        assert report.failed_assertions == 1

    def test_eval_metrics_defaults(self):
        m = EvalMetrics()
        assert m.llm_call_count == 0
        assert m.tool_call_count == 0
        assert m.total_latency_ms == 0.0


# ===================================================================
# Runner unit tests (assertion checking)
# ===================================================================

class TestAssertionChecking:
    """Test individual assertion checks in isolation."""

    def test_response_not_empty_pass(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        resp = PipelineResponse(response="hello", debug=DebugState())
        a = EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY)
        result = _check_assertion(a, resp, CognitivePipeline())
        assert result.passed

    def test_response_not_empty_fail(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        resp = PipelineResponse(response="", debug=DebugState())
        a = EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY)
        result = _check_assertion(a, resp, CognitivePipeline())
        assert not result.passed

    def test_modulator_range_pass(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        debug = DebugState()
        debug.modulator_snapshot = {"arousal": 0.5, "valence": 0.6}
        resp = PipelineResponse(response="ok", debug=debug)
        a = EvalAssertion(
            kind=AssertionKind.MODULATOR_RANGE,
            params={"name": "arousal", "low": 0.3, "high": 0.7},
        )
        result = _check_assertion(a, resp, CognitivePipeline())
        assert result.passed

    def test_modulator_range_fail(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        debug = DebugState()
        debug.modulator_snapshot = {"arousal": 0.9}
        resp = PipelineResponse(response="ok", debug=debug)
        a = EvalAssertion(
            kind=AssertionKind.MODULATOR_RANGE,
            params={"name": "arousal", "low": 0.3, "high": 0.7},
        )
        result = _check_assertion(a, resp, CognitivePipeline())
        assert not result.passed

    def test_tool_not_used_pass(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        resp = PipelineResponse(response="ok", debug=DebugState())
        a = EvalAssertion(kind=AssertionKind.TOOL_NOT_USED)
        result = _check_assertion(a, resp, CognitivePipeline())
        assert result.passed

    def test_tool_used_fail_no_trace(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        resp = PipelineResponse(response="ok", debug=DebugState())
        a = EvalAssertion(kind=AssertionKind.TOOL_USED)
        result = _check_assertion(a, resp, CognitivePipeline())
        assert not result.passed

    def test_debug_field_not_none(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        debug = DebugState()
        debug.detected_emotion = object()
        resp = PipelineResponse(response="ok", debug=debug)
        a = EvalAssertion(
            kind=AssertionKind.DEBUG_FIELD,
            params={"field": "detected_emotion", "not_none": True},
        )
        result = _check_assertion(a, resp, CognitivePipeline())
        assert result.passed

    def test_response_contains(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        resp = PipelineResponse(response="Hello world!", debug=DebugState())
        a = EvalAssertion(
            kind=AssertionKind.RESPONSE_CONTAINS,
            params={"substring": "hello"},
        )
        result = _check_assertion(a, resp, CognitivePipeline())
        assert result.passed  # case-insensitive

    def test_custom_assertion(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        resp = PipelineResponse(response="hello", debug=DebugState())
        a = EvalAssertion(
            kind=AssertionKind.CUSTOM,
            params={"fn": lambda r, p: len(r.response) > 3},
        )
        result = _check_assertion(a, resp, CognitivePipeline())
        assert result.passed

    def test_unresolved_created(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        debug = DebugState()
        debug.unresolved_count = 2
        resp = PipelineResponse(response="ok", debug=debug)
        a = EvalAssertion(
            kind=AssertionKind.UNRESOLVED_CREATED,
            params={"min_count": 1},
        )
        result = _check_assertion(a, resp, CognitivePipeline())
        assert result.passed

    def test_proactive_suppressed_no_trace(self):
        from pipeline import CognitivePipeline, PipelineResponse, DebugState
        resp = PipelineResponse(response="ok", debug=DebugState())
        a = EvalAssertion(kind=AssertionKind.PROACTIVE_SUPPRESSED)
        result = _check_assertion(a, resp, CognitivePipeline())
        assert result.passed


# ===================================================================
# Reporting tests
# ===================================================================

class TestReporting:
    def _make_report(self) -> EvalReport:
        tr_pass = TurnResult(0, "hi", "yo", [
            AssertionResult(EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY), True),
        ])
        tr_fail = TurnResult(0, "hi", "", [
            AssertionResult(
                EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY), False,
                message="Response is empty",
            ),
        ])
        r1 = EvalResult(
            scenario_id="a", scenario_name="Scenario A", tags=["core"],
            turn_results=[tr_pass], metrics=EvalMetrics(llm_call_count=2),
        )
        r2 = EvalResult(
            scenario_id="b", scenario_name="Scenario B", tags=["tool"],
            turn_results=[tr_fail], metrics=EvalMetrics(tool_call_count=1),
        )
        return EvalReport(results=[r1, r2])

    def test_text_report_format(self):
        report = self._make_report()
        txt = text_report(report)
        assert "Nūr Evaluation Report" in txt
        assert "1/2 passed" in txt
        assert "[PASS]" in txt
        assert "[FAIL]" in txt
        assert "Scenario A" in txt
        assert "Scenario B" in txt

    def test_text_report_shows_failures(self):
        report = self._make_report()
        txt = text_report(report)
        assert "FAIL turn[0]" in txt
        assert "response_not_empty" in txt

    def test_text_report_shows_metrics(self):
        report = self._make_report()
        txt = text_report(report)
        assert "llm=2" in txt
        assert "tools=1" in txt

    def test_json_report_valid(self):
        report = self._make_report()
        raw = json_report(report)
        data = json.loads(raw)
        assert data["total_scenarios"] == 2
        assert data["passed_scenarios"] == 1
        assert data["failed_scenarios"] == 1
        assert len(data["results"]) == 2

    def test_json_report_fields(self):
        report = self._make_report()
        data = json.loads(json_report(report))
        r = data["results"][0]
        assert "scenario_id" in r
        assert "metrics" in r
        assert "turns" in r
        assert r["metrics"]["llm_call_count"] == 2


# ===================================================================
# Scenario definition tests
# ===================================================================

class TestScenarioDefinitions:
    def test_all_scenarios_not_empty(self):
        scenarios = all_scenarios()
        assert len(scenarios) >= 15

    def test_all_scenarios_have_ids(self):
        for s in all_scenarios():
            assert s.id, f"Scenario missing id: {s.name}"

    def test_all_scenarios_have_tags(self):
        for s in all_scenarios():
            assert s.tags, f"Scenario {s.id} has no tags"

    def test_all_scenarios_have_turns_or_proactive(self):
        for s in all_scenarios():
            assert s.turns or s.check_proactive, f"Scenario {s.id} has no turns"

    def test_scenario_ids_unique(self):
        ids = [s.id for s in all_scenarios()]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[i for i in ids if ids.count(i) > 1]}"

    def test_emotional_core_count(self):
        assert len(emotional_core_scenarios()) >= 4

    def test_tool_loop_count(self):
        assert len(tool_loop_scenarios()) >= 4

    def test_proactive_count(self):
        assert len(proactive_scenarios()) >= 2

    def test_relationship_count(self):
        assert len(relationship_scenarios()) >= 2

    def test_defense_resolution_count(self):
        assert len(defense_resolution_scenarios()) >= 2

    def test_calibration_count(self):
        assert len(calibration_scenarios()) >= 6


# ===================================================================
# Integration: run scenarios through real pipeline
# ===================================================================

class TestIntegrationScenarios:
    """Run actual scenarios through the pipeline with mock backend."""

    def test_warm_greeting(self):
        scenario = emotional_core_scenarios()[0]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)
        assert result.metrics.llm_call_count >= 1

    def test_hostile_spike(self):
        scenario = emotional_core_scenarios()[1]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_escalation(self):
        scenario = emotional_core_scenarios()[2]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)
        assert len(result.turn_results) == 3

    def test_energy_drain(self):
        scenario = emotional_core_scenarios()[3]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_de_escalation(self):
        scenario = emotional_core_scenarios()[4]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_tool_read_file(self, tmp_path):
        """Tool read with a real file on disk."""
        f = tmp_path / "test_eval.txt"
        f.write_text("evaluation content")
        scenario = EvalScenario(
            id="integration_read",
            name="Integration file read",
            tags=["tool"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message=f"read the file {f}",
                    assertions=[
                        EvalAssertion(kind=AssertionKind.TOOL_USED,
                                      params={"tool_name": "fs.read_file"}),
                        EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY),
                    ],
                ),
            ],
        )
        result = run_scenario(scenario)
        assert result.passed, _failures(result)
        assert result.metrics.tool_call_count >= 1

    def test_tool_list_dir(self, tmp_path):
        scenario = EvalScenario(
            id="integration_list",
            name="Integration list dir",
            tags=["tool"],
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message=f"list files in {tmp_path}",
                    assertions=[
                        EvalAssertion(kind=AssertionKind.TOOL_USED,
                                      params={"tool_name": "fs.list_dir"}),
                    ],
                ),
            ],
        )
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_conversational_no_tool(self):
        scenario = tool_loop_scenarios()[2]  # "How are you feeling today?"
        result = run_scenario(scenario)
        assert result.passed, _failures(result)
        assert result.metrics.tool_call_count == 0

    def test_proactive_suppressed_calm(self):
        scenario = proactive_scenarios()[0]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_proactive_suppressed_low_energy(self):
        scenario = proactive_scenarios()[1]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_high_trust_positive(self):
        scenario = relationship_scenarios()[0]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_independent_users(self):
        scenario = relationship_scenarios()[2]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)


class TestCalibrationScenarios:
    """Integration tests for Phase 10 calibration boundary scenarios."""

    def test_persistence_low_energy(self):
        scenario = calibration_scenarios()[0]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_persistence_normal(self):
        scenario = calibration_scenarios()[1]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_defer_low_urgency(self):
        scenario = calibration_scenarios()[2]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_no_defer_moderate(self):
        scenario = calibration_scenarios()[3]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_valence_boost_boundary(self):
        scenario = calibration_scenarios()[4]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_refuse_destructive_low_certainty(self):
        scenario = calibration_scenarios()[5]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_clarify_low_autonomy(self):
        scenario = calibration_scenarios()[6]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)

    def test_trust_positive_tool_delta(self):
        scenario = calibration_scenarios()[7]
        result = run_scenario(scenario)
        assert result.passed, _failures(result)


class TestRunnerBatch:
    """Test batch runner and tag filtering."""

    def test_run_all(self):
        scenarios = emotional_core_scenarios()[:2]
        report = run_scenarios(scenarios)
        assert report.total_scenarios == 2
        assert report.passed_scenarios >= 1

    def test_run_by_tag(self):
        scenarios = all_scenarios()
        report = run_by_tag(scenarios, "proactive")
        assert report.total_scenarios >= 2
        for r in report.results:
            assert "proactive" in r.tags

    def test_run_by_nonexistent_tag(self):
        scenarios = all_scenarios()
        report = run_by_tag(scenarios, "nonexistent_tag_xyz")
        assert report.total_scenarios == 0


class TestMetricsCollection:
    """Verify metrics are collected from pipeline runs."""

    def test_llm_count(self):
        scenario = EvalScenario(
            id="metrics_llm",
            name="LLM count check",
            tags=["metrics"],
            turns=[
                EvalTurn(user_message="Hello", assertions=[]),
                EvalTurn(user_message="How are you?", assertions=[]),
            ],
        )
        result = run_scenario(scenario)
        # Each turn = at least 1 LLM call (generator)
        assert result.metrics.llm_call_count >= 2

    def test_tool_count(self, tmp_path):
        f = tmp_path / "metric_test.txt"
        f.write_text("data")
        scenario = EvalScenario(
            id="metrics_tool",
            name="Tool count check",
            tags=["metrics"],
            with_tools=True,
            turns=[
                EvalTurn(user_message=f"read the file {f}", assertions=[]),
            ],
        )
        result = run_scenario(scenario)
        assert result.metrics.tool_call_count >= 1

    def test_elapsed_populated(self):
        scenario = EvalScenario(
            id="metrics_time",
            name="Elapsed time populated",
            tags=["metrics"],
            turns=[EvalTurn(user_message="Hello")],
        )
        result = run_scenario(scenario)
        assert result.elapsed_ms > 0

    def test_stage_timings(self):
        scenario = EvalScenario(
            id="metrics_stages",
            name="Stage timings present",
            tags=["metrics"],
            turns=[EvalTurn(user_message="Hello")],
        )
        result = run_scenario(scenario)
        assert "total" in result.metrics.stage_timings_ms


# ===================================================================
# Helpers
# ===================================================================

def _failures(result: EvalResult) -> str:
    """Format failures for assertion messages."""
    parts = []
    for t in result.turn_results:
        for ar in t.assertion_results:
            if not ar.passed:
                parts.append(
                    f"turn[{t.turn_index}] {ar.assertion.kind.value}: {ar.message}"
                )
    for ar in result.proactive_results:
        if not ar.passed:
            parts.append(f"proactive {ar.assertion.kind.value}: {ar.message}")
    return "; ".join(parts)
