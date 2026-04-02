"""Evaluation types — scenario definitions, assertions, and results.

All types are plain dataclasses. No external dependencies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Assertions — behavioral checks (not string matching)
# ---------------------------------------------------------------------------

class AssertionKind(str, Enum):
    """What an assertion checks."""
    TOOL_USED = "tool_used"
    TOOL_NOT_USED = "tool_not_used"
    TOOL_CATEGORY = "tool_category"
    DECISION = "decision"                     # execute / clarify / defer / refuse
    MODULATOR_RANGE = "modulator_range"
    UNRESOLVED_CREATED = "unresolved_created"
    UNRESOLVED_RESOLVED = "unresolved_resolved"
    TASK_PLAN_CREATED = "task_plan_created"
    TASK_PLAN_CONTINUED = "task_plan_continued"
    TASK_PLAN_COMPLETED = "task_plan_completed"
    PROACTIVE_TRIGGERED = "proactive_triggered"
    PROACTIVE_SUPPRESSED = "proactive_suppressed"
    DEBUG_FIELD = "debug_field"               # arbitrary debug field check
    RESPONSE_CONTAINS = "response_contains"
    RESPONSE_NOT_EMPTY = "response_not_empty"
    CUSTOM = "custom"                         # callable-based check


@dataclass
class ModulatorRange:
    """Allowed range for a modulator value (inclusive)."""
    name: str
    low: float
    high: float

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high


@dataclass
class EvalAssertion:
    """A single behavioral assertion on a turn's output.

    ``kind`` determines what is checked. ``params`` holds kind-specific
    configuration (e.g. modulator name, expected tool category).
    """
    kind: AssertionKind
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""


# ---------------------------------------------------------------------------
# Scenario structure
# ---------------------------------------------------------------------------

@dataclass
class EvalTurn:
    """A single conversation turn in an eval scenario.

    ``user_message`` is sent through the pipeline. Assertions are checked
    against the pipeline response + debug state.
    """
    user_message: str
    assertions: list[EvalAssertion] = field(default_factory=list)
    user_id: str = "eval_user"


@dataclass
class EvalScenario:
    """A complete evaluation scenario.

    Describes initial conditions, conversation turns with assertions,
    and optional proactive checks.
    """
    id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)

    # Initial state overrides (applied before first turn)
    initial_modulators: dict[str, float] | None = None
    initial_trust: float | None = None

    # Conversation turns
    turns: list[EvalTurn] = field(default_factory=list)

    # Whether to check proactive behavior after the last turn
    check_proactive: bool = False
    proactive_assertions: list[EvalAssertion] = field(default_factory=list)
    proactive_idle_seconds: float = 600.0

    # Tool executor: whether to wire a ToolExecutor for this scenario
    with_tools: bool = False


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class AssertionResult:
    """Outcome of a single assertion check."""
    assertion: EvalAssertion
    passed: bool
    actual: Any = None
    message: str = ""


@dataclass
class TurnResult:
    """Result of processing one turn."""
    turn_index: int
    user_message: str
    response: str
    assertion_results: list[AssertionResult] = field(default_factory=list)
    modulator_snapshot: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.assertion_results)


@dataclass
class EvalMetrics:
    """Performance counters captured during scenario execution."""
    llm_call_count: int = 0
    tool_call_count: int = 0
    total_latency_ms: float = 0.0
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    proactive_count: int = 0
    task_loop_count: int = 0
    unresolved_items_created: int = 0
    defense_activations: int = 0


@dataclass
class EvalResult:
    """Full result of running one scenario."""
    scenario_id: str
    scenario_name: str
    tags: list[str] = field(default_factory=list)
    turn_results: list[TurnResult] = field(default_factory=list)
    proactive_results: list[AssertionResult] = field(default_factory=list)
    metrics: EvalMetrics = field(default_factory=EvalMetrics)
    elapsed_ms: float = 0.0

    @property
    def passed(self) -> bool:
        turns_ok = all(t.passed for t in self.turn_results)
        proactive_ok = all(r.passed for r in self.proactive_results)
        return turns_ok and proactive_ok

    @property
    def total_assertions(self) -> int:
        count = sum(len(t.assertion_results) for t in self.turn_results)
        count += len(self.proactive_results)
        return count

    @property
    def failed_assertions(self) -> int:
        count = sum(
            1 for t in self.turn_results
            for r in t.assertion_results if not r.passed
        )
        count += sum(1 for r in self.proactive_results if not r.passed)
        return count


@dataclass
class EvalReport:
    """Aggregated report across multiple scenarios."""
    results: list[EvalResult] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def total_scenarios(self) -> int:
        return len(self.results)

    @property
    def passed_scenarios(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed_scenarios(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def total_assertions(self) -> int:
        return sum(r.total_assertions for r in self.results)

    @property
    def failed_assertions(self) -> int:
        return sum(r.failed_assertions for r in self.results)
