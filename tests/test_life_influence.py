from __future__ import annotations

from datetime import datetime

import pytest

from config.loader import SemanticMemoryConfig
from core.life_influence import (
    action_variable_deltas,
    apply_life_influence_to_action_variables,
    derive_life_influence,
)
from core.memory.semantic import derive_semantic_entries
from core.proactive import evaluate_proactive
from core.types import ActionVariables, ModulatorState, PersonProfile, UnresolvedItem


def test_life_influence_is_neutral_without_context():
    influence = derive_life_influence({})
    assert influence.is_neutral
    assert all(value == 0.0 for value in influence.to_dict().values())


def test_life_influence_is_deterministic_and_unclamped_with_domain_bounds_later():
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
    assert first.curiosity_pressure == pytest.approx(0.3)
    assert first.caution_pressure == pytest.approx(0.2)
    assert first.autonomy_pressure == pytest.approx(0.2)
    assert first.attachment_pressure == pytest.approx(0.04)


def test_action_adjustment_uses_full_pressure_and_clamps_domain():
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
    assert adjusted.risk_tolerance == pytest.approx(0.0)
    assert adjusted.persistence_drive == pytest.approx(1.0)
    assert adjusted.autonomy_bias == pytest.approx(1.0)


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


def test_repair_pressure_creates_proactive_score_delta():
    influence = derive_life_influence({"drives": [{"name": "repair", "delta": 0.2}]})
    _, trace = evaluate_proactive(
        state=ModulatorState(arousal=0.5, valence=0.5, resolution=0.0, energy=1.0),
        unresolved_items=[
            UnresolvedItem(
                id="loop-1",
                source="relationship",
                description="unresolved deadline tension",
                created_at=datetime.utcnow(),
                intensity=0.35,
                decay_rate=0.0,
            )
        ],
        active_plan=None,
        person=PersonProfile(person_id="alice", trust=0.5),
        idle_seconds=600.0,
        proactive_count=0,
        life_influence=influence,
    )

    assert trace.life_influence_score_deltas
    assert list(trace.life_influence_score_deltas.values())[0] == pytest.approx(0.08)


def test_competence_pressure_records_action_variable_delta():
    base = ActionVariables(
        risk_tolerance=0.5,
        persistence_drive=0.5,
        autonomy_bias=0.5,
    )
    influence = derive_life_influence({"drives": [{"name": "competence", "delta": 0.2}]})

    adjusted = apply_life_influence_to_action_variables(base, influence)
    deltas = action_variable_deltas(base, adjusted)

    assert deltas["persistence_delta"] == pytest.approx(0.2)
    assert deltas["risk_tolerance_delta"] == pytest.approx(0.0)
    assert deltas["autonomy_delta"] == pytest.approx(0.0)


def test_curiosity_pressure_creates_semantic_salience_delta():
    config = SemanticMemoryConfig()
    base_entries = derive_semantic_entries(
        config=config,
        user_id="alice",
        user_message="I want to learn graph databases.",
        assistant_response="We can study that.",
        event_intensity=0.0,
    )
    influenced_entries = derive_semantic_entries(
        config=config,
        user_id="alice",
        user_message="I want to learn graph databases.",
        assistant_response="We can study that.",
        event_intensity=0.0,
        life_influence=derive_life_influence({"drives": [{"name": "curiosity", "delta": 0.2}]}),
    )

    assert influenced_entries[0].salience - base_entries[0].salience == pytest.approx(0.2)


def test_neutral_context_creates_no_action_variable_delta():
    base = ActionVariables(risk_tolerance=0.5, persistence_drive=0.5, autonomy_bias=0.5)
    adjusted = apply_life_influence_to_action_variables(base, derive_life_influence({}))

    assert action_variable_deltas(base, adjusted) == {
        "risk_tolerance_delta": 0.0,
        "persistence_delta": 0.0,
        "autonomy_delta": 0.0,
    }
