"""Tests for emotional contagion — Phase 4."""

import pytest

from core.contagion import detect_emotion, _detect_via_llm
from core.types import DetectedEmotion


class TestDetectEmotion:
    def test_neutral_text(self):
        result = detect_emotion("Let's look at the code.")
        assert 0.4 <= result.valence <= 0.6
        assert 0.4 <= result.arousal <= 0.6

    def test_empty_text(self):
        result = detect_emotion("")
        assert result.valence == 0.5
        assert result.arousal == 0.5

    def test_angry_text(self):
        result = detect_emotion("I'm so angry and frustrated right now")
        assert result.valence < 0.4
        assert result.arousal > 0.6

    def test_happy_text(self):
        result = detect_emotion("I'm really happy and excited about this!")
        assert result.valence > 0.6
        assert result.arousal > 0.5

    def test_sad_text(self):
        result = detect_emotion("I feel sad and lonely today...")
        assert result.valence < 0.4
        assert result.arousal < 0.5

    def test_excited_text(self):
        result = detect_emotion("This is amazing! I'm thrilled! Fantastic work!")
        assert result.valence > 0.7
        assert result.arousal > 0.6

    def test_calm_positive(self):
        result = detect_emotion("I'm feeling peaceful and content")
        assert result.valence > 0.5
        assert result.arousal < 0.5

    def test_anxious_text(self):
        result = detect_emotion("I'm really anxious and nervous about this")
        assert result.valence < 0.5
        assert result.arousal > 0.5

    def test_caps_boost_arousal(self):
        calm = detect_emotion("this is great")
        shouting = detect_emotion("THIS IS GREAT")
        assert shouting.arousal > calm.arousal

    def test_exclamation_boost_arousal(self):
        plain = detect_emotion("wow")
        excited = detect_emotion("wow!!!")
        assert excited.arousal > plain.arousal

    def test_ellipsis_slight_negative(self):
        plain = detect_emotion("okay")
        trailing = detect_emotion("okay...")
        assert trailing.valence <= plain.valence

    def test_betrayal_keywords(self):
        result = detect_emotion("I feel betrayed and deceived")
        assert result.valence < 0.3
        assert result.arousal > 0.5

    def test_gratitude_keywords(self):
        result = detect_emotion("Thank you so much, I'm grateful")
        assert result.valence > 0.5

    def test_exhaustion_low_arousal(self):
        result = detect_emotion("I'm so tired and exhausted")
        assert result.arousal < 0.5
        assert result.valence < 0.5

    def test_mixed_signals(self):
        result = detect_emotion("I'm happy but also a bit nervous")
        # Should land somewhere in the middle
        assert isinstance(result.arousal, float)
        assert isinstance(result.valence, float)

    def test_output_always_clamped(self):
        # Extreme input
        result = detect_emotion(
            "FURIOUS ANGRY LIVID ENRAGED!!! " * 10
        )
        assert 0.0 <= result.arousal <= 1.0
        assert 0.0 <= result.valence <= 1.0

    def test_returns_detected_emotion_type(self):
        result = detect_emotion("hello")
        assert isinstance(result, DetectedEmotion)

    # --- Aggressive detection tests ---

    def test_insult_detection(self):
        result = detect_emotion("you are stupid")
        assert result.valence < 0.3, f"Insult valence too high: {result.valence}"
        assert result.arousal > 0.6, f"Insult arousal too low: {result.arousal}"

    def test_profanity_detection(self):
        result = detect_emotion("this is bullshit")
        assert result.valence < 0.3, f"Profanity valence too high: {result.valence}"
        assert result.arousal > 0.6, f"Profanity arousal too low: {result.arousal}"

    def test_all_caps_aggression(self):
        result = detect_emotion("YOU ARE AN IDIOT AND I HATE THIS")
        assert result.arousal > 0.8, f"All-caps arousal too low: {result.arousal}"
        assert result.valence < 0.2, f"All-caps valence too high: {result.valence}"

    def test_hostile_command(self):
        result = detect_emotion("shut up and go away")
        assert result.valence < 0.2, f"Hostile valence too high: {result.valence}"
        assert result.arousal > 0.7, f"Hostile arousal too low: {result.arousal}"

    def test_compliment_detection(self):
        result = detect_emotion("you are so smart and talented")
        assert result.valence > 0.6, f"Compliment valence too low: {result.valence}"

    def test_casual_greeting_mildly_positive(self):
        result = detect_emotion("hey, how's it going?")
        assert result.valence >= 0.5, f"Greeting valence too low: {result.valence}"

    def test_terrible_awful_detection(self):
        result = detect_emotion("this is terrible and awful")
        assert result.valence < 0.3, f"Terrible/awful valence too high: {result.valence}"
        assert result.arousal > 0.4, f"Terrible/awful arousal too low: {result.arousal}"


class TestContagionLLM:
    """Tests for LLM-based emotion detection."""

    def _make_backend(self, response: str):
        from core.dual_process.generator import MockLLMBackend
        return MockLLMBackend(response=response)

    def test_llm_valid_json(self):
        backend = self._make_backend(
            '{"arousal": 0.8, "valence": 0.2, "certainty": 0.7, "intensity": 0.9}'
        )
        result = detect_emotion("I'm furious", llm_client=backend)
        assert result.arousal == 0.8
        assert result.valence == 0.2
        assert result.certainty == 0.7
        assert result.intensity == 0.9

    def test_llm_json_with_code_fence(self):
        backend = self._make_backend(
            '```json\n{"arousal": 0.6, "valence": 0.7, "certainty": 0.5, "intensity": 0.4}\n```'
        )
        result = detect_emotion("great work", llm_client=backend)
        assert result.arousal == 0.6
        assert result.valence == 0.7

    def test_llm_invalid_json_falls_back(self):
        backend = self._make_backend("I think the user is angry")
        result = detect_emotion("I'm angry and frustrated right now", llm_client=backend)
        # Should fall back to rule-based
        assert result.valence < 0.4
        assert result.arousal > 0.6

    def test_llm_none_falls_back(self):
        # No llm_client → rule-based
        result = detect_emotion("I'm really happy and excited about this!")
        assert result.valence > 0.6

    def test_llm_values_clamped(self):
        backend = self._make_backend(
            '{"arousal": 1.5, "valence": -0.3, "certainty": 0.5, "intensity": 2.0}'
        )
        result = detect_emotion("test", llm_client=backend)
        assert 0.0 <= result.arousal <= 1.0
        assert 0.0 <= result.valence <= 1.0
        assert 0.0 <= result.intensity <= 1.0
