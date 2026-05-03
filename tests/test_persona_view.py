from __future__ import annotations

import pytest

from core.life_influence import LifeInfluence
from core.types import AppraisalFrame, RelationshipContext, StrategyDecisionTrace
from pipeline import DebugState
from runtime.debug.persona_view import build_persona_view


def test_persona_view_inactive_shape():
    view = build_persona_view(session_key="telegram:alice:1")

    assert view["active"] is False
    assert view["session_key"] == "telegram:alice:1"
    assert view["emotions"]["simple_label"] == "neutral"
    assert view["perception"]["available"] is False
    assert view["relationship"]["open_loop_count"] == 0
    assert view["life"]["context_available"] is False
    assert view["skills_tools"]["enabled_skill_count"] == 0


def test_persona_view_translates_modulators_to_human_emotion():
    debug = DebugState(
        emotion_label="angry",
        modulator_snapshot={
            "arousal": 0.82,
            "valence": 0.18,
            "certainty": 0.78,
            "bonding": 0.42,
            "energy": 0.86,
            "resolution": 0.35,
        },
    )

    view = build_persona_view(debug)

    assert view["emotions"]["primary"] == "angry"
    assert view["emotions"]["simple_label"] == "angry"
    assert "high activation" in view["emotions"]["drivers"]
    assert "negative emotional tone" in view["emotions"]["drivers"]
    assert view["emotions"]["modulators"]["arousal"]["level"] == "high"
    assert view["emotions"]["mental_health"]["label"] in {"stable", "strained", "distressed", "critical"}
    assert 0 <= view["emotions"]["mental_health"]["score"] <= 100


def test_persona_view_exposes_perception_life_memory_and_skills():
    debug = DebugState(
        modulator_snapshot={
            "arousal": 0.7,
            "valence": 0.4,
            "certainty": 0.25,
            "bonding": 0.6,
            "energy": 0.9,
            "resolution": 0.2,
        },
        appraisal_frame=AppraisalFrame(
            primary_target="external",
            social_move="vulnerability",
            inferred_intent="seek_support",
            vulnerability=0.8,
        ),
        response_strategy="validate",
        strategy_trace=StrategyDecisionTrace(
            selected="validate",
            matched_rule="external_vulnerability_validate",
        ),
        relationship_context=RelationshipContext(summary="No open loops."),
        life_history_context={
            "beliefs": [{"subject": "learning"}],
            "drives": [{"name": "curiosity", "delta": 0.03}],
            "recent_evolution": [{"event": "digest"}],
        },
        life_influence=LifeInfluence(curiosity_pressure=0.03),
        life_influence_effects={"memory_salience_delta": 0.03},
        skill_context={
            "count": 1,
            "skills": [{"id": "note-helper", "name": "note-helper"}],
        },
    )

    view = build_persona_view(debug)

    assert view["perception"]["target"] == "external"
    assert view["perception"]["vulnerability"] == pytest.approx(0.8)
    assert view["relationship"]["strategy"] == "validate"
    assert view["relationship"]["strategy_reason"] == "external_vulnerability_validate"
    assert view["life"]["belief_count"] == 1
    assert view["life"]["active_pressures"]["curiosity_pressure"] == pytest.approx(0.03)
    assert view["life"]["effects"] == {"memory_salience_delta": 0.03}
    assert view["skills_tools"]["enabled_skill_count"] == 1
    assert view["skills_tools"]["enabled_skills"][0]["id"] == "note-helper"
