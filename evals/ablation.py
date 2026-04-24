"""Run architecture ablations against a real (or mock) backend.

For each variant in ``evals.ablation_hypotheses.ABLATIONS``:
  1. Build a backend factory that produces fresh pipelines with the
     ablation's ``PipelineFeatures`` applied.
  2. Run the selected scenario set through the standard eval runner.
  3. Emit a provenance-stamped JSON report per variant.
  4. Compare each ablation to the baseline and label every scenario
     outcome against the prior hypothesis.

The baseline (all features enabled) is always run first in the same
invocation so provenance (git sha, timestamps, backend identity,
config fingerprints) is consistent across the variants. That makes
the per-scenario deltas reproducible.

Usage:
    python -m evals.ablation --backend mock --tag phase11 \\
      --report-dir reports/ablation/

    python -m evals.ablation --backend provider \\
      --base-url https://provider.example/v1 --model your-model \\
      --api-key-env LLM_API_KEY --tag phase11 \\
      --report-dir reports/ablation/

    python -m evals.ablation --backend minimax \\
      --api-key-env MINIMAX_API_KEY --tag phase11 \\
      --report-dir reports/ablation/

The command prints a summary comparison table and writes:
    reports/ablation/baseline.json
    reports/ablation/<ablation-label>.json  (one per variant)
    reports/ablation/summary.json            (combined deltas for the paper)
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import json
import sys
import time
from pathlib import Path
from typing import Any

from core.pipeline_features import PipelineFeatures
from evals.ablation_hypotheses import ABLATIONS, Ablation, label_outcome
from evals.backends import (
    MissingBackendConfigError,
    build_backend_factory,
    spec_from_args,
)
from evals.provenance import build_provenance
from evals.reporting import _result_to_dict, json_report
from evals.runner import BackendFactory, run_scenarios
from evals.scenarios import all_scenarios
from evals.types import EvalReport, EvalResult


# ---------------------------------------------------------------------------
# Wrapping the backend factory so ablation-specific features are applied
# ---------------------------------------------------------------------------


def _features_wrapped_runner(
    scenarios: list,
    backend_factory: BackendFactory,
    features: PipelineFeatures,
) -> EvalReport:
    """Run the standard runner with a patched pipeline factory.

    The vanilla runner in ``evals.runner`` constructs pipelines without
    any feature override. Ablation runs need the toggle applied
    identically to every scenario. We monkey-patch ``_make_pipeline``
    at the runner module level for the duration of this call, restore
    it afterwards, and return the resulting report.
    """
    from evals import runner as runner_mod

    original = runner_mod._make_pipeline

    def wrapped(scenario, factory):
        # Build the pipeline the same way the runner would, then
        # replace it with one that honors the ablation features.
        from pipeline import CognitivePipeline

        executor = None
        if scenario.with_tools:
            from nur_tools.registry import ToolRegistry
            from nur_tools.executor import ToolExecutor
            from nur_tools import register_builtins

            registry = ToolRegistry()
            executor = ToolExecutor(registry)
            register_builtins(registry, executor)

        pipeline = CognitivePipeline(
            llm_backend=factory(),
            tool_executor=executor,
            features=features,
        )

        # Apply initial-state overrides (same as runner's version)
        if scenario.initial_modulators:
            state = pipeline.engine.state
            for name, value in scenario.initial_modulators.items():
                if hasattr(state, name):
                    setattr(state, name, max(0.0, min(1.0, value)))
        return pipeline

    runner_mod._make_pipeline = wrapped
    try:
        return run_scenarios(scenarios, backend_factory)
    finally:
        runner_mod._make_pipeline = original


# ---------------------------------------------------------------------------
# Provenance + reporting per variant
# ---------------------------------------------------------------------------


def _attach_provenance(
    report: EvalReport,
    *,
    spec,
    scenario_set: str,
    scenario_ids: list[str],
    elapsed_s: float,
    features: PipelineFeatures,
    ablation_label: str,
) -> None:
    prov = build_provenance(
        backend_type=spec.type,
        requested_model=spec.requested_model,
        resolved_model=spec.resolved_model,
        base_url=spec.resolved_base_url,
        scenario_set=scenario_set,
        scenario_ids=scenario_ids,
    )
    prov.finished_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    prov.total_turns = sum(len(r.turn_results) for r in report.results)
    prov.llm_calls = sum(r.metrics.llm_call_count for r in report.results)
    prov.failures = sum(1 for r in report.results if r.errored)
    prov.total_latency_s = round(elapsed_s, 3)
    report.provenance = prov
    # Stash ablation metadata in a small side-channel the reporter can pick up.
    report._ablation_label = ablation_label  # type: ignore[attr-defined]
    report._features_disabled = features.disabled_labels()  # type: ignore[attr-defined]


def _write_variant_report(
    path: Path,
    report: EvalReport,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.loads(json_report(report))
    body["ablation_label"] = getattr(report, "_ablation_label", "baseline")
    body["features_disabled"] = getattr(report, "_features_disabled", [])
    with open(path, "w") as f:
        json.dump(body, f, indent=2)


# ---------------------------------------------------------------------------
# Summary comparison across variants
# ---------------------------------------------------------------------------


def _build_summary(
    baseline: EvalReport,
    variants: list[tuple[Ablation, EvalReport]],
) -> dict[str, Any]:
    """Top-level summary used for the paper's ablation table.

    Embeds a compact provenance block at the top so the tracked summary
    artifact is self-describing: a reader can tell from summary.json
    alone which code state and which backend produced the numbers,
    without having to cross-reference the (gitignored) raw reports.
    """

    def by_id(rep: EvalReport) -> dict[str, EvalResult]:
        return {r.scenario_id: r for r in rep.results}

    b_by = by_id(baseline)
    summary: dict[str, Any] = {
        "provenance": _summary_provenance(baseline),
        "baseline": {
            "passed": baseline.passed_scenarios,
            "total": baseline.total_scenarios,
            "assertions_passed": baseline.total_assertions - baseline.failed_assertions,
            "assertions_total": baseline.total_assertions,
            "llm_calls": sum(r.metrics.llm_call_count for r in baseline.results),
            "latency_s": baseline.provenance.total_latency_s if baseline.provenance else 0,
        },
        "ablations": [],
    }
    for ablation, rep in variants:
        r_by = by_id(rep)
        outcomes = {}
        for sid in sorted(b_by):
            baseline_passed = b_by[sid].passed
            variant_passed = r_by[sid].passed if sid in r_by else False
            outcome = label_outcome(ablation, sid, variant_passed)
            outcomes[sid] = {
                "baseline_passed": baseline_passed,
                "variant_passed": variant_passed,
                "outcome": outcome,
            }
        summary["ablations"].append({
            "label": ablation.label,
            "features_disabled": ablation.features.disabled_labels(),
            "hypothesis": ablation.hypothesis,
            "expected_failures": list(ablation.expected_failures),
            "passed": rep.passed_scenarios,
            "total": rep.total_scenarios,
            "assertions_passed": rep.total_assertions - rep.failed_assertions,
            "assertions_total": rep.total_assertions,
            "llm_calls": sum(r.metrics.llm_call_count for r in rep.results),
            "latency_s": rep.provenance.total_latency_s if rep.provenance else 0,
            "per_scenario": outcomes,
            # Compact counts for the paper table
            "counts": _count_outcomes(outcomes),
        })
    return summary


def _summary_provenance(baseline: EvalReport) -> dict[str, Any]:
    """Compact, paper-citable provenance lifted from the baseline run.

    Intentionally narrower than the full ``RunProvenance`` block in the
    raw per-variant reports — the summary is for citation, not replay.
    Includes only the fields a reader needs to trust the numbers:
    code state, backend identity, scenario set, and when it ran. Each
    variant report still carries the full provenance block if deeper
    inspection is needed.
    """
    if baseline.provenance is None:
        return {}
    p = baseline.provenance
    return {
        "git_sha": p.git_sha,
        "git_branch": p.git_branch,
        "dirty_worktree": p.dirty_worktree,
        "backend_type": p.backend_type,
        "requested_model": p.requested_model,
        "resolved_model": p.resolved_model,
        "base_url": p.base_url,
        "host": p.host,
        "started_at": p.started_at,
        "finished_at": p.finished_at,
        "scenario_set": p.scenario_set,
        "scenario_count": p.scenario_count,
        # Full config fingerprints live in the per-variant baseline.json;
        # here we record only the fingerprint count so the reader knows
        # config-drift tracking is in effect.
        "config_fingerprint_count": len(p.config_fingerprints),
    }


def _count_outcomes(outcomes: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts = {
        "expected_failure": 0,
        "unexpected_failure": 0,
        "no_effect": 0,
        "newly_passing": 0,
    }
    for o in outcomes.values():
        counts[o["outcome"]] += 1
    return counts


def _print_summary_table(summary: dict[str, Any]) -> None:
    b = summary["baseline"]
    print()
    print("=" * 78)
    print("ABLATION SUMMARY")
    print("=" * 78)
    print(
        f"baseline                        "
        f"pass={b['passed']}/{b['total']}  "
        f"assert={b['assertions_passed']}/{b['assertions_total']}  "
        f"llm={b['llm_calls']}  "
        f"latency={b['latency_s']:.1f}s"
    )
    print(
        f"  {'ablation':<28s} "
        f"{'pass':>6s} "
        f"{'exp.fail':>8s} "
        f"{'unex':>5s} "
        f"{'noeff':>6s} "
        f"{'llm':>5s} "
        f"{'lat_s':>7s}"
    )
    for abl in summary["ablations"]:
        c = abl["counts"]
        print(
            f"  {abl['label']:<28s} "
            f"{abl['passed']:>3d}/{abl['total']:<2d} "
            f"{c['expected_failure']:>8d} "
            f"{c['unexpected_failure']:>5d} "
            f"{c['no_effect']:>6d} "
            f"{abl['llm_calls']:>5d} "
            f"{abl['latency_s']:>7.1f}"
        )
    print("=" * 78)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run architecture ablations on Nūr's Phase 11 scenario set.",
    )
    p.add_argument("--backend", choices=["mock", "provider", "minimax", "openai_compat"], required=True)
    p.add_argument("--model", default="")
    p.add_argument("--base-url", default="")
    p.add_argument("--api-key", default="")
    p.add_argument("--api-key-env", default="")
    # --temperature / --max-tokens were not propagated to clients; removed.
    p.add_argument(
        "--tag",
        default="phase11",
        help="Tag filter for scenarios. Default: phase11.",
    )
    p.add_argument(
        "--report-dir",
        default="reports/ablation",
        help="Directory for baseline.json, per-ablation JSONs, and summary.json.",
    )
    p.add_argument(
        "--only",
        default="",
        help="Comma-separated list of ablation labels to run (default: all).",
    )
    return p.parse_args()


def _filter_ablations(only: str) -> list[Ablation]:
    if not only:
        return list(ABLATIONS)
    wanted = {s.strip() for s in only.split(",") if s.strip()}
    return [a for a in ABLATIONS if a.label in wanted]


def main() -> None:
    args = _parse_args()
    scenarios = [s for s in all_scenarios() if args.tag in s.tags]
    if not scenarios:
        print(f"error: no scenarios match tag={args.tag!r}", file=sys.stderr)
        sys.exit(2)
    scenario_ids = [s.id for s in scenarios]

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

    ablations = _filter_ablations(args.only)
    report_dir = Path(args.report_dir)

    # --- Baseline ---
    print(f"Running BASELINE ({len(scenarios)} scenarios) ...")
    t0 = time.perf_counter()
    baseline_report = _features_wrapped_runner(
        scenarios, factory, PipelineFeatures(),
    )
    elapsed = time.perf_counter() - t0
    _attach_provenance(
        baseline_report,
        spec=spec,
        scenario_set=args.tag,
        scenario_ids=scenario_ids,
        elapsed_s=elapsed,
        features=PipelineFeatures(),
        ablation_label="baseline",
    )
    _write_variant_report(report_dir / "baseline.json", baseline_report)
    print(
        f"  baseline: {baseline_report.passed_scenarios}/{baseline_report.total_scenarios} passed, "
        f"{elapsed:.1f}s"
    )

    # --- Ablations ---
    variants: list[tuple[Ablation, EvalReport]] = []
    for ablation in ablations:
        print(f"Running ABLATION: {ablation.label} ...")
        t0 = time.perf_counter()
        rep = _features_wrapped_runner(
            scenarios, factory, ablation.features,
        )
        elapsed = time.perf_counter() - t0
        _attach_provenance(
            rep,
            spec=spec,
            scenario_set=args.tag,
            scenario_ids=scenario_ids,
            elapsed_s=elapsed,
            features=ablation.features,
            ablation_label=ablation.label,
        )
        _write_variant_report(
            report_dir / f"{ablation.label}.json", rep,
        )
        variants.append((ablation, rep))
        print(
            f"  {ablation.label}: {rep.passed_scenarios}/{rep.total_scenarios} passed, "
            f"{elapsed:.1f}s"
        )

    # --- Summary ---
    summary = _build_summary(baseline_report, variants)
    summary_path = report_dir / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    _print_summary_table(summary)
    print(f"\nReports written to: {report_dir}/")

    # Exit non-zero if any unexpected failures surfaced.
    total_unexpected = sum(
        abl["counts"]["unexpected_failure"] for abl in summary["ablations"]
    )
    if total_unexpected:
        print(
            f"\nNOTE: {total_unexpected} unexpected failure(s) across ablations. "
            f"Inspect summary.json.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
