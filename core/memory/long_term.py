"""Long-term emotional memory.

SQLite-backed persistent storage of distilled session summaries and
relational updates. Retrieval uses ACT-R activation formula biased
by current emotional state.

Asymmetric update curves: positive +0.02, negative -0.15.
Spike bypass: intensity > threshold writes with outsized weight.
Confidence threshold: only write if signal consistency > 0.6.

No LLM calls. Database + math only.
"""

from __future__ import annotations

import math
import sqlite3
import time

from config.loader import get_config
from core.types import LongTermEntry, ModulatorState

# ---------------------------------------------------------------------------
# Config-driven constants (loaded from config/modulators.yaml → memory)
# ---------------------------------------------------------------------------

_cfg = get_config().memory
POSITIVE_DELTA = _cfg.trust_positive_delta
NEGATIVE_DELTA = _cfg.trust_negative_delta
SPIKE_WEIGHT = 3.0  # multiplier for spike entries (internal)
CONFIDENCE_THRESHOLD = _cfg.confidence_threshold
ACT_R_DECAY = _cfg.act_r_decay
EMOTIONAL_BIAS_WEIGHT = _cfg.emotional_bias_weight
SPIKE_RETRIEVAL_BONUS = _cfg.spike_retrieval_bonus
TOPIC_CONTEXT_BONUS = _cfg.topic_context_bonus
PERSON_CONTEXT_BONUS = _cfg.person_context_bonus


class LongTermMemory:
    """Persistent emotional memory with ACT-R retrieval."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                summary TEXT NOT NULL,
                emotional_valence REAL NOT NULL,
                trust_delta REAL NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                source_person TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL,
                spike INTEGER NOT NULL DEFAULT 0,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed REAL NOT NULL
            )
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def store(self, entry: LongTermEntry) -> int | None:
        """Store a memory if it passes the confidence threshold.

        Returns the row ID if stored, None if rejected.
        """
        if not entry.spike and entry.confidence < CONFIDENCE_THRESHOLD:
            return None

        now = time.time()
        cursor = self._conn.execute(
            """INSERT INTO memories
               (timestamp, summary, emotional_valence, trust_delta,
                topic, source_person, confidence, spike, access_count, last_accessed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                entry.timestamp,
                entry.summary,
                entry.emotional_valence,
                entry.trust_delta,
                entry.topic,
                entry.source_person,
                entry.confidence,
                1 if entry.spike else 0,
                now,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def store_spike(self, entry: LongTermEntry) -> int:
        """Force-store a spike memory, bypassing confidence threshold.

        Spike entries get amplified weight in retrieval.
        """
        entry.spike = True
        entry.confidence = max(entry.confidence, CONFIDENCE_THRESHOLD)
        row_id = self.store(entry)
        assert row_id is not None
        return row_id

    # ------------------------------------------------------------------
    # Retrieval (ACT-R activation)
    # ------------------------------------------------------------------

    def retrieve(
        self,
        current_state: ModulatorState,
        topic: str = "",
        source_person: str = "",
        limit: int = 10,
    ) -> list[LongTermEntry]:
        """Retrieve memories ranked by ACT-R activation, biased by current emotional state.

        ACT-R base-level activation: B_i = ln(n) - d * ln(T)
        where n = access_count + 1, T = time since creation.

        Emotional bias: memories whose valence aligns with current mood
        get a boost. Negative mood → negative memories surface more.

        Spike memories get a flat activation bonus.
        """
        rows = self._conn.execute("SELECT * FROM memories").fetchall()
        now = time.time()

        scored: list[tuple[float, LongTermEntry]] = []
        for row in rows:
            entry = self._row_to_entry(row)
            activation = self._compute_activation(entry, now, current_state)

            # Topic and person context boost
            if topic and entry.topic and topic.lower() in entry.topic.lower():
                activation += TOPIC_CONTEXT_BONUS
            if source_person and entry.source_person == source_person:
                activation += PERSON_CONTEXT_BONUS

            entry.activation = activation
            scored.append((activation, entry))

        # Sort by activation descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Mark accessed
        top = scored[:limit]
        for _, entry in top:
            if entry.id is not None:
                self._mark_accessed(entry.id)

        return [entry for _, entry in top]

    def _compute_activation(
        self, entry: LongTermEntry, now: float, current_state: ModulatorState
    ) -> float:
        """ACT-R base-level activation + emotional bias + spike bonus."""
        row = self._conn.execute(
            "SELECT access_count FROM memories WHERE id = ?", (entry.id,)
        ).fetchone()
        access_count = row["access_count"] if row else 0

        # Base-level: ln(n+1) - d * ln(T+1)
        age_seconds = max(1.0, now - entry.timestamp)
        base = math.log(access_count + 1) - ACT_R_DECAY * math.log(age_seconds)

        # Emotional bias: when current valence is low, negative memories rise
        # When current valence is high, positive memories rise
        current_valence = current_state.valence
        # Map current valence from [0,1] to [-1,1] for comparison
        current_valence_signed = (current_valence - 0.5) * 2.0
        # Alignment: positive when memory valence matches mood direction
        alignment = current_valence_signed * entry.emotional_valence
        emotional_bias = alignment * EMOTIONAL_BIAS_WEIGHT

        # Spike bonus
        spike_bonus = SPIKE_RETRIEVAL_BONUS if entry.spike else 0.0

        return base + emotional_bias + spike_bonus

    def _mark_accessed(self, memory_id: int) -> None:
        self._conn.execute(
            "UPDATE memories SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
            (time.time(), memory_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Asymmetric trust update
    # ------------------------------------------------------------------

    @staticmethod
    def compute_trust_delta(valence: float) -> float:
        """Asymmetric trust update: slow build, fast break.

        Positive interactions: +0.02
        Negative interactions: -0.15
        Scaled by absolute valence magnitude.
        """
        if valence >= 0:
            return POSITIVE_DELTA * valence
        else:
            return NEGATIVE_DELTA * abs(valence)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) as c FROM memories").fetchone()
        return row["c"]

    def all(self) -> list[LongTermEntry]:
        rows = self._conn.execute(
            "SELECT * FROM memories ORDER BY timestamp DESC"
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def by_person(self, person_id: str) -> list[LongTermEntry]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE source_person = ? ORDER BY timestamp DESC",
            (person_id,),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def by_topic(self, topic: str) -> list[LongTermEntry]:
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE topic LIKE ? ORDER BY timestamp DESC",
            (f"%{topic}%",),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> LongTermEntry:
        return LongTermEntry(
            id=row["id"],
            timestamp=row["timestamp"],
            summary=row["summary"],
            emotional_valence=row["emotional_valence"],
            trust_delta=row["trust_delta"],
            topic=row["topic"],
            source_person=row["source_person"],
            confidence=row["confidence"],
            spike=bool(row["spike"]),
        )

    def close(self) -> None:
        self._conn.close()
