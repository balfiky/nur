"""Shared profiling mechanism for all entity types.

The critical design decision: the AI profiles itself using the exact
same mechanism it uses to profile others. One observation system,
one trait extraction pipeline, one storage layer.

No LLM calls. SQLite + math only.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from config.loader import get_config

# ---------------------------------------------------------------------------
# Config-driven constants (loaded from config/profiles_schema.yaml)
# ---------------------------------------------------------------------------

_cfg = get_config().profiling
PRIMACY_DEFAULT = _cfg.primacy_default
PRIMACY_DECAY_RATE = _cfg.primacy_decay_rate
OBSERVATION_WINDOW = _cfg.observation_window


# ---------------------------------------------------------------------------
# Observation — the universal unit of profiling
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    """A single behavioral observation about an entity (self or other)."""
    id: int | None = None
    entity_id: str = ""
    timestamp: float = field(default_factory=time.time)
    trait: str = ""  # what was observed (e.g. "patient", "blunt", "avoidant")
    value: float = 0.0  # magnitude/intensity of the observation (0.0 - 1.0)
    context: str = ""  # what triggered this observation
    is_primacy: bool = False  # was this from early interactions?


# ---------------------------------------------------------------------------
# ProfileStore — SQLite-backed persistence for all profile types
# ---------------------------------------------------------------------------

class ProfileStore:
    """Shared storage layer for observations and extracted traits.

    Used by PersonProfileManager, SelfProfileManager, and TopicProfileManager.
    One database, multiple entity types sharing the same schema.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                trait TEXT NOT NULL,
                value REAL NOT NULL,
                context TEXT NOT NULL DEFAULT '',
                is_primacy INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS extracted_traits (
                entity_id TEXT NOT NULL,
                trait TEXT NOT NULL,
                score REAL NOT NULL,
                observation_count INTEGER NOT NULL DEFAULT 0,
                last_updated REAL NOT NULL,
                PRIMARY KEY (entity_id, trait)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS defense_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                defense_type TEXT NOT NULL,
                raw_intensity REAL NOT NULL,
                expressed_intensity REAL NOT NULL,
                suppression_delta REAL NOT NULL
            )
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    def record_observation(self, obs: Observation) -> int:
        """Record a behavioral observation about an entity."""
        cursor = self._conn.execute(
            """INSERT INTO observations
               (entity_id, timestamp, trait, value, context, is_primacy)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                obs.entity_id,
                obs.timestamp,
                obs.trait,
                obs.value,
                obs.context,
                1 if obs.is_primacy else 0,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_observations(
        self, entity_id: str, limit: int = OBSERVATION_WINDOW
    ) -> list[Observation]:
        """Get recent observations for an entity, newest first."""
        rows = self._conn.execute(
            """SELECT * FROM observations
               WHERE entity_id = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (entity_id, limit),
        ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def get_observations_for_trait(
        self, entity_id: str, trait: str
    ) -> list[Observation]:
        """Get all observations of a specific trait for an entity."""
        rows = self._conn.execute(
            """SELECT * FROM observations
               WHERE entity_id = ? AND trait = ?
               ORDER BY timestamp DESC""",
            (entity_id, trait),
        ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def observation_count(self, entity_id: str) -> int:
        """Total observations recorded for an entity."""
        row = self._conn.execute(
            "SELECT COUNT(*) as c FROM observations WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        return row["c"]

    # ------------------------------------------------------------------
    # Trait extraction with primacy bias
    # ------------------------------------------------------------------

    def extract_traits(
        self,
        entity_id: str,
        primacy_weight: float = 1.0,
    ) -> dict[str, float]:
        """Extract weighted trait scores from observations.

        Primacy bias: early observations (is_primacy=True) are weighted
        more heavily. This models the psychological first-impression effect.

        primacy_weight is the boost for early observations (e.g., 1.5 means
        first impressions count 1.5x). Later observations get weight 1.0.

        Returns {trait: weighted_score} for all observed traits.
        """
        observations = self.get_observations(entity_id, limit=1000)
        if not observations:
            return {}

        trait_scores: dict[str, list[float]] = {}
        for obs in observations:
            # Primacy observations keep full weight (1.0).
            # Later observations are dampened by primacy_weight (e.g., 0.8),
            # so first impressions count more than subsequent ones.
            weight = 1.0 if obs.is_primacy else primacy_weight
            trait_scores.setdefault(obs.trait, []).append(obs.value * weight)

        result: dict[str, float] = {}
        now = time.time()
        for trait, scores in trait_scores.items():
            if scores:
                avg = sum(scores) / len(scores)
                result[trait] = max(0.0, min(1.0, avg))

                # Update extracted_traits table
                self._conn.execute(
                    """INSERT INTO extracted_traits
                       (entity_id, trait, score, observation_count, last_updated)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(entity_id, trait) DO UPDATE SET
                       score = ?, observation_count = ?, last_updated = ?""",
                    (
                        entity_id, trait, avg, len(scores), now,
                        avg, len(scores), now,
                    ),
                )

        self._conn.commit()
        return result

    def get_extracted_traits(self, entity_id: str) -> dict[str, float]:
        """Get previously extracted trait scores."""
        rows = self._conn.execute(
            "SELECT trait, score FROM extracted_traits WHERE entity_id = ?",
            (entity_id,),
        ).fetchall()
        return {row["trait"]: row["score"] for row in rows}

    # ------------------------------------------------------------------
    # Defense event persistence
    # ------------------------------------------------------------------

    def record_defense_event(
        self,
        timestamp: float,
        defense_type: str,
        raw_intensity: float,
        expressed_intensity: float,
        suppression_delta: float,
    ) -> int:
        """Persist a defense activation event."""
        cursor = self._conn.execute(
            """INSERT INTO defense_events
               (timestamp, defense_type, raw_intensity, expressed_intensity, suppression_delta)
               VALUES (?, ?, ?, ?, ?)""",
            (timestamp, defense_type, raw_intensity, expressed_intensity, suppression_delta),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_defense_events(self, limit: int = 50) -> list[dict]:
        """Get recent defense events, newest first."""
        rows = self._conn.execute(
            """SELECT * FROM defense_events
               ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {
                "timestamp": row["timestamp"],
                "defense_type": row["defense_type"],
                "raw_intensity": row["raw_intensity"],
                "expressed_intensity": row["expressed_intensity"],
                "suppression_delta": row["suppression_delta"],
            }
            for row in rows
        ]

    def defense_event_count(self) -> int:
        """Total defense events recorded."""
        row = self._conn.execute("SELECT COUNT(*) as c FROM defense_events").fetchone()
        return row["c"]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_observation(row: sqlite3.Row) -> Observation:
        return Observation(
            id=row["id"],
            entity_id=row["entity_id"],
            timestamp=row["timestamp"],
            trait=row["trait"],
            value=row["value"],
            context=row["context"],
            is_primacy=bool(row["is_primacy"]),
        )

    def close(self) -> None:
        self._conn.close()
