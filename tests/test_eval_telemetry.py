"""Tests guarding eval-telemetry honesty.

Specifically: ``llm_call_count`` must reflect every real ``generate()``
invocation, including self-check-driven regenerations. The previous
implementation added ``dialogue_trace.total_llm_calls + 1`` per turn,
which silently undercounted whenever the self-check forced a retry.

These tests lock in the new behavior so the bug cannot regress.
"""

from __future__ import annotations

import pytest

from core.dual_process.generator import LLMBackend, MockLLMBackend
from core.dual_process.self_check import SelfCheckResult
from evals.instrumented_backend import BackendCounter, InstrumentedBackend
from evals.runner import run_scenario
from evals.types import EvalScenario, EvalTurn


class _FakeBackend:
    """Plain backend that records every call argument and returns canned text."""

    def __init__(self, response: str = "ok") -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        return self.response


class TestInstrumentedBackend:
    def test_increments_counter_per_call(self):
        counter = BackendCounter()
        wrapped = InstrumentedBackend(_FakeBackend(), counter)
        wrapped.generate("sys", "msg1")
        wrapped.generate("sys", "msg2")
        wrapped.generate("sys", "msg3")
        assert counter.generate_calls == 3

    def test_forwards_call_to_inner(self):
        counter = BackendCounter()
        inner = _FakeBackend(response="hello")
        wrapped = InstrumentedBackend(inner, counter)
        result = wrapped.generate("sys", "hi")
        assert result == "hello"
        assert inner.calls == [("sys", "hi")]

    def test_close_forwards_to_inner(self):
        counter = BackendCounter()
        closed = []

        class Closeable:
            def generate(self, *_a, **_kw):
                return ""

            def close(self):
                closed.append(True)

        wrapped = InstrumentedBackend(Closeable(), counter)
        wrapped.close()
        assert closed == [True]

    def test_attribute_passthrough_to_inner(self):
        """MockLLMBackend exposes ``last_system_prompt``; the wrapper must
        not hide it."""
        counter = BackendCounter()
        inner = MockLLMBackend(response="x")
        wrapped = InstrumentedBackend(inner, counter)
        wrapped.generate("captured-sys", "msg")
        assert wrapped.last_system_prompt == "captured-sys"


class TestSharedCounterAcrossBackends:
    """A scenario can build multiple backends; one counter aggregates them."""

    def test_two_backends_share_one_counter(self):
        counter = BackendCounter()
        a = InstrumentedBackend(_FakeBackend(), counter)
        b = InstrumentedBackend(_FakeBackend(), counter)
        a.generate("s", "1")
        a.generate("s", "2")
        b.generate("s", "3")
        assert counter.generate_calls == 3

    def test_factory_pattern_produces_aggregated_count(self):
        """Mimics how the runner threads the counter: a single counter
        plus a factory that wraps every fresh backend."""
        counter = BackendCounter()

        def real_factory():
            return _FakeBackend()

        def counting_factory():
            return InstrumentedBackend(real_factory(), counter)

        # Three "pipelines" each get a fresh backend.
        backends = [counting_factory() for _ in range(3)]
        for b in backends:
            b.generate("sys", "msg")
        assert counter.generate_calls == 3


class TestRunnerSelfCheckRegression:
    """The cardinal regression: self-check failure → second generator call.

    Locks the fix for the hardcoded ``+1`` undercount. Before the fix this
    test would have read llm_call_count == 1 even though the backend was
    invoked twice. Now it must read 2.
    """

    def test_self_check_failure_counts_both_generations(self, monkeypatch):
        from evals import runner as runner_mod

        original_make = runner_mod._make_pipeline

        def make_with_failing_check(scenario, factory):
            pipeline = original_make(scenario, factory)
            # Force exactly one self-check failure, then pass on retry.
            calls = {"n": 0}

            def failing_check(response, ctx):
                calls["n"] += 1
                if calls["n"] == 1:
                    return SelfCheckResult(
                        passed=False,
                        issues=["forced for test"],
                        correction_note="please revise",
                    )
                return SelfCheckResult(passed=True)

            pipeline.self_checker.check = failing_check
            # Force the rule-based checker (not the LLM checker) so the
            # retry path is deterministic and doesn't add LLM calls of
            # its own from the self-check side.
            pipeline._self_check_llm = pipeline.self_checker
            return pipeline

        monkeypatch.setattr(runner_mod, "_make_pipeline", make_with_failing_check)

        scenario = EvalScenario(
            id="self_check_retry_smoke",
            name="self-check retry forces second generation",
            turns=[EvalTurn(user_message="hi", user_id="alice")],
        )

        result = run_scenario(
            scenario,
            backend_factory=lambda: MockLLMBackend(response="ok"),
        )

        # One turn, but two backend calls: the original generation + the
        # self-check-driven regeneration. The previous (broken) counter
        # would have reported 1.
        assert result.metrics.llm_call_count == 2, (
            f"Expected 2 generator calls (original + self-check retry), "
            f"got {result.metrics.llm_call_count}. "
            f"This means the hardcoded +1 counting bug has regressed."
        )

    def test_passing_self_check_counts_one_generation(self, monkeypatch):
        """Sanity check: when self-check passes, only one generation."""
        from evals import runner as runner_mod

        original_make = runner_mod._make_pipeline

        def make_with_passing_check(scenario, factory):
            pipeline = original_make(scenario, factory)
            pipeline.self_checker.check = lambda response, ctx: SelfCheckResult(passed=True)
            pipeline._self_check_llm = pipeline.self_checker
            return pipeline

        monkeypatch.setattr(runner_mod, "_make_pipeline", make_with_passing_check)

        scenario = EvalScenario(
            id="self_check_pass_smoke",
            name="self-check pass keeps count to one",
            turns=[EvalTurn(user_message="hi", user_id="alice")],
        )

        result = run_scenario(
            scenario,
            backend_factory=lambda: MockLLMBackend(response="ok"),
        )

        assert result.metrics.llm_call_count == 1
