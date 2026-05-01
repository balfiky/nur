from __future__ import annotations

import pytest

from core.life_influence import (
    apply_life_influence_to_action_variables,
    derive_life_influence,
)
from core.types import ActionVariables


def test_life_influence_is_neutral_without_context():
    influence = derive_life_influence({})
    assert influence.is_neutral
    assert all(value == 0.0 for value in influence.to_dict().values())


def test_life_influence_is_deterministic_and_bounded():
    context = {
        "drives": [
            {"name": "curiosity", "delta": 0.3},
            {"name": "caution", "delta": 0.2},
            {"name": "autonomy", "value": 0.7, "baseline": 0.5},
            {"name": "attachment", "delta": 0.04},
        ]
    }
    first = derive_life_influence(context)
    second = derive_life_influence(context)
    assert first == second
    assert first.curiosity_pressure == pytest.approx(0.05)
    assert first.caution_pressure == pytest.approx(0.05)
    assert first.autonomy_pressure == pytest.approx(0.05)
    assert first.attachment_pressure == pytest.approx(0.04)


def test_action_adjustment_limits_each_change_to_point_zero_five():
    base = ActionVariables(
        risk_tolerance=0.5,
        action_urgency=0.3,
        clarification_threshold=0.5,
        persistence_drive=0.5,
        autonomy_bias=0.5,
    )
    influence = derive_life_influence({
        "drives": [
            {"name": "caution", "delta": 1.0},
            {"name": "competence", "delta": 1.0},
            {"name": "autonomy", "delta": 1.0},
        ]
    })
    adjusted = apply_life_influence_to_action_variables(
        base,
        influence,
        read_only_action=True,
    )
    assert adjusted.risk_tolerance == pytest.approx(0.45)
    assert adjusted.persistence_drive == pytest.approx(0.55)
    assert adjusted.autonomy_bias == pytest.approx(0.55)


def test_autonomy_does_not_raise_non_read_only_action_bias():
    base = ActionVariables(risk_tolerance=0.5, autonomy_bias=0.5)
    influence = derive_life_influence({
        "drives": [
            {"name": "autonomy", "delta": 0.05},
            {"name": "caution", "delta": 0.05},
        ]
    })
    adjusted = apply_life_influence_to_action_variables(
        base,
        influence,
        read_only_action=False,
    )
    assert adjusted.autonomy_bias == pytest.approx(0.5)
    assert adjusted.risk_tolerance == pytest.approx(0.45)
