"""Relationship-arc memory.

SQLite-backed storage for structured relationship events and unresolved
relational loops that should carry across sessions.
"""

from __future__ import annotations

import sqlite3
import time

from core.schema import ensure_schema_version
from core.types import OpenLoop, RelationshipContext, RelationshipEvent


class NullRelationshipMemory:
    """Disabled relationship-memory backend.

    Every write is a no-op; every read returns empty. Used in ablation
    runs where ``PipelineFeatures(relationship_memory=False)`` is set.
    Callers must not distinguish this from the real implementation —
    the contract is that the system behaves as if no relationship
    memory existed at all.
    """

    def record_event(self, event: RelationshipEvent) -> int:
        return 0

    def upsert_open_loop(self, loop: OpenLoop) -> int:
        return 0

    def resolve_matching_loop(
        self,
        *_args,
        **_kwargs,
    ) -> OpenLoop | None:
        return None

    def active_loops(self, *_args, **_kwargs) -> list[OpenLoop]:
        return []

    def recent_events(self, *_args, **_kwargs) -> list[RelationshipEvent]:
        return []

    def build_context(self, *_args, **_kwargs) -> RelationshipContext:
        return RelationshipContext()

    def count_events(self, source_person: str = "") -> int:
        return 0

    def count_open_loops(self, source_person: str = "") -> int:
        return 0

    def close(self) -> None:
        return None


class RelationshipMemory:
    """Persistent relationship memory for a single user-assistant arc."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        ensure_schema_version(self._conn)

    def record_event(self, event: RelationshipEvent) -> int:
        """Persist a relationship event and return its row id."""
        cursor = self._conn.execute(
            """INSERT INTO relationship_events
               (event_kind, source_person, topic, summary, valence,
                intensity, confidence, created_at, related_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_kind,
                event.source_person,
                event.topic,
                event.summary,
                event.valence,
                event.intensity,
                event.confidence,
                event.created_at,
                event.related_key,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def upsert_open_loop(self, loop: OpenLoop) -> int:
        """Insert or strengthen an equivalent open loop."""
        existing = self._find_open_loop(
            source_person=loop.source_person,
            loop_kind=loop.loop_kind,
            topic=loop.topic,
            related_key=loop.related_key,
        )
        if existing is not None and existing.id is not None:
            now = time.time()
            self._conn.execute(
                """UPDATE open_loops
                   SET description = ?, intensity = ?, updated_at = ?, related_key = ?
                   WHERE id = ?""",
                (
                    loop.description or existing.description,
                    max(existing.intensity, loop.intensity),
                    now,
                    loop.related_key or existing.related_key,
                    existing.id,
                ),
            )
            self._conn.commit()
            return existing.id

        cursor = self._conn.execute(
            """INSERT INTO open_loops
               (loop_kind, source_person, topic, description, intensity, status,
                created_at, updated_at, resolved_at, related_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                loop.loop_kind,
                loop.source_person,
                loop.topic,
                loop.description,
                loop.intensity,
                loop.status,
                loop.created_at,
                loop.updated_at,
                loop.resolved_at,
                loop.related_key,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def resolve_matching_loop(
        self,
        source_person: str,
        *,
        loop_kind: str = "",
        topic: str = "",
        related_key: str = "",
        description_hint: str = "",
    ) -> int | None:
        """Resolve the best matching open loop, if one exists."""
        rows = self._conn.execute(
            """SELECT * FROM open_loops
               WHERE source_person = ? AND status = 'open'
               ORDER BY updated_at DESC, intensity DESC""",
            (source_person,),
        ).fetchall()

        best_id: int | None = None
        best_score = -1
        specific_match_required = bool(topic or related_key)
        for row in rows:
            loop = self._row_to_loop(row)
            score = 0
            if loop_kind and loop.loop_kind == loop_kind:
                score += 4
            if related_key and loop.related_key and loop.related_key == related_key:
                score += 8
            if topic and loop.topic and loop.topic == topic:
                score += 5
            if description_hint and description_hint.lower() in loop.description.lower():
                score += 2
            if score > best_score:
                best_score = score
                best_id = loop.id

        if specific_match_required and best_score < 5:
            return None

        if best_id is None and rows:
            best_id = rows[0]["id"]

        if best_id is None:
            return None

        now = time.time()
        self._conn.execute(
            """UPDATE open_loops
               SET status = 'resolved', updated_at = ?, resolved_at = ?
               WHERE id = ? AND status = 'open'""",
            (now, now, best_id),
        )
        self._conn.commit()
        return int(best_id)

    def active_loops(
        self,
        source_person: str,
        *,
        topic: str = "",
        limit: int = 5,
    ) -> list[OpenLoop]:
        """Return open loops, prioritizing topic matches when provided."""
        rows = self._conn.execute(
            """SELECT * FROM open_loops
               WHERE source_person = ? AND status = 'open'
               ORDER BY updated_at DESC, intensity DESC""",
            (source_person,),
        ).fetchall()
        loops = [self._row_to_loop(row) for row in rows]
        if topic:
            loops.sort(
                key=lambda loop: (
                    0 if loop.topic and topic.lower() in loop.topic.lower() else 1,
                    -loop.intensity,
                    -loop.updated_at,
                ),
            )
        return loops[:limit]

    def recent_events(
        self,
        source_person: str,
        *,
        topic: str = "",
        limit: int = 5,
    ) -> list[RelationshipEvent]:
        """Return recent relationship events, prioritizing topic matches."""
        rows = self._conn.execute(
            """SELECT * FROM relationship_events
               WHERE source_person = ?
               ORDER BY created_at DESC""",
            (source_person,),
        ).fetchall()
        events = [self._row_to_event(row) for row in rows]
        if topic:
            events.sort(
                key=lambda event: (
                    0 if event.topic and topic.lower() in event.topic.lower() else 1,
                    -event.created_at,
                ),
            )
        return events[:limit]

    def build_context(
        self,
        source_person: str,
        *,
        topic: str = "",
        event_limit: int = 3,
        loop_limit: int = 3,
    ) -> RelationshipContext:
        """Build a compact relationship summary for generation/debug."""
        loops = self.active_loops(source_person, topic=topic, limit=loop_limit)
        events = self.recent_events(source_person, topic=topic, limit=event_limit)
        if not loops and not events:
            return RelationshipContext()

        return RelationshipContext(
            summary=self._compose_summary(loops, events),
            active_loops=loops,
            recent_events=events,
            open_loop_count=self.count_open_loops(source_person),
        )

    def count_events(self, source_person: str = "") -> int:
        """Count relationship events, optionally filtered by person."""
        if source_person:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM relationship_events WHERE source_person = ?",
                (source_person,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM relationship_events"
            ).fetchone()
        return int(row["c"])

    def count_open_loops(self, source_person: str = "") -> int:
        """Count currently open loops, optionally filtered by person."""
        if source_person:
            row = self._conn.execute(
                """SELECT COUNT(*) AS c FROM open_loops
                   WHERE source_person = ? AND status = 'open'""",
                (source_person,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM open_loops WHERE status = 'open'"
            ).fetchone()
        return int(row["c"])

    def close(self) -> None:
        self._conn.close()

    def _find_open_loop(
        self,
        *,
        source_person: str,
        loop_kind: str,
        topic: str,
        related_key: str,
    ) -> OpenLoop | None:
        rows = self._conn.execute(
            """SELECT * FROM open_loops
               WHERE source_person = ? AND status = 'open'
               ORDER BY updated_at DESC, intensity DESC""",
            (source_person,),
        ).fetchall()
        for row in rows:
            loop = self._row_to_loop(row)
            if loop.loop_kind != loop_kind:
                continue
            if related_key and loop.related_key and loop.related_key == related_key:
                return loop
            if topic and loop.topic and loop.topic == topic:
                return loop
        return None

    @staticmethod
    def _compose_summary(
        loops: list[OpenLoop],
        events: list[RelationshipEvent],
    ) -> str:
        parts: list[str] = []
        if loops:
            loop_bits = [RelationshipMemory._compact(loop.description, 70) for loop in loops[:2]]
            parts.append(f"Open loops: {'; '.join(loop_bits)}.")
        if events:
            event_bits = []
            for event in events[:3]:
                label = event.event_kind.replace("_", " ")
                subject = event.topic or event.summary
                event_bits.append(f"{label}: {RelationshipMemory._compact(subject, 56)}")
            parts.append(f"Recent arc: {'; '.join(event_bits)}.")
        return " ".join(parts)

    @staticmethod
    def _compact(text: str, limit: int) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "..."

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> RelationshipEvent:
        return RelationshipEvent(
            id=row["id"],
            event_kind=row["event_kind"],
            source_person=row["source_person"],
            topic=row["topic"],
            summary=row["summary"],
            valence=row["valence"],
            intensity=row["intensity"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            related_key=row["related_key"],
        )

    @staticmethod
    def _row_to_loop(row: sqlite3.Row) -> OpenLoop:
        return OpenLoop(
            id=row["id"],
            loop_kind=row["loop_kind"],
            source_person=row["source_person"],
            topic=row["topic"],
            description=row["description"],
            intensity=row["intensity"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            resolved_at=row["resolved_at"],
            related_key=row["related_key"],
        )
