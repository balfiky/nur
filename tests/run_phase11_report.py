"""Detailed Phase 11 report.

Runs the focused Phase 11 human-likeness scenarios and prints both the
assertion summary and a per-turn trace of the key debug signals:
appraisal, event classification, response strategy, and relationship context.

Usage:
    python -m tests.run_phase11_report
    python -m tests.run_phase11_report --scenario p11_open_loop_challenge
    python -m tests.run_phase11_report --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from evals.reporting import json_report, text_report
from evals.runner import _check_assertion, _make_pipeline, run_scenarios
from evals.scenarios import phase11_human_scenarios
from evals.types import EvalScenario


def load_phase11_scenarios(scenario_id: str | None = None) -> list[EvalScenario]:
    """Return all Phase 11 scenarios or a single scenario by id."""
    scenarios = phase11_human_scenarios()
    if scenario_id is None:
        return scenarios
    filtered = [scenario for scenario in scenarios if scenario.id == scenario_id]
    if not filtered:
        known = ", ".join(s.id for s in scenarios)
        raise ValueError(f"Unknown scenario '{scenario_id}'. Known: {known}")
    return filtered


def collect_phase11_trace(scenario: EvalScenario) -> dict[str, Any]:
    """Run one Phase 11 scenario and capture a compact debug trace."""
    pipeline = _make_pipeline(scenario)
    try:
        if scenario.initial_trust is not None:
            seen_users: set[str] = set()
            for turn in scenario.turns:
                if turn.user_id in seen_users:
                    continue
                person = pipeline.person_profiles.get_or_create(turn.user_id)
                person.trust = scenario.initial_trust
                pipeline.person_profiles.save(person)
                seen_users.add(turn.user_id)

        turn_traces: list[dict[str, Any]] = []
        for index, turn in enumerate(scenario.turns):
            response = pipeline.process(turn.user_message, user_id=turn.user_id)
            debug = response.debug
            assertion_results = [
                _check_assertion(assertion, response, pipeline)
                for assertion in turn.assertions
            ]

            turn_data: dict[str, Any] = {
                "turn_index": index,
                "user_id": turn.user_id,
                "user_message": turn.user_message,
                "response": response.response,
                "event_classified": debug.event_classified,
                "response_strategy": debug.response_strategy,
                "appraisal": (
                    debug.appraisal_frame.to_dict() if debug.appraisal_frame else None
                ),
                "relationship_context": (
                    debug.relationship_context.to_dict()
                    if debug.relationship_context
                    else None
                ),
                "assertions": [
                    {
                        "kind": result.assertion.kind.value,
                        "passed": result.passed,
                        "message": result.message,
                    }
                    for result in assertion_results
                ],
                "passed": all(result.passed for result in assertion_results),
            }

            if turn.end_session:
                digested = pipeline.end_session(user_id=turn.user_id)
                turn_data["session_end"] = {
                    "summary": digested.summary,
                    "arc": digested.emotional_arc_label,
                    "trust_delta": digested.trust_delta,
                    "unresolved_flags": list(digested.unresolved_flags),
                    "memories_written": digested.memories_written,
                }

            turn_traces.append(turn_data)

        return {
            "scenario_id": scenario.id,
            "scenario_name": scenario.name,
            "description": scenario.description,
            "tags": list(scenario.tags),
            "turns": turn_traces,
            "passed": all(turn["passed"] for turn in turn_traces),
        }
    finally:
        pipeline.close()


def render_phase11_trace(traces: list[dict[str, Any]]) -> str:
    """Render the detailed turn-by-turn Phase 11 trace."""
    lines: list[str] = []
    sep = "=" * 70
    lines.append(sep)
    lines.append("Phase 11 Detailed Trace")
    lines.append(sep)

    for trace in traces:
        status = "PASS" if trace["passed"] else "FAIL"
        lines.append(f"{trace['scenario_name']} ({trace['scenario_id']}) [{status}]")
        for turn in trace["turns"]:
            appraisal = turn["appraisal"] or {}
            relationship = turn["relationship_context"] or {}
            open_loops = relationship.get("open_loop_count", 0)
            lines.append(f"  turn[{turn['turn_index']}] user: {turn['user_message']}")
            lines.append(
                "    "
                f"event={turn['event_classified']}  "
                f"strategy={turn['response_strategy']}  "
                f"target={appraisal.get('primary_target', '')}  "
                f"move={appraisal.get('social_move', '')}  "
                f"loops={open_loops}"
            )
            if relationship.get("summary"):
                lines.append(f"    relationship: {relationship['summary']}")
            lines.append(f"    response: {_shorten(turn['response'], 160)}")
            failed = [a for a in turn["assertions"] if not a["passed"]]
            if failed:
                for item in failed:
                    lines.append(
                        f"    FAIL {item['kind']}: {item['message'] or 'assertion failed'}"
                    )
            else:
                lines.append("    assertions: all passed")
            if "session_end" in turn:
                session_end = turn["session_end"]
                lines.append(
                    "    "
                    f"[session end] arc={session_end['arc'] or 'n/a'}  "
                    f"trust_delta={session_end['trust_delta']:+.4f}  "
                    f"memories={session_end['memories_written']}"
                )
        lines.append("")

    return "\n".join(lines)


def _shorten(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def main() -> None:
    parser = argparse.ArgumentParser(description="Detailed Phase 11 regression report")
    parser.add_argument("--scenario", help="Run only one Phase 11 scenario by id")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args()

    try:
        scenarios = load_phase11_scenarios(args.scenario)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    report = run_scenarios(scenarios)
    traces = [collect_phase11_trace(scenario) for scenario in scenarios]

    if args.json:
        payload = {
            "report": json.loads(json_report(report)),
            "traces": traces,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(text_report(report))
        print()
        print(render_phase11_trace(traces))

    if report.failed_scenarios > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
