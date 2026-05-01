from __future__ import annotations

import pytest

from core.life_influence import LifeInfluence
from core.types import OpenLoop, PersonProfile, RelationshipContext, RelationshipEvent, StrategyDecisionTrace
from pipeline import DebugState
from runtime.debug.relationship_view import build_relationship_view


def test_relationship_view_stable_shape_without_previous_debug():
    debug = DebugState(
        emotion_label="calm",
        response_strategy="validate",
        modulator_snapshot={
            "arousal": 0.4,
            "valence": 0.6,
            "certainty": 0.7,
            "bonding": 0.55,
            "energy": 0.9,
            "resolution": 0.2,
        },
        person_profile=PersonProfile(person_id="alice", trust=0.62),
    )

    view = build_relationship_view(debug)

    assert view["emotion_label"] == "calm"
    assert view["strategy"] == "validate"
    assert set(view["modulators"]) == {
        "arousal",
        "valence",
        "certainty",
        "bonding",
        "energy",
        "resolution",
    }
    assert view["modulators"]["arousal"] == {"value": 0.4, "delta": None}
    assert view["trust"] == {"value": 0.62, "delta": None}
    assert view["open_loops"] == []
    assert view["recent_relationship_events"] == []
    assert view["memory_used"] == {
        "long_term_count": 0,
        "relationship_context_used": False,
        "semantic_count": 0,
    }


def test_relationship_view_computes_deltas_with_previous_debug():
    previous = DebugState(
        modulator_snapshot={"arousal": 0.4, "valence": 0.7},
        person_profile=PersonProfile(person_id="alice", trust=0.6),
    )
    current = DebugState(
        modulator_snapshot={"arousal": 0.45, "valence": 0.62},
        person_profile=PersonProfile(person_id="alice", trust=0.58),
    )

    view = build_relationship_view(current, previous_debug=previous)

    assert view["modulators"]["arousal"]["delta"] == pytest.approx(0.05)
    assert view["modulators"]["valence"]["delta"] == pytest.approx(-0.08)
    assert view["modulators"]["certainty"]["delta"] is None
    assert view["trust"]["delta"] == pytest.approx(-0.02)


def test_relationship_view_populates_relationship_context_and_strategy_reason():
    debug = DebugState(
        response_strategy="repair",
        strategy_trace=StrategyDecisionTrace(
            selected="repair",
            matched_rule="assistant_targeted_repair",
            evidence={"target": "assistant"},
        ),
        relationship_context=RelationshipContext(
            summary="Open loops: deadline.",
            active_loops=[
                OpenLoop(
                    loop_kind="tension",
                    source_person="alice",
                    topic="deadline",
                    description="unresolved tension about deadline",
                    intensity=0.7,
                    related_key="deadline",
                )
            ],
            recent_events=[
                RelationshipEvent(
                    event_kind="rupture",
                    source_person="alice",
                    topic="deadline",
                    summary="Rupture around deadline",
                    valence=-0.7,
                    intensity=0.7,
                    confidence=0.8,
                    related_key="deadline",
                )
            ],
            open_loop_count=1,
        ),
    )

    view = build_relationship_view(debug)

    assert view["strategy_reason"] == "assistant_targeted_repair"
    assert view["memory_used"]["relationship_context_used"] is True
    assert view["open_loops"][0]["topic"] == "deadline"
    assert view["recent_relationship_events"][0]["event_kind"] == "rupture"


def test_relationship_view_serializes_life_influence_and_effects():
    debug = DebugState(
        life_influence=LifeInfluence(repair_pressure=0.03, curiosity_pressure=0.02),
        life_influence_effects={"proactive_score_delta": 0.03},
    )

    view = build_relationship_view(debug)

    assert view["life_influence"]["repair_pressure"] == pytest.approx(0.03)
    assert view["life_influence"]["curiosity_pressure"] == pytest.approx(0.02)
    assert view["life_influence_effects"] == {"proactive_score_delta": 0.03}
