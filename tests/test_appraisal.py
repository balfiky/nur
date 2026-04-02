"""Tests for the deterministic social appraisal layer."""

from core.appraisal import appraise_message
from core.types import DetectedEmotion


def _detected(
    *,
    arousal: float = 0.5,
    valence: float = 0.5,
    certainty: float = 0.5,
    intensity: float = 0.5,
) -> DetectedEmotion:
    return DetectedEmotion(
        arousal=arousal,
        valence=valence,
        certainty=certainty,
        intensity=intensity,
    )


class TestAppraisal:
    def test_detects_direct_attack_on_assistant(self):
        appraisal = appraise_message(
            "You are useless and this answer is terrible.",
            _detected(arousal=0.8, valence=0.1, intensity=0.8),
        )
        assert appraisal.targets_assistant is True
        assert appraisal.primary_target == "assistant"
        assert appraisal.social_move == "attack"
        assert appraisal.inferred_intent == "harm"

    def test_external_distress_is_not_treated_as_attack(self):
        appraisal = appraise_message(
            "I'm furious about work, not at you.",
            _detected(arousal=0.9, valence=0.2, intensity=0.8),
        )
        assert appraisal.targets_assistant is False
        assert appraisal.primary_target == "external"
        assert appraisal.social_move in {"complaint", "vulnerability"}

    def test_mixed_affect_detected(self):
        appraisal = appraise_message(
            "I'm happy it worked, but I'm scared about what comes next.",
            _detected(arousal=0.7, valence=0.45, intensity=0.7),
        )
        assert appraisal.mixed_affect is True
        assert appraisal.vulnerability >= 0.5

    def test_apology_is_affiliative_repair(self):
        appraisal = appraise_message(
            "I'm sorry, I handled that badly.",
            _detected(arousal=0.4, valence=0.35, intensity=0.4),
        )
        assert appraisal.social_move == "apology"
        assert appraisal.inferred_intent == "repair"
        assert appraisal.affiliation_bid >= 0.8
