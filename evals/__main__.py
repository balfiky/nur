"""Run the evaluation suite from the command line.

Usage:
    python -m evals --backend mock
    python -m evals --backend minimax --tag phase11
    python -m evals --backend openai_compat --base-url http://localhost:8080/v1 --model qwen2.5

    python -m evals --backend mock --list
    python -m evals --backend minimax --tag phase11 --report reports/phase11.json

Provenance is recorded automatically: git sha, dirty-worktree flag,
backend identity, config/prompt SHA256 fingerprints, and per-run
execution counters. See ``evals.types.RunProvenance``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path

from evals.backends import (
    MissingBackendConfigError,
    build_backend_factory,
    spec_from_args,
)
from evals.provenance import build_provenance
from evals.reporting import json_report, text_report
from evals.runner import run_scenarios
from evals.scenarios import ALL_TAGS, all_scenarios


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nūr evaluation runner — runs behavioral scenarios against a real backend.",
    )
    parser.add_argument(
        "--backend",
        choices=["mock", "minimax", "openai_compat"],
        required=False,
        help=(
            "LLM backend to run against. REQUIRED unless --list is set. "
            "No silent fallback to mock."
        ),
    )
    parser.add_argument("--model", default="", help="Model name (minimax / openai_compat)")
    parser.add_argument("--base-url", default="", help="Base URL (openai_compat, or override minimax)")
    parser.add_argument("--api-key", default="", help="API key literal (prefer --api-key-env or env vars)")
    parser.add_argument(
        "--api-key-env",
        default="",
        help="Name of env var to read the API key from (e.g. MINIMAX_API_KEY)",
    )
    # --temperature and --max-tokens were previously accepted but never
    # propagated to the backend request payloads. Removed to stop recording
    # inference settings that did not actually apply. If/when the clients are
    # instrumented to accept and send these, re-add here and in BackendSpec.
    parser.add_argument("--tag", default="", help="Run only scenarios with this tag")
    parser.add_argument("--list", action="store_true", help="List scenarios and exit")
    parser.add_argument("--json", action="store_true", help="Emit JSON report to stdout")
    parser.add_argument(
        "--report",
        default="",
        help="Write JSON report with full provenance to this path",
    )
    parser.add_argument(
        "--raw-dir",
        default="",
        help="Write per-scenario raw JSON artifacts to this directory",
    )
    return parser.parse_args()


def _scenario_set_label(tag: str) -> str:
    return tag if tag else "all"


def _resolve_scenarios(tag: str) -> list:
    scenarios = all_scenarios()
    if tag:
        scenarios = [s for s in scenarios if tag in s.tags]
    return scenarios


def _write_raw_artifact(raw_dir: str, result_dict: dict) -> None:
    Path(raw_dir).mkdir(parents=True, exist_ok=True)
    scenario_id = result_dict.get("scenario_id", "unknown")
    path = Path(raw_dir) / f"{scenario_id}.json"
    with open(path, "w") as f:
        json.dump(result_dict, f, indent=2)


def main() -> None:
    args = _parse_args()

    scenarios = _resolve_scenarios(args.tag)

    if args.list:
        for s in scenarios:
            print(f"  {s.id:40s} tags={s.tags}")
        print(f"\n{len(scenarios)} scenarios, tags: {ALL_TAGS}")
        return

    if not args.backend:
        print(
            "error: --backend is required (mock | minimax | openai_compat). "
            "There is no silent fallback.",
            file=sys.stderr,
        )
        sys.exit(2)

    if not scenarios:
        print(f"error: no scenarios matched tag={args.tag!r}", file=sys.stderr)
        sys.exit(2)

    # Build backend spec + factory up front so config errors fail fast.
    try:
        spec = spec_from_args(
            backend=args.backend,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            api_key_env=args.api_key_env,
        )
        factory = build_backend_factory(spec)
    except MissingBackendConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    # Provenance: everything we know before execution.
    provenance = build_provenance(
        backend_type=spec.type,
        requested_model=spec.requested_model,
        resolved_model=spec.resolved_model,
        base_url=spec.resolved_base_url,
        scenario_set=_scenario_set_label(args.tag),
        scenario_ids=[s.id for s in scenarios],
    )

    # Execute.
    t_start = time.perf_counter()
    report = run_scenarios(scenarios, factory)
    elapsed = time.perf_counter() - t_start

    # Fill in execution counters now that we have results.
    total_turns = sum(len(r.turn_results) for r in report.results)
    llm_calls = sum(r.metrics.llm_call_count for r in report.results)
    failures = sum(1 for r in report.results if r.errored)
    provenance.finished_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    provenance.total_turns = total_turns
    provenance.llm_calls = llm_calls
    provenance.failures = failures
    provenance.total_latency_s = round(elapsed, 3)
    report.provenance = provenance

    # Per-scenario raw artifacts, if requested.
    if args.raw_dir:
        from evals.reporting import _result_to_dict  # noqa: PLC2701 — intentional internal use

        for result in report.results:
            _write_raw_artifact(args.raw_dir, _result_to_dict(result))

    # Main output.
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report, "w") as f:
            f.write(json_report(report))
        print(f"Wrote report: {args.report}")

    if args.json:
        print(json_report(report))
    else:
        print(text_report(report))

    # Exit code: non-zero if any scenario failed or errored.
    if report.failed_scenarios > 0 or failures > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
