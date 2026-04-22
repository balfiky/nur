"""Evaluation reporting — text and JSON output."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from evals.types import AssertionResult, EvalReport, EvalResult, RunProvenance


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

def text_report(report: EvalReport) -> str:
    """Produce a human-readable text summary of an eval report."""
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("Nūr Evaluation Report")
    lines.append("=" * 70)
    lines.append(
        f"Scenarios: {report.passed_scenarios}/{report.total_scenarios} passed"
    )
    lines.append(
        f"Assertions: {report.total_assertions - report.failed_assertions}"
        f"/{report.total_assertions} passed"
    )
    lines.append("")

    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        lines.append(f"  [{status}] {result.scenario_name} ({result.scenario_id})")
        lines.append(f"         tags={result.tags}  elapsed={result.elapsed_ms:.0f}ms")

        # Metrics
        m = result.metrics
        metrics_parts = []
        if m.llm_call_count:
            metrics_parts.append(f"llm={m.llm_call_count}")
        if m.tool_call_count:
            metrics_parts.append(f"tools={m.tool_call_count}")
        if m.defense_activations:
            metrics_parts.append(f"defense={m.defense_activations}")
        if m.proactive_count:
            metrics_parts.append(f"proactive={m.proactive_count}")
        if m.task_loop_count:
            metrics_parts.append(f"task_loops={m.task_loop_count}")
        if metrics_parts:
            lines.append(f"         metrics: {', '.join(metrics_parts)}")

        if not result.passed:
            for turn in result.turn_results:
                for ar in turn.assertion_results:
                    if not ar.passed:
                        lines.append(
                            f"         FAIL turn[{turn.turn_index}]: "
                            f"{ar.assertion.kind.value} — {ar.message}"
                        )
            for ar in result.proactive_results:
                if not ar.passed:
                    lines.append(
                        f"         FAIL proactive: "
                        f"{ar.assertion.kind.value} — {ar.message}"
                    )
        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------

def _assertion_result_to_dict(ar: AssertionResult) -> dict[str, Any]:
    actual = ar.actual
    # Ensure serializable
    if not isinstance(actual, (str, int, float, bool, list, dict, type(None))):
        actual = str(actual)
    return {
        "kind": ar.assertion.kind.value,
        "description": ar.assertion.description,
        "passed": ar.passed,
        "actual": actual,
        "message": ar.message,
    }


def _result_to_dict(result: EvalResult) -> dict[str, Any]:
    return {
        "scenario_id": result.scenario_id,
        "scenario_name": result.scenario_name,
        "tags": result.tags,
        "passed": result.passed,
        "errored": result.errored,
        "failure_reason": result.failure_reason,
        "elapsed_ms": round(result.elapsed_ms, 1),
        "total_assertions": result.total_assertions,
        "failed_assertions": result.failed_assertions,
        "metrics": {
            "llm_call_count": result.metrics.llm_call_count,
            "tool_call_count": result.metrics.tool_call_count,
            "total_latency_ms": round(result.metrics.total_latency_ms, 1),
            "proactive_count": result.metrics.proactive_count,
            "task_loop_count": result.metrics.task_loop_count,
            "unresolved_items_created": result.metrics.unresolved_items_created,
            "defense_activations": result.metrics.defense_activations,
        },
        "turns": [
            {
                "turn_index": t.turn_index,
                "user_message": t.user_message,
                "response": t.response[:200],
                "modulators": t.modulator_snapshot,
                "assertions": [
                    _assertion_result_to_dict(ar)
                    for ar in t.assertion_results
                ],
            }
            for t in result.turn_results
        ],
        "proactive_assertions": [
            _assertion_result_to_dict(ar) for ar in result.proactive_results
        ],
    }


def _provenance_to_dict(prov: RunProvenance) -> dict[str, Any]:
    """RunProvenance → plain dict, sorted stably for diffable reports."""
    raw = dataclasses.asdict(prov)
    # Keep fingerprints deterministic across runs
    raw["config_fingerprints"] = dict(sorted(raw["config_fingerprints"].items()))
    raw["scenario_ids"] = sorted(raw.get("scenario_ids") or [])
    return raw


def json_report(report: EvalReport) -> str:
    """Produce a JSON report from an eval report.

    Provenance appears first so a quick head/grep on the file surfaces
    backend, git sha, and scenario set before any per-scenario detail.
    """
    data: dict[str, Any] = {}
    if report.provenance is not None:
        data["provenance"] = _provenance_to_dict(report.provenance)
    data["timestamp"] = report.timestamp
    data["total_scenarios"] = report.total_scenarios
    data["passed_scenarios"] = report.passed_scenarios
    data["failed_scenarios"] = report.failed_scenarios
    data["total_assertions"] = report.total_assertions
    data["failed_assertions"] = report.failed_assertions
    data["errored_scenarios"] = sum(1 for r in report.results if r.errored)
    data["results"] = [_result_to_dict(r) for r in report.results]
    return json.dumps(data, indent=2)
