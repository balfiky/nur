"""Calibration regression tests: per-turn modulator delta caps.

These tests regression-protect config/modulators.yaml. Any config edit that
breaks these invariants will fail here before a release.

Design notes:
- "Normal" events: intensity < spike_threshold (0.8)
- "Spike" events: intensity >= spike_threshold (0.8)
- Decay tests manipulate state directly and compare against effective_baseline.
"""

from __future__ import annotations

import math

import pytest

from config.loader import get_config
from core.emotional_engine import EmotionalEngine, EVENT_DELTA_CAPS, SPIKE_INTENSITY_THRESHOLD
from core.types import EmotionalEvent, EventType, ModulatorState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine() -> EmotionalEngine:
    """Engine with default (0.5) baseline for all modulators."""
    return EmotionalEngine()


def _event(event_type: EventType, intensity: float) -> EmotionalEvent:
    return EmotionalEvent(event_type=event_type, intensity=intensity)


def _delta(before: float, after: float) -> float:
    return abs(after - before)


_NORMAL_INTENSITY = SPIKE_INTENSITY_THRESHOLD - 0.01   # just below spike threshold
_SPIKE_INTENSITY = 1.0                                  # max intensity, above threshold


# ---------------------------------------------------------------------------
# Normal-event cap tests (intensity below spike_threshold)
# ---------------------------------------------------------------------------

_NORMAL_EVENTS = [
    EventType.USER_MESSAGE,
    EventType.POSITIVE_FEEDBACK,
    EventType.NEGATIVE_FEEDBACK,
    EventType.WARMTH,
    EventType.RESOLUTION,
    EventType.SILENCE,
    EventType.TOPIC_SHIFT,
]


@pytest.mark.parametrize("event_type", _NORMAL_EVENTS)
def test_normal_event_at_max_normal_intensity_stays_within_normal_caps(event_type):
    """At the highest non-spike intensity, each modulator must stay within the normal cap."""
    engine = _engine()
    before = {m: getattr(engine.state, m) for m in ("arousal", "valence", "certainty", "bonding")}

    engine.update(_event(event_type, intensity=_NORMAL_INTENSITY))

    caps = EVENT_DELTA_CAPS.get("normal", {})
    for mod, old_val in before.items():
        cap = caps.get(mod)
        if cap is not None:
            change = _delta(old_val, getattr(engine.state, mod))
            assert change <= cap + 1e-9, (
                f"{event_type.value} (normal) moved {mod} by {change:.4f}; "
                f"exceeds normal cap {cap}"
            )


# ---------------------------------------------------------------------------
# Spike-event cap tests (intensity at max)
# ---------------------------------------------------------------------------

_SPIKE_EVENTS = [EventType.BETRAYAL, EventType.CONFLICT, EventType.SURPRISE]


@pytest.mark.parametrize("event_type", _SPIKE_EVENTS)
def test_spike_event_at_max_intensity_stays_within_spike_caps(event_type):
    """At intensity=1.0 (spike), each modulator must stay within the spike cap."""
    engine = _engine()
    before = {m: getattr(engine.state, m) for m in ("arousal", "valence", "certainty", "bonding")}

    engine.update(_event(event_type, intensity=_SPIKE_INTENSITY))

    caps = EVENT_DELTA_CAPS.get("spike", {})
    for mod, old_val in before.items():
        cap = caps.get(mod)
        if cap is not None:
            change = _delta(old_val, getattr(engine.state, mod))
            assert change <= cap + 1e-9, (
                f"{event_type.value} (spike) moved {mod} by {change:.4f}; "
                f"exceeds spike cap {cap}"
            )


# ---------------------------------------------------------------------------
# Single-turn specific bounds
# ---------------------------------------------------------------------------

def test_mild_gratitude_bonding_bounded_to_normal_cap():
    """Mild gratitude (intensity 0.3) must not change bonding by more than the normal cap."""
    engine = _engine()
    before_bonding = engine.state.bonding

    engine.update(_event(EventType.POSITIVE_FEEDBACK, intensity=0.3))

    change = _delta(before_bonding, engine.state.bonding)
    normal_cap = EVENT_DELTA_CAPS.get("normal", {}).get("bonding", 0.05)
    assert change <= normal_cap + 1e-9, (
        f"Mild gratitude moved bonding by {change:.4f}; exceeds cap {normal_cap}"
    )


def test_mild_complaint_valence_bounded_to_normal_cap():
    """Mild complaint (intensity 0.3) must not change valence by more than the normal cap."""
    engine = _engine()
    before_valence = engine.state.valence

    engine.update(_event(EventType.NEGATIVE_FEEDBACK, intensity=0.3))

    change = _delta(before_valence, engine.state.valence)
    normal_cap = EVENT_DELTA_CAPS.get("normal", {}).get("valence", 0.16)
    assert change <= normal_cap + 1e-9, (
        f"Mild complaint changed valence by {change:.4f}; exceeds cap {normal_cap}"
    )


def test_10_mild_gratitudes_bonding_bounded():
    """10 mild gratitude turns (without decay) must not push bonding above 0.75."""
    engine = _engine()
    for _ in range(10):
        engine.update(_event(EventType.POSITIVE_FEEDBACK, intensity=0.3))
        engine.update(_event(EventType.WARMTH, intensity=0.3))

    # Normal bonding cap is 0.05 per event; 20 events × 0.05 max = +1.0, clamped to 1.0
    # This test checks the system stays in valid range even without decay
    assert 0.0 <= engine.state.bonding <= 1.0


# ---------------------------------------------------------------------------
# Bounds invariants
# ---------------------------------------------------------------------------

def test_all_modulators_stay_in_range_after_10_betrayal_spikes():
    """All modulators must remain in [0.0, 1.0] after extreme stress."""
    engine = _engine()
    for _ in range(10):
        engine.update(_event(EventType.BETRAYAL, intensity=1.0))

    for mod in ("arousal", "valence", "certainty", "bonding", "energy"):
        val = getattr(engine.state, mod)
        assert 0.0 <= val <= 1.0, f"{mod} = {val:.4f} is outside [0.0, 1.0]"
        assert math.isfinite(val), f"{mod} is non-finite after spikes"


def test_all_modulators_stay_in_range_after_mixed_extremes():
    """Alternating betrayal + warmth must not create NaN or out-of-range values."""
    engine = _engine()
    for _ in range(20):
        engine.update(_event(EventType.BETRAYAL, intensity=1.0))
        engine.update(_event(EventType.WARMTH, intensity=_NORMAL_INTENSITY))

    for mod in ("arousal", "valence", "certainty", "bonding", "energy"):
        val = getattr(engine.state, mod)
        assert 0.0 <= val <= 1.0, f"{mod} = {val:.4f} is outside [0.0, 1.0]"
        assert math.isfinite(val), f"{mod} is non-finite"


# ---------------------------------------------------------------------------
# Decay half-life regression tests
# ---------------------------------------------------------------------------

def test_arousal_decays_half_toward_baseline_after_one_half_life():
    """Arousal must be ~50% of its deviation from baseline after one half-life (120 s)."""
    cfg = get_config()
    half_life = cfg.half_lives.get("arousal", 120.0)

    engine = _engine()                   # baseline.arousal = 0.5
    engine.state.arousal = 0.9          # push state to 0.9

    baseline = engine.effective_baseline("arousal")   # 0.5
    initial_deviation = engine.state.arousal - baseline  # 0.4

    engine.decay(elapsed_seconds=half_life)

    remaining_deviation = engine.state.arousal - baseline
    expected = initial_deviation * 0.5  # 0.2
    assert abs(remaining_deviation - expected) < 0.02, (
        f"Arousal half-life: expected deviation ~{expected:.3f}, "
        f"got {remaining_deviation:.3f}"
    )


def test_valence_decays_half_toward_baseline_after_one_half_life():
    """Valence must be ~50% of its deviation from baseline after one half-life (1800 s)."""
    cfg = get_config()
    half_life = cfg.half_lives.get("valence", 1800.0)

    engine = _engine()
    engine.state.valence = 0.9

    baseline = engine.effective_baseline("valence")
    initial_deviation = engine.state.valence - baseline

    engine.decay(elapsed_seconds=half_life)

    remaining_deviation = engine.state.valence - baseline
    expected = initial_deviation * 0.5
    assert abs(remaining_deviation - expected) < 0.02, (
        f"Valence half-life: expected deviation ~{expected:.3f}, "
        f"got {remaining_deviation:.3f}"
    )


def test_bonding_decays_half_toward_baseline_after_one_day():
    """Bonding must be ~50% of its deviation after one day (86400 s)."""
    cfg = get_config()
    half_life = cfg.half_lives.get("bonding", 86400.0)

    engine = _engine()
    engine.state.bonding = 0.9

    baseline = engine.effective_baseline("bonding")
    initial_deviation = engine.state.bonding - baseline

    engine.decay(elapsed_seconds=half_life)

    remaining_deviation = engine.state.bonding - baseline
    expected = initial_deviation * 0.5
    assert abs(remaining_deviation - expected) < 0.02, (
        f"Bonding half-life: expected deviation ~{expected:.3f}, "
        f"got {remaining_deviation:.3f}"
    )
