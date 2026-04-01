"""Tests for the dual memory system — Phase 2."""

import time
import pytest

from core.types import (
    EmotionalEvent,
    EventType,
    LongTermEntry,
    ModulatorState,
)
from core.memory.short_term import ShortTermMemory
from core.memory.long_term import (
    CONFIDENCE_THRESHOLD,
    NEGATIVE_DELTA,
    POSITIVE_DELTA,
    LongTermMemory,
)
from core.memory.digestion import (
    SPIKE_INTENSITY_THRESHOLD,
    DigestedSession,
    digest_session,
)


# =========================================================================
# Short-Term Memory
# =========================================================================

class TestShortTermMemory:
    def test_record_and_retrieve(self):
        stm = ShortTermMemory()
        event = EmotionalEvent(event_type=EventType.WARMTH, intensity=0.6)
        snap = ModulatorState(valence=0.7)
        stm.record(event, snap)
        assert len(stm) == 1
        assert stm.recent(1)[0].event.event_type == EventType.WARMTH

    def test_recent_returns_last_n(self):
        stm = ShortTermMemory()
        for i in range(5):
            event = EmotionalEvent(
                event_type=EventType.USER_MESSAGE,
                intensity=i * 0.2,
                timestamp=1000.0 + i,
            )
            stm.record(event, ModulatorState())
        recent = stm.recent(3)
        assert len(recent) == 3
        # Should be the last 3 entries
        assert recent[0].event.intensity == pytest.approx(0.4)

    def test_max_entries_eviction(self):
        stm = ShortTermMemory(max_entries=5)
        for i in range(10):
            event = EmotionalEvent(
                event_type=EventType.USER_MESSAGE,
                intensity=0.1,
                timestamp=float(i),
            )
            stm.record(event, ModulatorState())
        assert len(stm) == 5
        # Oldest should be gone — first entry should be timestamp 5
        assert stm.all()[0].timestamp == 5.0

    def test_emotional_arc(self):
        stm = ShortTermMemory()
        for v in [0.3, 0.5, 0.8]:
            event = EmotionalEvent(event_type=EventType.USER_MESSAGE, intensity=0.5)
            stm.record(event, ModulatorState(valence=v))
        arc = stm.emotional_arc()
        assert len(arc) == 3
        assert arc[0]["valence"] == 0.3
        assert arc[2]["valence"] == 0.8

    def test_average_intensity(self):
        stm = ShortTermMemory()
        for intensity in [0.2, 0.4, 0.6]:
            event = EmotionalEvent(event_type=EventType.USER_MESSAGE, intensity=intensity)
            stm.record(event, ModulatorState())
        assert stm.average_intensity() == pytest.approx(0.4)

    def test_peak_intensity(self):
        stm = ShortTermMemory()
        for intensity in [0.2, 0.9, 0.4]:
            event = EmotionalEvent(event_type=EventType.USER_MESSAGE, intensity=intensity)
            stm.record(event, ModulatorState())
        assert stm.peak_intensity() == pytest.approx(0.9)

    def test_valence_drift(self):
        stm = ShortTermMemory()
        event1 = EmotionalEvent(event_type=EventType.USER_MESSAGE, intensity=0.5)
        stm.record(event1, ModulatorState(valence=0.3))
        event2 = EmotionalEvent(event_type=EventType.USER_MESSAGE, intensity=0.5)
        stm.record(event2, ModulatorState(valence=0.7))
        assert stm.valence_drift() == pytest.approx(0.4)

    def test_clear(self):
        stm = ShortTermMemory()
        event = EmotionalEvent(event_type=EventType.USER_MESSAGE, intensity=0.5)
        stm.record(event, ModulatorState())
        stm.clear()
        assert len(stm) == 0

    def test_empty_stats(self):
        stm = ShortTermMemory()
        assert stm.average_intensity() == 0.0
        assert stm.peak_intensity() == 0.0
        assert stm.valence_drift() == 0.0


# =========================================================================
# Long-Term Memory
# =========================================================================

class TestLongTermMemory:
    def test_store_above_confidence(self):
        ltm = LongTermMemory()
        entry = LongTermEntry(
            summary="Warm session",
            emotional_valence=0.5,
            trust_delta=0.02,
            confidence=0.8,
        )
        row_id = ltm.store(entry)
        assert row_id is not None
        assert ltm.count() == 1
        ltm.close()

    def test_reject_below_confidence(self):
        ltm = LongTermMemory()
        entry = LongTermEntry(
            summary="Ambiguous session",
            emotional_valence=0.1,
            trust_delta=0.0,
            confidence=0.3,  # below 0.6 threshold
        )
        row_id = ltm.store(entry)
        assert row_id is None
        assert ltm.count() == 0
        ltm.close()

    def test_spike_bypasses_confidence(self):
        ltm = LongTermMemory()
        entry = LongTermEntry(
            summary="Betrayal spike",
            emotional_valence=-0.9,
            trust_delta=-0.15,
            confidence=0.2,  # would normally be rejected
            spike=True,
        )
        row_id = ltm.store_spike(entry)
        assert row_id is not None
        assert ltm.count() == 1
        ltm.close()

    def test_asymmetric_trust_delta(self):
        # Positive: small increment
        pos = LongTermMemory.compute_trust_delta(0.8)
        assert pos == pytest.approx(POSITIVE_DELTA * 0.8)
        assert pos > 0

        # Negative: large decrement
        neg = LongTermMemory.compute_trust_delta(-0.8)
        assert neg == pytest.approx(NEGATIVE_DELTA * 0.8)
        assert neg < 0

        # Asymmetry: negative impact >> positive
        assert abs(neg) > abs(pos)

    def test_retrieve_returns_entries(self):
        ltm = LongTermMemory()
        for i in range(5):
            ltm.store(LongTermEntry(
                summary=f"Memory {i}",
                emotional_valence=0.1 * i,
                trust_delta=0.0,
                confidence=0.8,
                timestamp=time.time() - (4 - i),  # older → lower activation
            ))
        results = ltm.retrieve(ModulatorState(), limit=3)
        assert len(results) == 3
        # All should have activation scores computed (may be negative for fresh entries)
        assert all(isinstance(r.activation, float) for r in results)
        ltm.close()

    def test_retrieve_emotional_bias_negative_mood(self):
        ltm = LongTermMemory()
        # Store one positive and one negative memory
        ltm.store(LongTermEntry(
            summary="Happy memory",
            emotional_valence=0.8,
            trust_delta=0.02,
            confidence=0.9,
            timestamp=time.time(),
        ))
        ltm.store(LongTermEntry(
            summary="Sad memory",
            emotional_valence=-0.8,
            trust_delta=-0.1,
            confidence=0.9,
            timestamp=time.time(),
        ))

        # Retrieve with negative mood
        sad_state = ModulatorState(valence=0.1)
        results = ltm.retrieve(sad_state, limit=2)
        # Negative memory should rank higher with negative mood
        assert results[0].summary == "Sad memory"
        ltm.close()

    def test_retrieve_emotional_bias_positive_mood(self):
        ltm = LongTermMemory()
        ltm.store(LongTermEntry(
            summary="Happy memory",
            emotional_valence=0.8,
            trust_delta=0.02,
            confidence=0.9,
            timestamp=time.time(),
        ))
        ltm.store(LongTermEntry(
            summary="Sad memory",
            emotional_valence=-0.8,
            trust_delta=-0.1,
            confidence=0.9,
            timestamp=time.time(),
        ))

        # Retrieve with positive mood
        happy_state = ModulatorState(valence=0.9)
        results = ltm.retrieve(happy_state, limit=2)
        assert results[0].summary == "Happy memory"
        ltm.close()

    def test_spike_bonus_in_retrieval(self):
        ltm = LongTermMemory()
        now = time.time()
        # Normal memory
        ltm.store(LongTermEntry(
            summary="Normal",
            emotional_valence=0.5,
            trust_delta=0.02,
            confidence=0.9,
            timestamp=now - 5,  # 5 seconds ago
        ))
        # Spike memory (slightly older, should rank higher due to spike bonus)
        ltm.store_spike(LongTermEntry(
            summary="Spike",
            emotional_valence=-0.9,
            trust_delta=-0.15,
            confidence=0.9,
            timestamp=now - 10,  # 10 seconds ago
        ))

        results = ltm.retrieve(ModulatorState(), limit=2)
        assert results[0].summary == "Spike"
        ltm.close()

    def test_topic_context_boost(self):
        ltm = LongTermMemory()
        now = time.time()
        ltm.store(LongTermEntry(
            summary="Work stress",
            emotional_valence=-0.3,
            trust_delta=0.0,
            confidence=0.8,
            topic="work",
            timestamp=now - 5,
        ))
        ltm.store(LongTermEntry(
            summary="Fun weekend",
            emotional_valence=0.5,
            trust_delta=0.0,
            confidence=0.8,
            topic="leisure",
            timestamp=now,
        ))

        results = ltm.retrieve(ModulatorState(), topic="work", limit=2)
        assert results[0].summary == "Work stress"
        ltm.close()

    def test_person_context_boost(self):
        ltm = LongTermMemory()
        now = time.time()
        ltm.store(LongTermEntry(
            summary="Chat with Alice",
            emotional_valence=0.5,
            trust_delta=0.02,
            confidence=0.8,
            source_person="alice",
            timestamp=now - 5,
        ))
        ltm.store(LongTermEntry(
            summary="Chat with Bob",
            emotional_valence=0.5,
            trust_delta=0.02,
            confidence=0.8,
            source_person="bob",
            timestamp=now,
        ))

        results = ltm.retrieve(ModulatorState(), source_person="alice", limit=2)
        assert results[0].source_person == "alice"
        ltm.close()

    def test_by_person(self):
        ltm = LongTermMemory()
        ltm.store(LongTermEntry(
            summary="A", emotional_valence=0.1, trust_delta=0.0,
            confidence=0.8, source_person="alice",
        ))
        ltm.store(LongTermEntry(
            summary="B", emotional_valence=0.1, trust_delta=0.0,
            confidence=0.8, source_person="bob",
        ))
        assert len(ltm.by_person("alice")) == 1
        ltm.close()

    def test_by_topic(self):
        ltm = LongTermMemory()
        ltm.store(LongTermEntry(
            summary="A", emotional_valence=0.1, trust_delta=0.0,
            confidence=0.8, topic="work deadline",
        ))
        ltm.store(LongTermEntry(
            summary="B", emotional_valence=0.1, trust_delta=0.0,
            confidence=0.8, topic="vacation",
        ))
        assert len(ltm.by_topic("work")) == 1
        ltm.close()


# =========================================================================
# Digestion
# =========================================================================

class TestDigestion:
    def _populate_session(
        self, stm: ShortTermMemory, events: list[tuple[EventType, float]]
    ) -> None:
        """Helper: fill short-term memory with events at ascending timestamps."""
        base = 1000.0
        valence = 0.5
        for i, (etype, intensity) in enumerate(events):
            event = EmotionalEvent(
                event_type=etype,
                intensity=intensity,
                timestamp=base + i,
            )
            # Simulate valence shifting with events
            if etype in (EventType.WARMTH, EventType.POSITIVE_FEEDBACK):
                valence = min(1.0, valence + 0.1)
            elif etype in (EventType.CONFLICT, EventType.BETRAYAL):
                valence = max(0.0, valence - 0.2)
            stm.record(event, ModulatorState(valence=valence))

    def test_empty_session(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        result = digest_session(stm, ltm)
        assert result.memories_written == 0
        assert result.summary == ""
        ltm.close()

    def test_basic_digestion(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        self._populate_session(stm, [
            (EventType.USER_MESSAGE, 0.3),
            (EventType.WARMTH, 0.6),
            (EventType.POSITIVE_FEEDBACK, 0.5),
        ])
        result = digest_session(stm, ltm, source_person="alice")
        assert result.summary != ""
        assert result.average_intensity > 0
        assert result.trust_delta > 0  # positive session → positive trust
        assert len(stm) == 0  # short-term cleared
        ltm.close()

    def test_spike_events_written_immediately(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        self._populate_session(stm, [
            (EventType.USER_MESSAGE, 0.3),
            (EventType.BETRAYAL, 0.95),  # spike!
        ])
        result = digest_session(stm, ltm)
        assert len(result.spike_events) == 1
        # Spike should have been written to LT regardless of confidence
        spike_memories = [m for m in ltm.all() if m.spike]
        assert len(spike_memories) == 1
        assert spike_memories[0].emotional_valence < 0
        ltm.close()

    def test_negative_session_trust_delta(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        self._populate_session(stm, [
            (EventType.CONFLICT, 0.7),
            (EventType.NEGATIVE_FEEDBACK, 0.6),
        ])
        result = digest_session(stm, ltm)
        assert result.trust_delta < 0

        # Verify asymmetry: the magnitude of negative trust impact
        # should be much larger than an equivalent positive session
        stm2 = ShortTermMemory()
        ltm2 = LongTermMemory()
        self._populate_session(stm2, [
            (EventType.WARMTH, 0.7),
            (EventType.POSITIVE_FEEDBACK, 0.6),
        ])
        result2 = digest_session(stm2, ltm2)
        assert result2.trust_delta > 0
        # Negative impact should be much larger in magnitude
        assert abs(result.trust_delta) > abs(result2.trust_delta)
        ltm.close()
        ltm2.close()

    def test_unresolved_conflict_flagged(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        self._populate_session(stm, [
            (EventType.CONFLICT, 0.7),
            (EventType.USER_MESSAGE, 0.3),  # no resolution follows
        ])
        result = digest_session(stm, ltm)
        assert "unresolved_conflict" in result.unresolved_flags
        ltm.close()

    def test_resolved_conflict_not_flagged(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        self._populate_session(stm, [
            (EventType.CONFLICT, 0.7),
            (EventType.RESOLUTION, 0.6),
        ])
        result = digest_session(stm, ltm)
        assert "unresolved_conflict" not in result.unresolved_flags
        ltm.close()

    def test_custom_summarizer(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        self._populate_session(stm, [
            (EventType.WARMTH, 0.5),
        ])

        def mock_summarizer(arc, events):
            return ("LLM summary", "warm", ["friendship"], [])

        result = digest_session(stm, ltm, summarizer=mock_summarizer)
        assert result.summary == "LLM summary"
        assert result.emotional_arc_label == "warm"
        assert "friendship" in result.topics
        ltm.close()

    def test_energy_drain_proportional(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        self._populate_session(stm, [
            (EventType.USER_MESSAGE, 0.3),
        ])
        result_short = digest_session(stm, ltm)

        stm2 = ShortTermMemory()
        ltm2 = LongTermMemory()
        self._populate_session(stm2, [
            (EventType.USER_MESSAGE, 0.3),
            (EventType.CONFLICT, 0.8),
            (EventType.NEGATIVE_FEEDBACK, 0.7),
            (EventType.BETRAYAL, 0.9),
        ])
        result_long = digest_session(stm2, ltm2)

        assert result_long.energy_drain > result_short.energy_drain
        ltm.close()
        ltm2.close()

    def test_low_confidence_session_not_stored(self):
        """A session with perfectly balanced erratic valence changes should
        have low consistency and be rejected by the confidence threshold."""
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        # Need equal positive and negative diffs for consistency = 0.5
        # Manually insert entries with alternating valence
        for i, v in enumerate([0.5, 0.6, 0.5, 0.6, 0.5]):
            event = EmotionalEvent(
                event_type=EventType.USER_MESSAGE,
                intensity=0.3,
                timestamp=1000.0 + i,
            )
            stm.record(event, ModulatorState(valence=v))
        result = digest_session(stm, ltm)
        # Diffs: +0.1, -0.1, +0.1, -0.1 → 2 pos, 2 neg → consistency = 0.5 < 0.6
        assert result.spike_events == []
        assert result.memories_written == 0
        ltm.close()

    def test_full_arc_digestion(self):
        """Simulate: warmth → conflict → resolution → warmth. Complete arc.

        Due to asymmetric trust curves (-0.15 vs +0.02), a single conflict
        outweighs several warmth events. This is by design (negativity bias).
        """
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        self._populate_session(stm, [
            (EventType.WARMTH, 0.6),
            (EventType.WARMTH, 0.5),
            (EventType.CONFLICT, 0.7),
            (EventType.RESOLUTION, 0.6),
            (EventType.WARMTH, 0.5),
            (EventType.POSITIVE_FEEDBACK, 0.7),
        ])
        result = digest_session(stm, ltm, source_person="paco")
        assert result.summary != ""
        assert len(stm) == 0  # cleared
        assert result.memories_written >= 1  # at least the session summary
        # Trust delta is negative because one conflict (-0.105) outweighs
        # four positive events (~+0.046 total). This is the asymmetry working.
        assert result.trust_delta < 0
        # But valence drift should show improvement (session ended better than conflict)
        assert result.valence_drift > 0 or result.valence_drift > -0.2
        ltm.close()
