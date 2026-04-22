"""Evaluation runner — executes scenarios through the real pipeline."""

from __future__ import annotations

import time
from typing import Any, Callable

from core.dual_process.generator import LLMBackend, MockLLMBackend
from core.types import ModulatorState
from evals.instrumented_backend import BackendCounter, InstrumentedBackend
from evals.types import (
    AssertionKind,
    AssertionResult,
    EvalAssertion,
    EvalMetrics,
    EvalReport,
    EvalResult,
    EvalScenario,
    TurnResult,
)
from pipeline import CognitivePipeline, DebugState, PipelineResponse


BackendFactory = Callable[[], LLMBackend]


def _default_backend_factory() -> LLMBackend:
    """Fallback used only when callers don't supply a factory.

    Programmatic callers (e.g. pytest) can rely on this; the CLI path
    always passes an explicit factory, so a silent mock at the CLI is
    not possible.
    """
    return MockLLMBackend()


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------

def _make_pipeline(
    scenario: EvalScenario,
    backend_factory: BackendFactory,
) -> CognitivePipeline:
    """Create a fresh pipeline configured for a scenario."""
    executor = None
    if scenario.with_tools:
        from tools.registry import ToolRegistry
        from tools.executor import ToolExecutor
        from tools import register_builtins

        registry = ToolRegistry()
        executor = ToolExecutor(registry)
        register_builtins(registry, executor)

    pipeline = CognitivePipeline(
        llm_backend=backend_factory(),
        tool_executor=executor,
    )

    # Apply initial state overrides
    if scenario.initial_modulators:
        state = pipeline.engine.state
        for name, value in scenario.initial_modulators.items():
            if hasattr(state, name):
                setattr(state, name, max(0.0, min(1.0, value)))

    return pipeline


# ---------------------------------------------------------------------------
# Assertion checking
# ---------------------------------------------------------------------------

def _check_assertion(
    assertion: EvalAssertion,
    response: PipelineResponse,
    pipeline: CognitivePipeline,
) -> AssertionResult:
    """Evaluate a single assertion against pipeline output."""
    debug = response.debug
    kind = assertion.kind
    params = assertion.params

    if kind == AssertionKind.TOOL_USED:
        tool_name = params.get("tool_name")
        trace = debug.tool_trace
        if trace is None or not trace.executed_results:
            return AssertionResult(assertion, False, None, "No tool executed")
        executed_names = [r.tool_name for r in trace.executed_results]
        if tool_name:
            ok = tool_name in executed_names
            return AssertionResult(assertion, ok, executed_names,
                                  f"Expected {tool_name} in {executed_names}")
        return AssertionResult(assertion, True, executed_names)

    if kind == AssertionKind.TOOL_NOT_USED:
        trace = debug.tool_trace
        if trace is None or not trace.executed_results:
            return AssertionResult(assertion, True, None)
        executed_names = [r.tool_name for r in trace.executed_results]
        return AssertionResult(assertion, False, executed_names,
                              f"Expected no tools but got {executed_names}")

    if kind == AssertionKind.TOOL_CATEGORY:
        expected = params.get("category")
        trace = debug.tool_trace
        if trace is None or not trace.executed_results:
            return AssertionResult(assertion, False, None, "No tool executed")
        # Check the first executed tool's category via registry
        tool_name = trace.executed_results[0].tool_name
        if pipeline._tool_executor:
            cap = pipeline._tool_executor._registry.get(tool_name)
            if cap and cap.category.value == expected:
                return AssertionResult(assertion, True, cap.category.value)
            actual = cap.category.value if cap else "unknown"
            return AssertionResult(assertion, False, actual,
                                  f"Expected category {expected}, got {actual}")
        return AssertionResult(assertion, False, None, "No tool executor")

    if kind == AssertionKind.DECISION:
        expected = params.get("decision")
        trace = debug.tool_trace
        if trace is None or trace.final_decision is None:
            actual = "none"
        else:
            actual = trace.final_decision.decision
        ok = actual == expected
        return AssertionResult(assertion, ok, actual,
                              f"Expected decision '{expected}', got '{actual}'")

    if kind == AssertionKind.MODULATOR_RANGE:
        name = params["name"]
        low = params["low"]
        high = params["high"]
        snapshot = debug.modulator_snapshot
        actual = snapshot.get(name, -1.0)
        ok = low <= actual <= high
        return AssertionResult(
            assertion, ok, actual,
            f"{name}={actual:.3f} not in [{low:.3f}, {high:.3f}]",
        )

    if kind == AssertionKind.UNRESOLVED_CREATED:
        count = debug.unresolved_count
        min_count = params.get("min_count", 1)
        ok = count >= min_count
        return AssertionResult(assertion, ok, count,
                              f"Expected >= {min_count} unresolved, got {count}")

    if kind == AssertionKind.UNRESOLVED_RESOLVED:
        count = debug.unresolved_count
        max_count = params.get("max_count", 0)
        ok = count <= max_count
        return AssertionResult(assertion, ok, count,
                              f"Expected <= {max_count} unresolved, got {count}")

    if kind == AssertionKind.TASK_PLAN_CREATED:
        trace = debug.task_trace
        ok = trace is not None and trace.plan is not None
        return AssertionResult(assertion, ok, trace is not None,
                              "No task plan created")

    if kind == AssertionKind.TASK_PLAN_CONTINUED:
        trace = debug.task_trace
        ok = trace is not None and trace.steps_executed > 0
        actual = trace.steps_executed if trace else 0
        return AssertionResult(assertion, ok, actual,
                              f"Expected task continuation, got {actual} steps")

    if kind == AssertionKind.TASK_PLAN_COMPLETED:
        trace = debug.task_trace
        ok = trace is not None and trace.plan_outcome == "completed"
        actual = trace.plan_outcome if trace else ""
        return AssertionResult(assertion, ok, actual,
                              f"Expected plan completed, got '{actual}'")

    if kind == AssertionKind.PROACTIVE_TRIGGERED:
        pt = debug.proactive_trace
        ok = pt is not None and pt.action_taken is not None
        return AssertionResult(assertion, ok, pt is not None,
                              "Proactive action not triggered")

    if kind == AssertionKind.PROACTIVE_SUPPRESSED:
        pt = debug.proactive_trace
        if pt is None:
            return AssertionResult(assertion, True, None, "No proactive trace")
        ok = pt.action_taken is None
        return AssertionResult(assertion, ok, pt.action_taken is not None,
                              "Expected proactive suppressed but action was taken")

    if kind == AssertionKind.DEBUG_FIELD:
        field_name = params.get("field")
        expected_value = params.get("value")
        check_not_none = params.get("not_none", False)
        actual = getattr(debug, field_name, None)
        if check_not_none:
            ok = actual is not None
            return AssertionResult(assertion, ok, actual,
                                  f"Expected debug.{field_name} not None")
        if expected_value is not None:
            ok = actual == expected_value
            return AssertionResult(assertion, ok, actual,
                                  f"Expected debug.{field_name}={expected_value}, got {actual}")
        return AssertionResult(assertion, True, actual)

    if kind == AssertionKind.RESPONSE_CONTAINS:
        substring = params.get("substring", "")
        ok = substring.lower() in response.response.lower()
        return AssertionResult(assertion, ok, response.response[:100],
                              f"Response doesn't contain '{substring}'")

    if kind == AssertionKind.RESPONSE_NOT_EMPTY:
        ok = len(response.response.strip()) > 0
        return AssertionResult(assertion, ok, len(response.response),
                              "Response is empty")

    if kind == AssertionKind.CUSTOM:
        # params["fn"] should be callable(response, pipeline) -> bool
        fn = params.get("fn")
        if fn is None:
            return AssertionResult(assertion, False, None, "No custom fn provided")
        try:
            ok = fn(response, pipeline)
            return AssertionResult(assertion, ok, ok)
        except Exception as exc:
            return AssertionResult(assertion, False, None, str(exc))

    return AssertionResult(assertion, False, None, f"Unknown assertion kind: {kind}")


def _check_proactive_assertion(
    assertion: EvalAssertion,
    proactive_response: PipelineResponse | None,
    pipeline: CognitivePipeline,
) -> AssertionResult:
    """Check a proactive assertion (called after idle period)."""
    if assertion.kind == AssertionKind.PROACTIVE_TRIGGERED:
        if proactive_response is None:
            return AssertionResult(assertion, False, None,
                                  "No proactive response generated")
        pt = proactive_response.debug.proactive_trace
        ok = pt is not None and pt.action_taken is not None
        return AssertionResult(assertion, ok, pt is not None,
                              "Proactive action not triggered")

    if assertion.kind == AssertionKind.PROACTIVE_SUPPRESSED:
        if proactive_response is not None:
            pt = proactive_response.debug.proactive_trace
            if pt and pt.action_taken is not None:
                return AssertionResult(assertion, False, True,
                                      "Expected proactive suppressed")
        return AssertionResult(assertion, True, None)

    # Fall back to standard check if we have a response
    if proactive_response is not None:
        return _check_assertion(assertion, proactive_response, pipeline)

    return AssertionResult(assertion, False, None, "No proactive response for check")


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------

def _collect_metrics(
    responses: list[PipelineResponse],
    proactive_response: PipelineResponse | None,
    backend_counter: BackendCounter,
) -> EvalMetrics:
    """Extract performance counters from pipeline responses.

    ``llm_call_count`` is read directly from ``backend_counter`` rather than
    derived from per-response heuristics. The previous implementation added
    ``dialogue_trace.total_llm_calls + 1`` per response, which silently
    undercounted whenever the self-check triggered a regeneration.
    """
    metrics = EvalMetrics()
    all_timings: dict[str, float] = {}

    for resp in responses:
        debug = resp.debug
        # Stage timings
        for stage, ms in debug.stage_timings_ms.items():
            all_timings[stage] = all_timings.get(stage, 0.0) + ms

        # Tool calls
        if debug.tool_trace:
            metrics.tool_call_count += len(debug.tool_trace.executed_results)
            metrics.task_loop_count += debug.tool_trace.loop_count

        # Task trace
        if debug.task_trace and debug.task_trace.steps_executed:
            metrics.task_loop_count += debug.task_trace.steps_executed

        # Unresolved items
        metrics.unresolved_items_created += debug.unresolved_count

        # Defense
        if debug.defense_activation:
            metrics.defense_activations += 1

    if proactive_response:
        metrics.proactive_count += 1

    # Authoritative call count: every backend.generate() in this scenario.
    # Captures inner-dialogue rounds, generator, self-check retries, and
    # anything else that hits the LLM — without per-call-site bookkeeping.
    metrics.llm_call_count = backend_counter.generate_calls

    metrics.stage_timings_ms = all_timings
    metrics.total_latency_ms = all_timings.get("total", 0.0)

    return metrics


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenario(
    scenario: EvalScenario,
    backend_factory: BackendFactory | None = None,
) -> EvalResult:
    """Execute a single evaluation scenario and return the result."""
    factory = backend_factory or _default_backend_factory
    t_start = time.perf_counter()

    # Per-scenario backend-call counter. Every backend the scenario builds
    # (one per unique user_id) writes into this shared counter, so the
    # final llm_call_count reflects every real generate() invocation.
    counter = BackendCounter()

    def counting_factory() -> LLMBackend:
        return InstrumentedBackend(factory(), counter)

    pipelines: dict[str, CognitivePipeline] = {}

    def get_pipeline(user_id: str) -> CognitivePipeline:
        pipeline = pipelines.get(user_id)
        if pipeline is not None:
            return pipeline

        pipeline = _make_pipeline(scenario, counting_factory)
        if scenario.initial_trust is not None:
            person = pipeline.person_profiles.get_or_create(user_id)
            person.trust = scenario.initial_trust
            pipeline.person_profiles.save(person)
        pipelines[user_id] = pipeline
        return pipeline

    turn_results: list[TurnResult] = []
    responses: list[PipelineResponse] = []
    proactive_results: list[AssertionResult] = []
    proactive_response: PipelineResponse | None = None
    failure_reason = ""

    try:
        for i, turn in enumerate(scenario.turns):
            pipeline = get_pipeline(turn.user_id)
            resp = pipeline.process(turn.user_message, user_id=turn.user_id)
            responses.append(resp)

            assertion_results = [
                _check_assertion(a, resp, pipeline)
                for a in turn.assertions
            ]

            turn_results.append(TurnResult(
                turn_index=i,
                user_message=turn.user_message,
                response=resp.response,
                assertion_results=assertion_results,
                modulator_snapshot=dict(resp.debug.modulator_snapshot),
            ))

            if turn.end_session:
                pipeline.end_session(user_id=turn.user_id)

        # Proactive check
        if scenario.check_proactive and scenario.proactive_assertions:
            proactive_user_id = scenario.turns[-1].user_id if scenario.turns else "eval_user"
            pipeline = get_pipeline(proactive_user_id)
            # Simulate idle time
            pipeline._last_turn_time = time.time() - scenario.proactive_idle_seconds
            proactive_response = pipeline.process_proactive(
                user_id=proactive_user_id,
                idle_threshold=1.0,  # low threshold since we control idle_seconds
            )
            for a in scenario.proactive_assertions:
                proactive_results.append(
                    _check_proactive_assertion(a, proactive_response, pipeline)
                )
    except Exception as exc:
        # Don't let one scenario's provider error kill the whole run.
        # Record the failure on the result so the report surfaces it.
        failure_reason = f"{type(exc).__name__}: {exc}"
    finally:
        for pipeline in pipelines.values():
            pipeline.close()

    elapsed = (time.perf_counter() - t_start) * 1000
    metrics = _collect_metrics(responses, proactive_response, counter)

    return EvalResult(
        scenario_id=scenario.id,
        scenario_name=scenario.name,
        tags=list(scenario.tags),
        turn_results=turn_results,
        proactive_results=proactive_results,
        metrics=metrics,
        elapsed_ms=elapsed,
        failure_reason=failure_reason,
    )


def run_scenarios(
    scenarios: list[EvalScenario],
    backend_factory: BackendFactory | None = None,
) -> EvalReport:
    """Run multiple scenarios and produce an aggregated report.

    If ``backend_factory`` is omitted, the mock backend is used. CLI
    callers should always pass an explicit factory to avoid silent
    mock fallback (see ``evals.backends.build_backend_factory``).
    """
    report = EvalReport()
    for scenario in scenarios:
        report.results.append(run_scenario(scenario, backend_factory))
    return report


def run_by_tag(
    scenarios: list[EvalScenario],
    tag: str,
    backend_factory: BackendFactory | None = None,
) -> EvalReport:
    """Run only scenarios matching a given tag."""
    filtered = [s for s in scenarios if tag in s.tags]
    return run_scenarios(filtered, backend_factory)
