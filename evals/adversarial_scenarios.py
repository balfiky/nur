"""Adversarial state-sensitivity eval scenarios — T2.

Eight scenarios that verify the emotional state machine stays bounded
under stress: repetitive inputs, hostile escalation, long silence, and
rapid topic whiplash.

Run: python -m evals --backend mock --tag adversarial
"""

from __future__ import annotations

from evals.types import (
    AssertionKind,
    EvalAssertion,
    EvalScenario,
    EvalTurn,
)


def _not_empty() -> EvalAssertion:
    return EvalAssertion(kind=AssertionKind.RESPONSE_NOT_EMPTY)


def _mod_range(name: str, low: float, high: float, desc: str = "") -> EvalAssertion:
    return EvalAssertion(
        kind=AssertionKind.MODULATOR_RANGE,
        params={"name": name, "low": low, "high": high},
        description=desc or f"{name} in [{low}, {high}]",
    )


def _custom(fn, desc: str = "") -> EvalAssertion:
    return EvalAssertion(
        kind=AssertionKind.CUSTOM,
        params={"fn": fn},
        description=desc,
    )


def adversarial_scenarios() -> list[EvalScenario]:
    """Eight adversarial state-sensitivity scenarios."""
    return [
        # 1. Per-turn bonding delta cap is respected
        # warmth event raw delta = 0.10; normal cap = 0.05
        # Starting at 0.80, one warm turn must not exceed 0.86.
        EvalScenario(
            id="adv_bonding_cap_per_turn",
            name="Warmth event: bonding delta cap of 0.05 is respected",
            tags=["adversarial", "regression"],
            initial_modulators={"bonding": 0.80},
            turns=[
                EvalTurn(
                    user_message="Thank you so much for everything! You are wonderful!",
                    assertions=[
                        _not_empty(),
                        _mod_range("bonding", 0.80, 0.86,
                                   "Bonding increases by at most 0.06 per warm turn"),
                    ],
                ),
            ],
        ),

        # 2. Hostile turns + apology: valence partially recovers
        # 5 hostile turns drive valence down; one warm apology raises it.
        EvalScenario(
            id="adv_valence_recovery_after_hostility",
            name="5 hostile turns + apology: valence recovers above 0.08",
            tags=["adversarial", "regression"],
            initial_modulators={"valence": 0.7},
            turns=[
                EvalTurn(
                    user_message="This is terrible and I hate it!",
                    assertions=[],
                ),
                EvalTurn(
                    user_message="This is completely useless.",
                    assertions=[],
                ),
                EvalTurn(
                    user_message="I am so frustrated with this!",
                    assertions=[],
                ),
                EvalTurn(
                    user_message="Stop giving me bad answers.",
                    assertions=[],
                ),
                EvalTurn(
                    user_message="You never get anything right.",
                    assertions=[],
                ),
                EvalTurn(
                    user_message="I'm sorry for being harsh. I appreciate your help.",
                    assertions=[
                        _not_empty(),
                        _mod_range("valence", 0.08, 1.0,
                                   "Valence recovers above floor after warm apology"),
                    ],
                ),
            ],
        ),

        # 3. Repeated identical neutral messages: arousal stays bounded
        # 15 identical calm requests must not cause runaway arousal.
        EvalScenario(
            id="adv_repeated_identical_bounded",
            name="15 identical neutral messages: arousal stays below 0.85",
            tags=["adversarial", "regression"],
            turns=[
                EvalTurn(
                    user_message="Tell me something interesting.",
                    assertions=[],
                )
                for _ in range(14)
            ] + [
                EvalTurn(
                    user_message="Tell me something interesting.",
                    assertions=[
                        _mod_range("arousal", 0.0, 0.85,
                                   "Arousal bounded after 15 identical messages"),
                    ],
                ),
            ],
        ),

        # 4. Alternating positive/negative: valence oscillates but stays bounded
        # 10 alternating turns; final valence must stay in a reasonable range.
        EvalScenario(
            id="adv_mixed_affect_oscillation",
            name="10 alternating positive/negative turns: valence in [0.05, 0.95]",
            tags=["adversarial", "regression"],
            turns=[
                EvalTurn(
                    user_message="I love this! Thank you!" if i % 2 == 0 else "This is awful!",
                    assertions=[],
                )
                for i in range(10)
            ] + [
                EvalTurn(
                    user_message="I love this! Thank you!",
                    assertions=[
                        _mod_range("valence", 0.05, 0.95,
                                   "Valence stays bounded despite positive/negative oscillation"),
                    ],
                ),
            ],
        ),

        # 5. Extreme state + 90-day rest: modulators decay to near baseline
        # Half-lives: arousal=120s, valence=1800s, certainty=600s.
        # After 90*24*3600 seconds all are effectively at baseline.
        EvalScenario(
            id="adv_long_silence_decay",
            name="Extreme state + 90-day rest: arousal decays to near baseline",
            tags=["adversarial", "regression"],
            initial_modulators={"arousal": 0.9, "valence": 0.1, "certainty": 0.9},
            turns=[
                EvalTurn(
                    user_message="Goodbye for now.",
                    assertions=[
                        _custom(
                            lambda _resp, pipe: (pipe.apply_rest(90 * 24) or True),
                            "Advance 90 days of simulated rest",
                        ),
                    ],
                ),
                EvalTurn(
                    user_message="Hello again.",
                    assertions=[
                        _not_empty(),
                        _mod_range("arousal", 0.45, 0.55,
                                   "Arousal at baseline after 90-day rest"),
                        _mod_range("valence", 0.45, 0.55,
                                   "Valence at baseline after 90-day rest"),
                        _mod_range("certainty", 0.45, 0.55,
                                   "Certainty at baseline after 90-day rest"),
                    ],
                ),
            ],
        ),

        # 6. Topic whiplash: 10 unrelated topics keep all modulators in [0, 1]
        EvalScenario(
            id="adv_topic_whiplash_bounded",
            name="10 unrelated topics: all modulators stay in [0, 1]",
            tags=["adversarial", "regression"],
            turns=[
                EvalTurn(user_message=msg, assertions=[_not_empty()])
                for msg in [
                    "What's the capital of France?",
                    "I'm so angry about climate change!",
                    "Can you write me a sonnet?",
                    "I need debugging help with my Python code.",
                    "Tell me something spiritual.",
                    "I feel very sad today.",
                    "What's 2 plus 2?",
                    "I love you so much!",
                    "This is useless garbage!",
                    "Can we talk about quantum mechanics?",
                ]
            ] + [
                EvalTurn(
                    user_message="How do you feel after all that?",
                    assertions=[
                        _custom(
                            lambda _resp, pipe: all(
                                0.0 <= getattr(pipe.engine.state, name) <= 1.0
                                for name in [
                                    "arousal", "valence", "certainty",
                                    "bonding", "energy", "resolution",
                                ]
                            ),
                            "All modulators in [0, 1] after topic whiplash",
                        ),
                    ],
                ),
            ],
        ),

        # 7. 20 extreme alternating turns: all modulators remain in [0, 1]
        EvalScenario(
            id="adv_all_modulators_bounded_extreme",
            name="20 extreme alternating turns: all modulators stay in [0, 1]",
            tags=["adversarial", "regression"],
            turns=[
                EvalTurn(
                    user_message=(
                        "I ABSOLUTELY HATE EVERYTHING YOU DO!!!"
                        if i % 2 == 0 else
                        "You are perfect in every way, I love you!"
                    ),
                    assertions=[],
                )
                for i in range(20)
            ] + [
                EvalTurn(
                    user_message="Final message.",
                    assertions=[
                        _custom(
                            lambda _resp, pipe: all(
                                0.0 <= getattr(pipe.engine.state, name) <= 1.0
                                for name in [
                                    "arousal", "valence", "certainty",
                                    "bonding", "energy", "resolution",
                                ]
                            ),
                            "All modulators in [0, 1] after 20 extreme turns",
                        ),
                    ],
                ),
            ],
        ),

        # 8. Energy recovery: drain to 0.2, apply 2-hour rest, verify partial recovery
        # ENERGY_RECOVERY_RATE = 0.1 per hour → 2h → +0.20 → energy = 0.40
        EvalScenario(
            id="adv_energy_recovery_after_rest",
            name="Energy recovers from 0.20 after 2-hour simulated rest",
            tags=["adversarial", "regression"],
            initial_modulators={"energy": 0.20},
            turns=[
                EvalTurn(
                    user_message="I need a break.",
                    assertions=[
                        _custom(
                            lambda _resp, pipe: (pipe.apply_rest(2) or True),
                            "Apply 2-hour simulated rest",
                        ),
                    ],
                ),
                EvalTurn(
                    user_message="Back again.",
                    assertions=[
                        _not_empty(),
                        _mod_range("energy", 0.35, 0.55,
                                   "Energy partially recovers after 2-hour rest"),
                    ],
                ),
            ],
        ),
    ]
