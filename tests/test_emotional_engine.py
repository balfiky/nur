"""Tests for the emotional engine — Phase 1."""

import math
import pytest

from core.types import (
    AttachmentStyle,
    BaselineShift,
    EmotionalEvent,
    EventType,
    ModulatorState,
)
from core.emotional_engine import (
    DEFAULT_HALF_LIVES,
    SPIKE_INTENSITY_THRESHOLD,
    EmotionalEngine,
)


class TestModulatorState:
    def test_defaults(self):
        s = ModulatorState()
        assert s.arousal == 0.5
        assert s.energy == 1.0

    def test_clamping(self):
        s = ModulatorState(arousal=1.5, valence=-0.3)
        assert s.arousal == 1.0
        assert s.valence == 0.0

    def test_to_dict(self):
        s = ModulatorState()
        d = s.to_dict()
        assert set(d.keys()) == {"arousal", "valence", "certainty", "bonding", "energy", "resolution"}

    def test_copy_is_independent(self):
        s = ModulatorState(arousal=0.8)
        c = s.copy()
        c.arousal = 0.2
        assert s.arousal == 0.8


class TestEmotionalEngine:
    def test_initial_state_matches_baseline(self):
        engine = EmotionalEngine()
        assert engine.state.arousal == engine.baseline.arousal

    def test_update_positive_feedback(self):
        engine = EmotionalEngine()
        event = EmotionalEvent(event_type=EventType.POSITIVE_FEEDBACK, intensity=0.8)
        engine.update(event)
        assert engine.state.valence > 0.5
        assert engine.state.bonding > 0.5

    def test_update_conflict(self):
        engine = EmotionalEngine()
        event = EmotionalEvent(event_type=EventType.CONFLICT, intensity=0.9)
        engine.update(event)
        assert engine.state.valence < 0.5
        assert engine.state.arousal > 0.5
        assert engine.state.certainty < 0.5

    def test_spike_detection(self):
        engine = EmotionalEngine()
        mild = EmotionalEvent(event_type=EventType.SURPRISE, intensity=0.5)
        assert engine.update(mild) is False
        intense = EmotionalEvent(event_type=EventType.BETRAYAL, intensity=0.9)
        assert engine.update(intense) is True

    def test_state_stays_clamped(self):
        engine = EmotionalEngine()
        # Push arousal way up with repeated surprises
        for _ in range(20):
            engine.update(EmotionalEvent(event_type=EventType.SURPRISE, intensity=1.0))
        assert engine.state.arousal <= 1.0
        assert engine.state.certainty >= 0.0

    def test_decay_toward_baseline(self):
        engine = EmotionalEngine()
        # Spike arousal
        engine.update(EmotionalEvent(event_type=EventType.SURPRISE, intensity=1.0))
        high_arousal = engine.state.arousal
        assert high_arousal > 0.5
        # Decay for 10 minutes
        engine.decay(600.0)
        assert engine.state.arousal < high_arousal
        # Arousal should be much closer to baseline after several half-lives
        assert engine.state.arousal < 0.6

    def test_decay_does_not_overshoot_baseline(self):
        engine = EmotionalEngine(baseline=ModulatorState(valence=0.5))
        engine.state.valence = 0.8
        engine.decay(100000.0)  # very long time
        # Should approach but not drop below baseline
        assert abs(engine.state.valence - 0.5) < 0.01

    def test_energy_drains(self):
        engine = EmotionalEngine()
        initial_energy = engine.state.energy
        engine.drain_energy(intensity=0.5)
        assert engine.state.energy < initial_energy

    def test_energy_recovers(self):
        engine = EmotionalEngine()
        engine.state.energy = 0.3
        engine.decay(3600.0)  # 1 hour
        assert engine.state.energy > 0.3

    def test_energy_does_not_exceed_one(self):
        engine = EmotionalEngine()
        engine.state.energy = 0.95
        engine.decay(36000.0)  # 10 hours
        assert engine.state.energy <= 1.0

    def test_context_shift(self):
        """Context shift sets a resting target offset, not an immediate additive."""
        engine = EmotionalEngine()
        shift = BaselineShift(arousal=0.1, bonding=0.2)
        engine.set_context_shift(shift)
        # Shift affects decay target, not immediate state
        assert engine.effective_baseline("arousal") == pytest.approx(0.6, abs=0.01)
        assert engine.effective_baseline("bonding") == pytest.approx(0.7, abs=0.01)
        # After decay, state moves toward the effective baseline
        engine.state.arousal = 0.3
        engine.decay(1000)
        assert engine.state.arousal > 0.3

    def test_contagion_bounded(self):
        engine = EmotionalEngine()
        # User is very excited, high bonding
        engine.apply_contagion(user_arousal=1.0, user_valence=1.0, bonding_score=1.0)
        # Change should be capped at ±0.15
        assert engine.state.arousal <= 0.65
        assert engine.state.valence <= 0.65

    def test_contagion_scales_with_bonding(self):
        engine_high = EmotionalEngine()
        engine_low = EmotionalEngine()
        engine_high.apply_contagion(user_arousal=1.0, user_valence=1.0, bonding_score=0.9)
        engine_low.apply_contagion(user_arousal=1.0, user_valence=1.0, bonding_score=0.1)
        # Higher bonding = more mirroring
        assert engine_high.state.arousal > engine_low.state.arousal

    def test_betrayal_is_devastating(self):
        engine = EmotionalEngine()
        # Build up some trust
        for _ in range(10):
            engine.update(EmotionalEvent(event_type=EventType.WARMTH, intensity=0.7))
        bonding_before = engine.state.bonding
        # Betrayal
        engine.update(EmotionalEvent(event_type=EventType.BETRAYAL, intensity=0.95))
        assert engine.state.bonding < bonding_before
        # Valence should drop significantly from the betrayal
        assert engine.state.valence < 0.6
        assert engine.state.arousal > 0.5

    def test_emotion_label_exhausted(self):
        engine = EmotionalEngine()
        engine.state.energy = 0.1
        engine.state.arousal = 0.3
        assert engine.to_emotion_label() == "exhausted"

    def test_emotion_label_angry(self):
        engine = EmotionalEngine()
        engine.state.arousal = 0.9
        engine.state.valence = 0.1
        engine.state.certainty = 0.9
        assert engine.to_emotion_label() == "angry"

    def test_snapshot_returns_dict(self):
        engine = EmotionalEngine()
        snap = engine.snapshot()
        assert isinstance(snap, dict)
        assert "arousal" in snap
        assert all(0.0 <= v <= 1.0 for v in snap.values())

    def test_full_emotional_arc(self):
        """Simulate: calm → surprise → conflict → resolution → recovery."""
        engine = EmotionalEngine()

        # Start calm
        assert engine.to_emotion_label() == "neutral"

        # Surprise
        engine.update(EmotionalEvent(event_type=EventType.SURPRISE, intensity=0.8))
        assert engine.state.arousal > 0.7
        assert engine.state.certainty < 0.4

        # Conflict
        engine.update(EmotionalEvent(event_type=EventType.CONFLICT, intensity=0.7))
        assert engine.state.valence < 0.4

        # Some time passes
        engine.decay(300.0)  # 5 minutes

        # Resolution
        engine.update(EmotionalEvent(event_type=EventType.RESOLUTION, intensity=0.6))
        assert engine.state.valence > engine.baseline.valence - 0.1

        # Long rest
        engine.decay(7200.0)  # 2 hours
        # Should be close to baseline
        for mod in ["arousal", "valence", "certainty"]:
            current = getattr(engine.state, mod)
            base = getattr(engine.baseline, mod)
            assert abs(current - base) < 0.1, f"{mod} didn't recover: {current} vs {base}"
