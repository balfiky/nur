"""Tests for relationship-arc memory and digestion wiring."""

from __future__ import annotations

from core.memory.digestion import digest_session
from core.memory.long_term import LongTermMemory
from core.memory.relationship import RelationshipMemory
from core.memory.short_term import ShortTermMemory
from core.types import (
    EmotionalEvent,
    EventType,
    ModulatorState,
    OpenLoop,
    RelationshipEvent,
)


def _record_user_event(
    stm: ShortTermMemory,
    event_type: EventType,
    intensity: float,
    *,
    valence: float,
    metadata: dict | None = None,
) -> None:
    stm.record(
        EmotionalEvent(
            event_type=event_type,
            intensity=intensity,
            source="user",
            metadata=metadata or {"targets_assistant": True},
        ),
        ModulatorState(valence=valence),
    )


class TestRelationshipMemory:
    def test_build_context_summarizes_open_loops_and_events(self):
        rel = RelationshipMemory()
        rel.record_event(
            RelationshipEvent(
                event_kind="rupture",
                source_person="alice",
                topic="deadline",
                summary="Rupture around deadline",
                valence=-0.7,
                intensity=0.7,
                confidence=0.8,
                related_key="deadline",
            )
        )
        rel.upsert_open_loop(
            OpenLoop(
                loop_kind="tension",
                source_person="alice",
                topic="deadline",
                description="unresolved tension about deadline",
                intensity=0.7,
                related_key="deadline",
            )
        )

        ctx = rel.build_context("alice", topic="deadline")
        assert ctx.open_loop_count == 1
        assert "Open loops:" in ctx.summary
        assert ctx.recent_events[0].event_kind == "rupture"
        rel.close()

    def test_resolve_matching_loop_marks_it_resolved(self):
        rel = RelationshipMemory()
        rel.upsert_open_loop(
            OpenLoop(
                loop_kind="tension",
                source_person="alice",
                topic="deadline",
                description="unresolved tension about deadline",
                intensity=0.6,
                related_key="deadline",
            )
        )

        resolved_id = rel.resolve_matching_loop(
            "alice",
            loop_kind="tension",
            topic="deadline",
            related_key="deadline",
        )
        assert resolved_id is not None
        assert rel.count_open_loops("alice") == 0
        rel.close()


class TestRelationshipDigestion:
    def test_digestion_writes_rupture_and_open_loop(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        rel = RelationshipMemory()
        _record_user_event(
            stm,
            EventType.CONFLICT,
            0.8,
            valence=0.2,
        )

        digest_session(
            stm,
            ltm,
            source_person="alice",
            relationship_memory=rel,
            conversation_history=[
                {"role": "user", "content": "I'm angry with you about the deadline."},
                {"role": "assistant", "content": "I understand."},
            ],
        )

        ctx = rel.build_context("alice", topic="deadline")
        assert ctx.open_loop_count == 1
        assert any(event.event_kind == "rupture" for event in ctx.recent_events)
        ltm.close()
        rel.close()

    def test_digestion_records_repair_and_resolves_loop(self):
        ltm = LongTermMemory()
        rel = RelationshipMemory()

        stm1 = ShortTermMemory()
        _record_user_event(stm1, EventType.CONFLICT, 0.8, valence=0.2)
        digest_session(
            stm1,
            ltm,
            source_person="alice",
            relationship_memory=rel,
            conversation_history=[
                {"role": "user", "content": "I'm angry with you about the deadline."},
                {"role": "assistant", "content": "I understand."},
            ],
        )

        stm2 = ShortTermMemory()
        _record_user_event(stm2, EventType.RESOLUTION, 0.7, valence=0.7)
        digest_session(
            stm2,
            ltm,
            source_person="alice",
            relationship_memory=rel,
            conversation_history=[
                {"role": "user", "content": "I'm sorry for snapping at you about the deadline."},
                {"role": "assistant", "content": "Thanks for saying that."},
            ],
        )

        ctx = rel.build_context("alice", topic="deadline")
        assert ctx.open_loop_count == 0
        assert any(event.event_kind == "repair" for event in ctx.recent_events)
        ltm.close()
        rel.close()

    def test_digestion_tracks_explicit_commitments(self):
        stm = ShortTermMemory()
        ltm = LongTermMemory()
        rel = RelationshipMemory()
        _record_user_event(
            stm,
            EventType.USER_MESSAGE,
            0.3,
            valence=0.5,
            metadata={"targets_assistant": False},
        )

        digest_session(
            stm,
            ltm,
            source_person="alice",
            relationship_memory=rel,
            conversation_history=[
                {"role": "user", "content": "Thanks for helping with the deadline."},
                {"role": "assistant", "content": "I'll follow up about the deadline tomorrow."},
            ],
        )

        ctx = rel.build_context("alice", topic="deadline")
        assert ctx.open_loop_count == 1
        assert any(event.event_kind == "commitment" for event in ctx.recent_events)
        ltm.close()
        rel.close()
