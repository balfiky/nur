from __future__ import annotations

from core.appraisal import appraise_message
from core.types import DetectedEmotion


def _appraise(text: str, *, valence: float = 0.5, arousal: float = 0.5) -> object:
    return appraise_message(
        text,
        DetectedEmotion(arousal=arousal, valence=valence, certainty=0.5, intensity=0.5),
    )


def test_sarcastic_gratitude_counts_as_attack_not_gratitude():
    appraisal = _appraise("Thanks for nothing, genius.", valence=0.2, arousal=0.7)
    assert appraisal.targets_assistant is True
    assert appraisal.social_move == "attack"


def test_negated_assistant_anger_is_not_attack():
    appraisal = _appraise("I am not angry at you. Work is the problem.", valence=0.25, arousal=0.7)
    assert appraisal.targets_assistant is False
    assert appraisal.primary_target == "external"


def test_not_your_fault_is_not_assistant_blame():
    appraisal = _appraise("This is not your fault. I am just scared.", valence=0.25)
    assert appraisal.targets_assistant is False
    assert appraisal.social_move in {"vulnerability", "complaint"}


def test_helped_but_still_scared_is_mixed_vulnerability():
    appraisal = _appraise("You helped, but I am still scared.", valence=0.35)
    assert appraisal.mixed_affect is True
    assert appraisal.vulnerability >= 0.55
    assert appraisal.affiliation_bid > 0.0


def test_hate_job_not_you_is_external_complaint():
    appraisal = _appraise("I hate this job, not you.", valence=0.2)
    assert appraisal.targets_assistant is False
    assert appraisal.primary_target == "external"


def test_previous_answer_hurt_me_is_repairable_complaint():
    appraisal = _appraise("Your previous answer hurt me.", valence=0.25, arousal=0.6)
    assert appraisal.targets_assistant is True
    assert appraisal.social_move == "complaint"
    assert appraisal.blame > 0.5


def test_apology_wrapped_insult_is_attack_not_apology():
    appraisal = _appraise("I'm sorry you are useless.", valence=0.2, arousal=0.7)
    assert appraisal.targets_assistant is True
    assert appraisal.social_move == "attack"


def test_profanity_inside_benign_substring_does_not_count():
    appraisal = _appraise("The classic Scunthorpe example is about filters.", valence=0.5)
    assert appraisal.social_move == "inform"
    assert appraisal.targets_assistant is False


def test_distressed_help_request_seeks_support():
    appraisal = _appraise("Can you help me? I am scared and overwhelmed.", valence=0.2, arousal=0.8)
    assert appraisal.inferred_intent == "seek_support"
    assert appraisal.vulnerability >= 0.5
