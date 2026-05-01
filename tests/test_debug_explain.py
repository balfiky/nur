from __future__ import annotations

from types import SimpleNamespace

from core.life_influence import LifeInfluence
from core.types import AppraisalFrame, StrategyDecisionTrace
from pipeline import DebugState
from runtime.debug.explain import explain_turn


def test_external_distress_explanation():
    debug = DebugState(
        appraisal_frame=AppraisalFrame(
            primary_target="external",
            social_move="vulnerability",
            vulnerability=0.8,
        ),
        response_strategy="validate",
        strategy_trace=StrategyDecisionTrace(
            selected="validate",
            matched_rule="external_vulnerability_validate",
        ),
        modulator_snapshot={"arousal": 0.6, "valence": 0.3, "energy": 0.9},
    )

    explanation = explain_turn(debug)

    assert "outside the relationship" in explanation["interpretation"]
    assert "external_vulnerability_validate" in explanation["strategy"]
    assert "Arousal" in explanation["state"]


def test_low_trust_boundary_explanation():
    debug = DebugState(
        appraisal_frame=AppraisalFrame(
            primary_target="assistant",
            social_move="attack",
            targets_assistant=True,
        ),
        response_strategy="set_boundary",
        strategy_trace=StrategyDecisionTrace(
            selected="set_boundary",
            matched_rule="assistant_targeted_attack_low_trust",
        ),
        modulator_snapshot={"arousal": 0.8, "valence": 0.2, "energy": 0.7},
    )

    explanation = explain_turn(debug)

    assert "directed at me" in explanation["interpretation"]
    assert "assistant_targeted_attack_low_trust" in explanation["strategy"]


def test_life_history_effect_explanation():
    debug = DebugState(
        life_history_context={
            "beliefs": [{"key": "repair"}],
            "drives": [{"name": "repair", "delta": 0.03}],
            "recent_evolution": [],
        },
        life_influence=LifeInfluence(repair_pressure=0.03),
        life_influence_effects={"strategy_tiebreak_used": True},
        modulator_snapshot={"arousal": 0.5, "valence": 0.5, "energy": 1.0},
    )

    explanation = explain_turn(debug)

    assert "Life History contributed" in explanation["life_history"]
    assert "bounded deterministic adjustment" in explanation["life_history"]


def test_tool_and_no_tool_explanations():
    no_tool = DebugState(modulator_snapshot={"arousal": 0.5, "valence": 0.5, "energy": 1.0})
    assert explain_turn(no_tool)["tools"] == "No tools were considered."

    used_tool = DebugState(
        tool_trace=SimpleNamespace(
            executed_results=[SimpleNamespace(tool_name="fs.read_file")],
            proposed_intents=[],
        ),
        modulator_snapshot={"arousal": 0.5, "valence": 0.5, "energy": 1.0},
    )
    assert "fs.read_file" in explain_turn(used_tool)["tools"]
