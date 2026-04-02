"""Tests for the focused Phase 11 report script."""

from __future__ import annotations

from tests.run_phase11_report import (
    collect_phase11_trace,
    load_phase11_scenarios,
    render_phase11_trace,
)


class TestPhase11Report:
    def test_loads_all_phase11_scenarios(self):
        scenarios = load_phase11_scenarios()
        assert len(scenarios) >= 6

    def test_collects_trace_for_single_scenario(self):
        scenario = load_phase11_scenarios("p11_external_distress_validate")[0]
        trace = collect_phase11_trace(scenario)
        assert trace["scenario_id"] == "p11_external_distress_validate"
        assert len(trace["turns"]) == 1
        assert trace["turns"][0]["response_strategy"] == "validate"

    def test_rendered_trace_mentions_strategy(self):
        scenario = load_phase11_scenarios("p11_action_request_practical")[0]
        trace = collect_phase11_trace(scenario)
        rendered = render_phase11_trace([trace])
        assert "strategy=practical_help" in rendered
        assert scenario.id in rendered
