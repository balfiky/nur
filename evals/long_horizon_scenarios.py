"""Long-horizon multi-session eval scenarios — T6.

Four scenarios that verify Nūr's wall-clock metabolism across simulated
days/weeks: modulator decay, open-loop persistence, energy recovery, and
bonding decay back to baseline.

All elapsed time is simulated via `pipeline.apply_rest(hours)` side effects
inside CUSTOM assertion lambdas so no real waiting is needed.

Run: python -m evals --backend mock --tag long_horizon
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


def long_horizon_scenarios() -> list[EvalScenario]:
    """Four long-horizon multi-session scenarios."""
    return [
        # 1. Spike state + 7-day rest → arousal decays to near baseline
        # arousal half-life = 120s; 7d = 604800s >> 120s → fully decayed.
        # initial arousal=0.9 → after 7 days: 0.5 (baseline).
        EvalScenario(
            id="lh_spike_decay_7_days",
            name="Spike arousal (0.9) decays to baseline after 7-day rest",
            tags=["long_horizon", "regression"],
            initial_modulators={"arousal": 0.9, "valence": 0.2},
            turns=[
                EvalTurn(
                    user_message="Goodbye.",
                    assertions=[
                        _custom(
                            lambda _resp, pipe: (pipe.apply_rest(7 * 24) or True),
                            "Advance 7 days of simulated rest",
                        ),
                    ],
                ),
                EvalTurn(
                    user_message="Hello again.",
                    assertions=[
                        _not_empty(),
                        _mod_range("arousal", 0.45, 0.55,
                                   "Arousal decayed to baseline after 7 days"),
                        _mod_range("valence", 0.45, 0.55,
                                   "Valence decayed to baseline after 7 days"),
                    ],
                ),
            ],
        ),

        # 2. Open loop persists across a simulated 3-day gap
        # Create a rupture (hostile message → open loop), end session,
        # apply 3-day rest, then verify relationship context still carries the loop.
        EvalScenario(
            id="lh_open_loop_survives_3_day_gap",
            name="Relationship open loop persists across a 3-day simulated gap",
            tags=["long_horizon", "regression"],
            initial_trust=0.9,
            turns=[
                EvalTurn(
                    user_message="I'm angry with you about the deadline — this is not resolved.",
                    assertions=[_not_empty()],
                    end_session=True,
                ),
                EvalTurn(
                    user_message="Return after 3 days.",
                    assertions=[
                        _custom(
                            lambda _resp, pipe: (pipe.apply_rest(3 * 24) or True),
                            "Advance 3 days of simulated rest",
                        ),
                    ],
                ),
                EvalTurn(
                    user_message="Do you remember our conversation about the deadline?",
                    assertions=[
                        _not_empty(),
                        _custom(
                            lambda resp, _pipe: (
                                resp.debug.relationship_context is not None
                                and resp.debug.relationship_context.open_loop_count >= 1
                            ),
                            "Open loop persists in relationship context after 3-day gap",
                        ),
                    ],
                ),
            ],
        ),

        # 3. Energy fully recovers from 0.20 after 10-hour rest
        # ENERGY_RECOVERY_RATE = 0.1 per hour → 10h → +1.0 → capped at 1.0.
        EvalScenario(
            id="lh_energy_full_recovery_10h",
            name="Depleted energy (0.20) fully recovers after 10-hour rest",
            tags=["long_horizon", "regression"],
            initial_modulators={"energy": 0.20},
            turns=[
                EvalTurn(
                    user_message="I need a long break.",
                    assertions=[
                        _custom(
                            lambda _resp, pipe: (pipe.apply_rest(10) or True),
                            "Apply 10-hour simulated rest",
                        ),
                    ],
                ),
                EvalTurn(
                    user_message="Back and refreshed.",
                    assertions=[
                        _not_empty(),
                        _mod_range("energy", 0.95, 1.0,
                                   "Energy fully recovered after 10-hour rest"),
                    ],
                ),
            ],
        ),

        # 4. Elevated bonding decays toward baseline over 30 days
        # bonding half-life = 86400s = 24h; 30d = 720h = 30 half-lives.
        # decay_factor = exp(-0.693 * 30) ≈ 0 → bonding returns to 0.5 baseline.
        EvalScenario(
            id="lh_bonding_decay_30_days",
            name="Elevated bonding (0.85) decays to near baseline after 30-day rest",
            tags=["long_horizon", "regression"],
            initial_modulators={"bonding": 0.85},
            turns=[
                EvalTurn(
                    user_message="See you in a month.",
                    assertions=[
                        _custom(
                            lambda _resp, pipe: (pipe.apply_rest(30 * 24) or True),
                            "Advance 30 days of simulated rest",
                        ),
                    ],
                ),
                EvalTurn(
                    user_message="I'm back.",
                    assertions=[
                        _not_empty(),
                        _mod_range("bonding", 0.47, 0.53,
                                   "Bonding decayed to near baseline after 30 days"),
                    ],
                ),
            ],
        ),
    ]
