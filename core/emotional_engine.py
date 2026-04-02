"""PSI-inspired emotional state machine.

Five continuous modulators (arousal, valence, certainty, bonding, energy),
each with its own decay curve. Emotions emerge from modulator combinations,
not from labels.

No LLM calls. Pure math.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from config.loader import get_config
from core.types import (
    AttachmentStyle,
    BaselineShift,
    EmotionalEvent,
    EventType,
    ModulatorName,
    ModulatorState,
    UnresolvedItem,
)

# ---------------------------------------------------------------------------
# Config-driven constants (loaded from config/modulators.yaml)
# ---------------------------------------------------------------------------


def _build_event_impacts(cfg_impacts: dict[str, dict[str, float]]) -> dict[EventType, dict[str, float]]:
    """Convert string-keyed config to EventType-keyed dict."""
    result: dict[EventType, dict[str, float]] = {}
    for event_name, impacts in cfg_impacts.items():
        try:
            et = EventType(event_name)
            result[et] = impacts
        except ValueError:
            pass
    return result


def _load_constants() -> tuple[
    dict[str, float],  # half_lives
    dict[EventType, dict[str, float]],  # event_impacts
    float,  # drain_per_message
    float,  # drain_per_spike
    float,  # recovery_rate
    float,  # spike_threshold
    float,  # contagion_factor
    float,  # contagion_cap
    dict[str, float],  # resolution_decay_rates
]:
    cfg = get_config()
    half_lives = dict(cfg.half_lives)
    if cfg.event_impacts:
        event_impacts = _build_event_impacts(cfg.event_impacts)
    else:
        event_impacts = {}
    return (
        half_lives,
        event_impacts,
        cfg.energy.drain_per_message,
        cfg.energy.drain_per_spike,
        cfg.energy.recovery_rate_per_hour,
        cfg.spike_threshold,
        cfg.contagion_engine.factor,
        cfg.contagion_engine.cap,
        dict(cfg.resolution.decay_rates),
    )


(
    DEFAULT_HALF_LIVES,
    EVENT_IMPACTS,
    ENERGY_DRAIN_PER_MESSAGE,
    ENERGY_DRAIN_PER_SPIKE,
    ENERGY_RECOVERY_RATE,
    SPIKE_INTENSITY_THRESHOLD,
    _CONTAGION_FACTOR,
    _CONTAGION_CAP,
    RESOLUTION_DECAY_RATES,
) = _load_constants()


class EmotionalEngine:
    """Maintains and updates the five-modulator emotional state."""

    def __init__(
        self,
        baseline: ModulatorState | None = None,
        attachment: AttachmentStyle = AttachmentStyle.SECURE,
        half_lives: dict[str, float] | None = None,
    ) -> None:
        self.baseline = baseline or ModulatorState()
        self.state = self.baseline.copy()
        self.attachment = attachment
        self.half_lives = half_lives or dict(DEFAULT_HALF_LIVES)
        self._last_update_time = time.time()
        # v2: resolution modulator
        self.unresolved_items: list[UnresolvedItem] = []
        self._resolution_decay_rates = dict(RESOLUTION_DECAY_RATES)
        # Context shift: resting target offset for current relationship
        self._context_shift: BaselineShift | None = None

    # ------------------------------------------------------------------
    # State restore (Phase 0 — runtime support)
    # ------------------------------------------------------------------

    def restore(
        self,
        snapshot: dict[str, float],
        saved_at: float | None = None,
    ) -> None:
        """Restore modulator state from a snapshot dict.

        Args:
            snapshot: Dict of modulator name → value (same format as snapshot()).
            saved_at: Unix timestamp when the snapshot was saved. If provided,
                      elapsed wall-clock time is computed and decay is applied
                      before use, so restored state reflects time passing.
        """
        for mod in ModulatorName:
            if mod.value in snapshot:
                val = max(0.0, min(1.0, snapshot[mod.value]))
                setattr(self.state, mod.value, val)

        if saved_at is not None:
            elapsed = time.time() - saved_at
            if elapsed > 0:
                self.decay(elapsed)

        self._last_update_time = time.time()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def update(self, event: EmotionalEvent) -> bool:
        """Apply an event to the modulator state. Returns True if spike."""
        impacts = EVENT_IMPACTS.get(event.event_type, {})
        is_spike = event.intensity >= SPIKE_INTENSITY_THRESHOLD

        for mod_name, base_delta in impacts.items():
            if mod_name == "energy":
                # Energy impact is not intensity-scaled the same way
                delta = base_delta
                if is_spike:
                    delta -= ENERGY_DRAIN_PER_SPIKE
            else:
                delta = base_delta * event.intensity

            # Attachment style modifies bonding updates
            if mod_name == "bonding":
                delta = self._apply_attachment(delta)

            current = getattr(self.state, mod_name)
            setattr(self.state, mod_name, max(0.0, min(1.0, current + delta)))

        self._last_update_time = time.time()
        return is_spike

    def decay(self, elapsed_seconds: float) -> None:
        """Apply time-based exponential decay toward baseline."""
        for mod in ModulatorName:
            if mod == ModulatorName.ENERGY:
                # Energy recovers toward 1.0 based on rest time
                self._recover_energy(elapsed_seconds)
                continue
            if mod == ModulatorName.RESOLUTION:
                # Resolution decays via item-level mechanism, not half-life
                self._decay_unresolved_items(elapsed_seconds)
                continue

            half_life = self.half_lives.get(mod.value)
            if half_life is None:
                continue

            current = getattr(self.state, mod.value)
            base = self.effective_baseline(mod.value)
            # Exponential decay toward effective baseline (baseline + context shift)
            decay_factor = math.exp(-0.693 * elapsed_seconds / half_life)
            new_val = base + (current - base) * decay_factor
            setattr(self.state, mod.value, max(0.0, min(1.0, new_val)))

        self._last_update_time = time.time()

    def set_context_shift(self, shift: BaselineShift) -> None:
        """Set the context shift for the current relationship.

        Not additive — stores the shift as a resting target offset.
        Decay will pull modulators toward baseline + shift, not accumulate.
        """
        self._context_shift = shift

    def effective_baseline(self, mod_name: str) -> float:
        """Baseline + context shift for a modulator."""
        base = getattr(self.baseline, mod_name)
        if self._context_shift is not None:
            shift_val = self._context_shift.to_dict().get(mod_name, 0.0)
            base += shift_val
        return max(0.0, min(1.0, base))

    def apply_context_shift(self, shift: BaselineShift) -> None:
        """Legacy wrapper — calls set_context_shift."""
        self.set_context_shift(shift)

    def apply_contagion(
        self, user_arousal: float, user_valence: float, bonding_score: float
    ) -> None:
        """Partially mirror detected user emotion into arousal + valence.

        Bounded: max ±0.15 shift. Weighted by bonding score.
        Strong signals (arousal > 0.7 or valence < 0.3) get a minimum
        floor to ensure perceptible movement.
        """
        weight = bonding_score * _CONTAGION_FACTOR
        cap = _CONTAGION_CAP
        min_delta = 0.05  # minimum shift for strong signals

        arousal_delta = (user_arousal - self.state.arousal) * weight
        # For strong arousal signals, ensure minimum shift
        if user_arousal > 0.7 and arousal_delta > 0:
            arousal_delta = max(min_delta, arousal_delta)
        arousal_delta = max(-cap, min(cap, arousal_delta))
        self.state.arousal = max(0.0, min(1.0, self.state.arousal + arousal_delta))

        valence_delta = (user_valence - self.state.valence) * weight
        # For strong negative valence, ensure minimum shift
        if user_valence < 0.3 and valence_delta < 0:
            valence_delta = min(-min_delta, valence_delta)
        valence_delta = max(-cap, min(cap, valence_delta))
        self.state.valence = max(0.0, min(1.0, self.state.valence + valence_delta))

    def drain_energy(self, intensity: float = 0.0) -> None:
        """Drain energy after processing a message."""
        drain = ENERGY_DRAIN_PER_MESSAGE + (intensity * 0.03)
        self.state.energy = max(0.0, self.state.energy - drain)

    def snapshot(self) -> dict[str, float]:
        """Current state as a dict for injection into LLM context."""
        return self.state.to_dict()

    def to_emotion_label(self) -> str:
        """Best-fit emotion label from current modulator state. For logging only."""
        s = self.state
        # Simple heuristic matching against known patterns
        if s.energy < 0.2:
            if s.arousal > 0.5:
                return "irritable"
            return "exhausted"
        if s.arousal > 0.7 and s.valence < 0.3:
            if s.certainty > 0.7:
                return "angry"
            return "fearful"
        if s.arousal > 0.7 and s.valence > 0.7:
            return "excited"
        if s.valence > 0.7 and s.bonding > 0.7:
            if s.arousal < 0.4:
                return "loving"
            return "joyful"
        if s.valence > 0.7:
            return "content"
        if s.valence < 0.3 and s.arousal < 0.3:
            return "sad"
        if s.certainty < 0.3:
            if s.arousal > 0.6:
                return "anxious"
            return "uncertain"
        if s.valence < 0.3 and s.bonding < 0.3:
            return "detached"
        return "neutral"

    # ------------------------------------------------------------------
    # Resolution modulator (v2)
    # ------------------------------------------------------------------

    def add_unresolved(self, item: UnresolvedItem) -> None:
        """Add an unresolved tension item and recalculate resolution."""
        self.unresolved_items.append(item)
        self._recalculate_resolution()

    def resolve_item(self, item_id: str) -> bool:
        """Mark an item as resolved. Returns True if found."""
        for item in self.unresolved_items:
            if item.id == item_id and not item.resolved:
                item.resolved = True
                item.resolved_at = datetime.now(timezone.utc)
                self._recalculate_resolution()
                return True
        return False

    def active_unresolved(self) -> list[UnresolvedItem]:
        """Return all active (unresolved) items."""
        return [i for i in self.unresolved_items if not i.resolved]

    def _recalculate_resolution(self) -> None:
        """Recompute resolution modulator from active unresolved items.

        Uses item intensity directly (already decayed by _decay_unresolved_items).
        No additional age-based weighting to avoid double-decay.
        """
        active = self.active_unresolved()
        if not active:
            self.state.resolution = 0.0
            return
        total = sum(i.intensity for i in active)
        self.state.resolution = max(0.0, min(1.0, total))

    def _decay_unresolved_items(self, elapsed_seconds: float) -> None:
        """Apply time-based intensity decay to unresolved items."""
        hours = elapsed_seconds / 3600.0
        for item in self.active_unresolved():
            if item.decay_rate > 0.0:
                item.intensity = max(0.0, item.intensity - item.decay_rate * hours)
        self._recalculate_resolution()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_attachment(self, bonding_delta: float) -> float:
        """Modify bonding update based on attachment style.

        v1: locked to secure. Other styles exist for future use.
        """
        if self.attachment == AttachmentStyle.SECURE:
            return bonding_delta  # proportional, no modification
        elif self.attachment == AttachmentStyle.ANXIOUS:
            # Rises fast, falls fast
            return bonding_delta * (1.5 if bonding_delta > 0 else 1.3)
        elif self.attachment == AttachmentStyle.AVOIDANT:
            # Rises slow, caps low
            if bonding_delta > 0:
                remaining = max(0, 0.6 - self.state.bonding)
                return bonding_delta * 0.5 * (remaining / 0.6 if remaining > 0 else 0)
            return bonding_delta * 0.7
        elif self.attachment == AttachmentStyle.DISORGANIZED:
            # Unpredictable oscillation
            import random
            return bonding_delta * random.uniform(0.5, 2.0) * random.choice([1, -1])
        return bonding_delta

    def _recover_energy(self, elapsed_seconds: float) -> None:
        """Recover energy based on elapsed rest time."""
        hours = elapsed_seconds / 3600.0
        recovery = hours * ENERGY_RECOVERY_RATE
        self.state.energy = min(1.0, self.state.energy + recovery)
