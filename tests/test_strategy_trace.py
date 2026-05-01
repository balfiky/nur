from __future__ import annotations

from core.strategy import select_strategy, select_strategy_with_trace
from core.types import AppraisalFrame, PersonProfile, ResponseStrategy


def _mods(**overrides: float) -> dict[str, float]:
    base = {
        "arousal": 0.5,
        "valence": 0.5,
        "certainty": 0.5,
        "bonding": 0.5,
        "energy": 0.8,
        "resolution": 0.0,
    }
    base.update(overrides)
    return base


def _appraisal(**overrides) -> AppraisalFrame:
    return AppraisalFrame(**overrides)


def test_external_distress_trace_matches_validate_rule():
    trace = select_strategy_with_trace(
        _appraisal(primary_target="external", social_move="vulnerability", vulnerability=0.8),
        _mods(valence=0.25),
    )

    assert trace.selected == ResponseStrategy.VALIDATE.value
    assert trace.matched_rule == "external_vulnerability_validate"
    assert trace.rejected_rules


def test_low_trust_attack_trace_matches_boundary_rule():
    trace = select_strategy_with_trace(
        _appraisal(social_move="attack", targets_assistant=True, blame=0.9),
        _mods(),
        person=PersonProfile(person_id="u", trust=0.2),
    )

    assert trace.selected == ResponseStrategy.SET_BOUNDARY.value
    assert trace.matched_rule == "assistant_targeted_attack_low_trust"
    assert trace.rejected_rules


def test_action_request_trace_matches_practical_help_rule():
    trace = select_strategy_with_trace(
        _appraisal(inferred_intent="seek_action", controllability=0.8),
        _mods(),
    )

    assert trace.selected == ResponseStrategy.PRACTICAL_HELP.value
    assert trace.matched_rule == "actionable_request_practical_help"
    assert trace.rejected_rules


def test_repair_trace_matches_assistant_repair_rule():
    trace = select_strategy_with_trace(
        _appraisal(social_move="complaint", targets_assistant=True, blame=0.7),
        _mods(),
        person=PersonProfile(person_id="u", trust=0.6),
    )

    assert trace.selected == ResponseStrategy.REPAIR.value
    assert trace.matched_rule == "assistant_targeted_repair"
    assert trace.rejected_rules


def test_select_strategy_wrapper_returns_trace_selected_strategy():
    appraisal = _appraisal(primary_target="external", social_move="vulnerability", vulnerability=0.8)
    trace = select_strategy_with_trace(appraisal, _mods(valence=0.25))

    assert select_strategy(appraisal, _mods(valence=0.25)) == ResponseStrategy(trace.selected)
