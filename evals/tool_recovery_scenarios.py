"""Tool-failure recovery eval scenarios — T14.

Three scenarios that verify behavioral change (clarification vs. execution)
when repeated write-tool failures erode certainty and saturate resolution.

Key mechanics:
- Each write failure applies certainty -0.10 and resolution +0.10.
- After 3 failures: certainty drops from 0.50→0.20, resolution saturates at 1.0.
- On turn 4: caution = low_certainty*0.45 + resolution*0.15 + seek_action*0.15
              = 0.80*0.45 + 1.0*0.15 + 0.15 = 0.66 > 0.65 → slow_down → clarify.

Run: python -m evals --backend mock --tag tool_recovery
"""

from __future__ import annotations

from evals.types import (
    AssertionKind,
    EvalAssertion,
    EvalScenario,
    EvalTurn,
)


def _tool_used(name: str) -> EvalAssertion:
    return EvalAssertion(
        kind=AssertionKind.TOOL_USED,
        params={"tool_name": name},
        description=f"{name} was executed",
    )


def _decision(d: str) -> EvalAssertion:
    return EvalAssertion(
        kind=AssertionKind.DECISION,
        params={"decision": d},
        description=f"tool decision == {d!r}",
    )


def _not_empty() -> EvalAssertion:
    return EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY)


def _mod_range(name: str, low: float, high: float, desc: str = "") -> EvalAssertion:
    return EvalAssertion(
        kind=AssertionKind.MODULATOR_RANGE,
        params={"name": name, "low": low, "high": high},
        description=desc or f"{name} in [{low}, {high}]",
    )


# /dev/null is a char device, not a directory.
# makedirs("/dev/null", exist_ok=True) raises NotADirectoryError,
# which the executor catches and returns as ToolResult(success=False).
# "Not a directory" is NOT matched by the operational-error regex, so
# full emotional deltas (certainty -0.10) are applied.
#
# "please" triggers action_request → social_move="request" → inferred_intent="seek_action",
# adding the +0.15 caution bonus needed for slow_down to fire on turn 4.
_FAILING_PATH = "/dev/null/nur_t14_test.txt"
_WRITE_MSG = f"please write 'test content' to {_FAILING_PATH}"


def tool_recovery_scenarios() -> list[EvalScenario]:
    """Three tool-failure recovery scenarios."""
    return [
        # 1. Behavioral contrast: high-certainty state executes write tools.
        #    This is the baseline against which scenario 2 is compared.
        EvalScenario(
            id="tool_recovery_high_certainty_executes",
            name="High certainty + trust: write tool is attempted (executed)",
            tags=["tool_recovery", "regression"],
            initial_modulators={"certainty": 0.90},
            initial_trust=0.9,
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message=_WRITE_MSG,
                    assertions=[
                        _not_empty(),
                        _tool_used("fs.write_file"),
                    ],
                ),
            ],
        ),

        # 2. Low certainty + low trust: write tool is clarified immediately.
        #    certainty=0.10, trust=0.0 → ct = 0.5 + 0.40*0.3 + 0.50*0.2 = 0.72 > 0.65.
        EvalScenario(
            id="tool_recovery_low_certainty_clarifies",
            name="Low certainty + no trust: write tool is clarified, not executed",
            tags=["tool_recovery", "regression"],
            initial_modulators={"certainty": 0.10},
            initial_trust=0.0,
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message=_WRITE_MSG,
                    assertions=[
                        _not_empty(),
                        _decision("clarify"),
                    ],
                ),
            ],
        ),

        # 3. Three write failures → 4th attempt is clarified.
        #    initial_modulators: certainty=0.5, resolution=0.8.
        #    Each failure: certainty -0.10, resolution +0.10 (capped at 1.0).
        #    Caution (turn 4) = (1-0.20)*0.45 + 1.0*0.15 + 0.15 = 0.66 > 0.65
        #    → agency slow_down → WRITE clarified.
        EvalScenario(
            id="tool_recovery_three_failures_then_clarify",
            name="3 write failures raise caution above threshold; 4th is clarified",
            tags=["tool_recovery", "regression"],
            initial_modulators={"certainty": 0.5, "resolution": 0.8},
            with_tools=True,
            turns=[
                EvalTurn(
                    user_message=_WRITE_MSG,
                    assertions=[
                        _tool_used("fs.write_file"),
                    ],
                ),
                EvalTurn(
                    user_message=_WRITE_MSG,
                    assertions=[
                        _tool_used("fs.write_file"),
                    ],
                ),
                EvalTurn(
                    user_message=_WRITE_MSG,
                    assertions=[
                        _tool_used("fs.write_file"),
                        _mod_range("certainty", 0.0, 0.30,
                                   "Certainty eroded to below 0.30 after 3 failures"),
                    ],
                ),
                EvalTurn(
                    user_message=_WRITE_MSG,
                    assertions=[
                        _not_empty(),
                        _decision("clarify"),
                    ],
                ),
            ],
        ),
    ]
