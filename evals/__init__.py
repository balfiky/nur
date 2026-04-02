"""Evaluation and benchmark harness for Project Nūr.

Provides structured scenario definitions, a deterministic runner,
assertion-based behavioral checks, and text/JSON reporting.
"""

from evals.types import (
    EvalAssertion,
    EvalMetrics,
    EvalReport,
    EvalResult,
    EvalScenario,
    EvalTurn,
    ModulatorRange,
)
from evals.runner import run_scenario, run_scenarios, run_by_tag
from evals.reporting import text_report, json_report
