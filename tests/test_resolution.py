"""Tests for the resolution modulator — v2 Phase 1."""

from datetime import datetime, timedelta, timezone

import pytest

from core.types import UnresolvedItem, ModulatorState
from core.emotional_engine import EmotionalEngine


def _make_item(
    source: str = "contradiction",
    intensity: float = 0.5,
    decay_rate: float = 0.02,
    age_hours: float = 0.0,
    item_id: str | None = None,
) -> UnresolvedItem:
    """Helper to build an UnresolvedItem with optional age offset."""
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return UnresolvedItem(
        id=item_id or f"{source}_{intensity}",
        source=source,
        description=f"test {source}",
        created_at=created,
        intensity=intensity,
        decay_rate=decay_rate,
    )


class TestUnresolvedItem:
    def test_intensity_clamped(self):
        item = _make_item(intensity=1.5)
        assert item.intensity == 1.0
        item2 = _make_item(intensity=-0.3)
        assert item2.intensity == 0.0

    def test_decay_rate_non_negative(self):
        item = _make_item(decay_rate=-1.0)
        assert item.decay_rate == 0.0

    def test_defaults(self):
        item = _make_item()
        assert item.resolved is False
        assert item.resolved_at is None


class TestResolutionModulator:
    def test_initial_resolution_zero(self):
        engine = EmotionalEngine()
        assert engine.state.resolution == 0.0
        assert engine.unresolved_items == []

    def test_add_single_item(self):
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(intensity=0.5))
        assert engine.state.resolution > 0.0
        assert engine.state.resolution <= 1.0

    def test_add_three_items_different_decay_rates(self):
        """Spec: add 3 items with different decay rates, verify resolution value."""
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(
            source="contradiction", intensity=0.4, decay_rate=0.02, item_id="c1",
        ))
        engine.add_unresolved(_make_item(
            source="topic", intensity=0.5, decay_rate=0.05, item_id="t1",
        ))
        engine.add_unresolved(_make_item(
            source="commitment", intensity=0.3, decay_rate=0.0, item_id="k1",
        ))
        # All fresh items — time_weight ~1.0, so resolution ≈ 0.4 + 0.5 + 0.3 = 1.2 → clamped to 1.0
        assert engine.state.resolution == pytest.approx(1.0, abs=0.01)
        assert len(engine.active_unresolved()) == 3

    def test_resolve_one_recalculates(self):
        """Spec: resolve one item, verify recalculation."""
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(
            source="contradiction", intensity=0.4, decay_rate=0.02, item_id="c1",
        ))
        engine.add_unresolved(_make_item(
            source="topic", intensity=0.3, decay_rate=0.05, item_id="t1",
        ))
        resolution_before = engine.state.resolution
        engine.resolve_item("c1")
        assert engine.state.resolution < resolution_before
        assert len(engine.active_unresolved()) == 1

    def test_resolve_sets_timestamp(self):
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(item_id="x1"))
        engine.resolve_item("x1")
        item = engine.unresolved_items[0]
        assert item.resolved is True
        assert item.resolved_at is not None

    def test_resolve_nonexistent_returns_false(self):
        engine = EmotionalEngine()
        assert engine.resolve_item("nope") is False

    def test_resolve_already_resolved_returns_false(self):
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(item_id="x1"))
        assert engine.resolve_item("x1") is True
        assert engine.resolve_item("x1") is False

    def test_commitment_no_decay(self):
        """Spec: commitments don't decay — verify."""
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(
            source="commitment", intensity=0.5, decay_rate=0.0, item_id="k1",
        ))
        initial = engine.state.resolution
        # Simulate 48 hours of decay
        engine.decay(48 * 3600.0)
        assert engine.state.resolution == pytest.approx(initial, abs=0.01)

    def test_simulated_hours_decay_per_type(self):
        """Spec: wait simulated hours, verify decay per type."""
        engine = EmotionalEngine()
        # Contradiction: 0.02/hr decay
        engine.add_unresolved(_make_item(
            source="contradiction", intensity=0.5, decay_rate=0.02, item_id="c1",
        ))
        # Dialogue deadlock: 0.10/hr decay (fast)
        engine.add_unresolved(_make_item(
            source="dialogue_deadlock", intensity=0.5, decay_rate=0.10, item_id="d1",
        ))

        # Simulate 5 hours
        engine.decay(5 * 3600.0)

        items = {i.id: i for i in engine.unresolved_items}
        # Contradiction: 0.5 - 0.02*5 = 0.4
        assert items["c1"].intensity == pytest.approx(0.4, abs=0.01)
        # Deadlock: 0.5 - 0.10*5 = 0.0
        assert items["d1"].intensity == pytest.approx(0.0, abs=0.01)

    def test_resolution_clamped_to_one(self):
        engine = EmotionalEngine()
        for i in range(10):
            engine.add_unresolved(_make_item(
                intensity=0.9, item_id=f"item_{i}",
            ))
        assert engine.state.resolution <= 1.0

    def test_resolution_zero_when_all_resolved(self):
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(item_id="a"))
        engine.add_unresolved(_make_item(item_id="b"))
        engine.resolve_item("a")
        engine.resolve_item("b")
        assert engine.state.resolution == 0.0

    def test_active_unresolved_filters(self):
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(item_id="a"))
        engine.add_unresolved(_make_item(item_id="b"))
        engine.resolve_item("a")
        active = engine.active_unresolved()
        assert len(active) == 1
        assert active[0].id == "b"

    def test_resolution_in_snapshot(self):
        """Resolution must appear in snapshot() dict."""
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(intensity=0.6, item_id="s1"))
        snap = engine.snapshot()
        assert "resolution" in snap
        assert snap["resolution"] > 0.0

    def test_resolution_in_modulator_state(self):
        """ModulatorState includes resolution with default 0.0."""
        state = ModulatorState()
        assert state.resolution == 0.0
        d = state.to_dict()
        assert "resolution" in d

    def test_time_weight_decreases_with_age(self):
        """Older items with positive decay rate should have lower time weight."""
        engine = EmotionalEngine()
        # Fresh item
        engine.add_unresolved(_make_item(
            intensity=0.5, decay_rate=0.05, age_hours=0.0, item_id="fresh",
        ))
        fresh_resolution = engine.state.resolution

        # Reset and add old item
        engine2 = EmotionalEngine()
        engine2.add_unresolved(_make_item(
            intensity=0.5, decay_rate=0.05, age_hours=10.0, item_id="old",
        ))
        assert engine2.state.resolution < fresh_resolution

    def test_decay_does_not_affect_other_modulators(self):
        """Resolution decay via items should not break normal modulator decay."""
        engine = EmotionalEngine()
        engine.add_unresolved(_make_item(intensity=0.5, item_id="r1"))
        engine.state.arousal = 0.9
        engine.decay(600.0)
        # Arousal should still decay toward baseline
        assert engine.state.arousal < 0.9
