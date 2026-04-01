"""Topic profile management.

Some subjects carry emotional charge independent of who's speaking.
Topics accumulate charge from repeated emotional associations.

No LLM calls. Database reads + math.
"""

from __future__ import annotations

import sqlite3
import time

from config.loader import get_config
from core.types import TopicProfile

# ---------------------------------------------------------------------------
# Config-driven constants (loaded from config/profiles_schema.yaml)
# ---------------------------------------------------------------------------

_cfg = get_config().topic
AVOIDANCE_CHARGE_THRESHOLD = _cfg.avoidance_charge_threshold
CONFLICT_AVOIDANCE_THRESHOLD = _cfg.conflict_avoidance_threshold
CHARGE_POSITIVE_DELTA = _cfg.charge_positive_delta
CHARGE_NEGATIVE_DELTA = _cfg.charge_negative_delta


class TopicProfileManager:
    """Manages emotional charge associated with topics."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS topic_profiles (
                topic TEXT PRIMARY KEY,
                emotional_charge REAL NOT NULL DEFAULT 0.0,
                avoidance INTEGER NOT NULL DEFAULT 0,
                conflict_count INTEGER NOT NULL DEFAULT 0,
                positive_count INTEGER NOT NULL DEFAULT 0,
                negative_count INTEGER NOT NULL DEFAULT 0,
                last_updated REAL NOT NULL
            )
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get_or_create(self, topic: str) -> TopicProfile:
        """Get existing topic profile or create a new one."""
        row = self._conn.execute(
            "SELECT * FROM topic_profiles WHERE topic = ?", (topic,)
        ).fetchone()

        if row:
            return self._row_to_profile(row)

        now = time.time()
        self._conn.execute(
            "INSERT INTO topic_profiles (topic, last_updated) VALUES (?, ?)",
            (topic, now),
        )
        self._conn.commit()
        return TopicProfile(topic=topic)

    def get(self, topic: str) -> TopicProfile | None:
        """Get topic profile if it exists."""
        row = self._conn.execute(
            "SELECT * FROM topic_profiles WHERE topic = ?", (topic,)
        ).fetchone()
        return self._row_to_profile(row) if row else None

    def all_profiles(self) -> list[TopicProfile]:
        """Return all topic profiles."""
        rows = self._conn.execute(
            "SELECT * FROM topic_profiles ORDER BY emotional_charge DESC"
        ).fetchall()
        return [self._row_to_profile(row) for row in rows]

    # ------------------------------------------------------------------
    # Charge updates (asymmetric)
    # ------------------------------------------------------------------

    def record_positive(self, topic: str, intensity: float = 1.0) -> TopicProfile:
        """Record a positive association with a topic."""
        profile = self.get_or_create(topic)
        delta = CHARGE_POSITIVE_DELTA * intensity
        # Positive associations reduce charge (toward neutral)
        new_charge = max(0.0, profile.emotional_charge - delta)
        self._update_charge(topic, new_charge, positive_inc=1)
        profile.emotional_charge = new_charge
        profile.avoidance = self._should_avoid(new_charge, profile.conflict_count)
        return profile

    def record_negative(self, topic: str, intensity: float = 1.0) -> TopicProfile:
        """Record a negative association with a topic."""
        profile = self.get_or_create(topic)
        delta = CHARGE_NEGATIVE_DELTA * intensity
        new_charge = min(1.0, profile.emotional_charge + delta)
        self._update_charge(topic, new_charge, negative_inc=1)
        profile.emotional_charge = new_charge
        profile.avoidance = self._should_avoid(new_charge, profile.conflict_count)
        return profile

    def record_conflict(self, topic: str) -> TopicProfile:
        """Record a conflict event associated with a topic."""
        profile = self.get_or_create(topic)
        new_conflict_count = profile.conflict_count + 1
        new_charge = min(1.0, profile.emotional_charge + CHARGE_NEGATIVE_DELTA)
        avoidance = self._should_avoid(new_charge, new_conflict_count)
        now = time.time()
        self._conn.execute(
            """UPDATE topic_profiles SET
               emotional_charge = ?, conflict_count = ?, avoidance = ?,
               negative_count = negative_count + 1, last_updated = ?
               WHERE topic = ?""",
            (new_charge, new_conflict_count, 1 if avoidance else 0, now, topic),
        )
        self._conn.commit()
        profile.emotional_charge = new_charge
        profile.conflict_count = new_conflict_count
        profile.avoidance = avoidance
        return profile

    # ------------------------------------------------------------------
    # Avoidance
    # ------------------------------------------------------------------

    @staticmethod
    def _should_avoid(charge: float, conflict_count: int) -> bool:
        """Determine if a topic should be flagged for avoidance."""
        return (
            charge >= AVOIDANCE_CHARGE_THRESHOLD
            or conflict_count >= CONFLICT_AVOIDANCE_THRESHOLD
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_charge(
        self,
        topic: str,
        charge: float,
        positive_inc: int = 0,
        negative_inc: int = 0,
    ) -> None:
        now = time.time()
        avoidance = 1 if self._should_avoid(charge, self._get_conflict_count(topic)) else 0
        self._conn.execute(
            """UPDATE topic_profiles SET
               emotional_charge = ?, avoidance = ?,
               positive_count = positive_count + ?,
               negative_count = negative_count + ?,
               last_updated = ?
               WHERE topic = ?""",
            (charge, avoidance, positive_inc, negative_inc, now, topic),
        )
        self._conn.commit()

    def _get_conflict_count(self, topic: str) -> int:
        row = self._conn.execute(
            "SELECT conflict_count FROM topic_profiles WHERE topic = ?",
            (topic,),
        ).fetchone()
        return row["conflict_count"] if row else 0

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> TopicProfile:
        return TopicProfile(
            topic=row["topic"],
            emotional_charge=row["emotional_charge"],
            avoidance=bool(row["avoidance"]),
            conflict_count=row["conflict_count"],
        )

    def close(self) -> None:
        self._conn.close()
