from __future__ import annotations

from core.character_vector import assemble_character_vector
from core.dual_process.self_check import coherence_check
from core.types import PipelineContext


def test_character_vector_is_deterministic_for_fixed_context():
    ctx = PipelineContext(
        modulator_snapshot={"energy": 0.8, "valence": 0.55},
        life_history_context={
            "beliefs": [
                {
                    "key": "autonomy",
                    "statement": "Autonomy grows through retained experience.",
                    "confidence": 0.72,
                }
            ],
            "drives": [
                {"name": "curiosity", "value": 0.62, "baseline": 0.5, "delta": 0.12},
                {"name": "caution", "value": 0.51, "baseline": 0.5, "delta": 0.01},
            ],
            "recent_evolution": [
                {
                    "domain": "belief",
                    "subject": "autonomy",
                    "after_state": "Autonomy grows through retained experience.",
                }
            ],
        },
    )

    first = assemble_character_vector(ctx)
    second = assemble_character_vector(ctx)

    assert first == second
    assert first.trace_id == second.trace_id
    assert first.conflict_resolutions[0].winner == "curiosity"


def test_character_vector_marks_drive_ambivalence():
    vector = assemble_character_vector(PipelineContext(
        life_history_context={
            "drives": [
                {"name": "curiosity", "value": 0.55, "baseline": 0.5, "delta": 0.05},
                {"name": "caution", "value": 0.50, "baseline": 0.5, "delta": 0.0},
            ],
        },
    ))

    assert vector.conflict_resolutions[0].state == "ambivalent"


def test_coherence_check_is_deterministic_and_flags_ungrounded_change():
    vector = assemble_character_vector(PipelineContext(
        life_history_context={
            "drives": [
                {"name": "curiosity", "value": 0.2, "baseline": 0.5, "delta": -0.3},
            ],
            "recent_evolution": [],
        },
    ))

    first = coherence_check("I learned this. I am very curious.", vector)
    second = coherence_check("I learned this. I am very curious.", vector)

    assert first == second
    assert "claims_durable_change_without_ledger_evidence" in first.misalignments
    assert "drive_state_claim_contradicts_value:curiosity" in first.misalignments


def test_coherence_check_requires_recent_evidence_when_turn_boundary_is_provided():
    vector = assemble_character_vector(PipelineContext(
        life_history_context={
            "recent_evolution": [
                {
                    "timestamp": 100.0,
                    "domain": "belief",
                    "subject": "autonomy",
                    "after_state": "Autonomy grows through retained experience.",
                }
            ],
        },
    ))

    old = coherence_check(
        "I learned this.",
        vector,
        durable_evidence_after=200.0,
    )
    recent = coherence_check(
        "I learned this.",
        vector,
        durable_evidence_after=50.0,
    )

    assert "claims_durable_change_without_ledger_evidence" in old.misalignments
    assert "claims_durable_change_without_ledger_evidence" not in recent.misalignments
