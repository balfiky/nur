"""Person profile management.

Each person the AI interacts with gets a multi-dimensional profile.
Trust builds slow, breaks fast. Early interactions carry primacy weight.
Each person can shift the AI's modulator baseline (context switching).

No LLM calls. Database reads + math.
"""

from __future__ import annotations

import sqlite3
import time

from config.loader import get_config
from core.schema import ensure_schema_version
from core.types import BaselineShift, PersonProfile
from core.profiles.base import Observation, ProfileStore

# ---------------------------------------------------------------------------
# Config-driven constants (loaded from config/profiles_schema.yaml)
# ---------------------------------------------------------------------------

_cfg = get_config().person
TRUST_POSITIVE_DELTA = _cfg.trust_positive_delta
TRUST_NEGATIVE_DELTA = _cfg.trust_negative_delta
PRIMACY_INTERACTION_THRESHOLD = _cfg.primacy_interaction_threshold
PRIMACY_DECAY_PER_INTERACTION = _cfg.primacy_decay_per_interaction
PRIMACY_FLOOR = _cfg.primacy_floor


class PersonProfileManager:
    """Manages person profiles with trust dynamics and context switching."""

    def __init__(self, store: ProfileStore, db_path: str = ":memory:") -> None:
        self._store = store
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        ensure_schema_version(self._conn)

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS person_profiles (
                person_id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                trust REAL NOT NULL DEFAULT 0.5,
                reliability REAL NOT NULL DEFAULT 0.5,
                emotional_volatility REAL NOT NULL DEFAULT 0.5,
                stress_response TEXT NOT NULL DEFAULT 'unknown',
                baseline_arousal REAL NOT NULL DEFAULT 0.0,
                baseline_valence REAL NOT NULL DEFAULT 0.0,
                baseline_certainty REAL NOT NULL DEFAULT 0.0,
                baseline_bonding REAL NOT NULL DEFAULT 0.0,
                primacy_weight REAL NOT NULL DEFAULT 0.8,
                interaction_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get_or_create(self, person_id: str, name: str = "") -> PersonProfile:
        """Get existing profile or create a new one."""
        row = self._conn.execute(
            "SELECT * FROM person_profiles WHERE person_id = ?",
            (person_id,),
        ).fetchone()

        if row:
            return self._row_to_profile(row)

        now = time.time()
        self._conn.execute(
            """INSERT INTO person_profiles
               (person_id, name, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (person_id, name, now, now),
        )
        self._conn.commit()
        return PersonProfile(person_id=person_id, name=name)

    def save(self, profile: PersonProfile) -> None:
        """Persist a profile back to the database."""
        now = time.time()
        self._conn.execute(
            """UPDATE person_profiles SET
               name = ?, trust = ?, reliability = ?,
               emotional_volatility = ?, stress_response = ?,
               baseline_arousal = ?, baseline_valence = ?,
               baseline_certainty = ?, baseline_bonding = ?,
               primacy_weight = ?, interaction_count = ?,
               updated_at = ?
               WHERE person_id = ?""",
            (
                profile.name, profile.trust, profile.reliability,
                profile.emotional_volatility, profile.stress_response,
                profile.baseline_shift.arousal, profile.baseline_shift.valence,
                profile.baseline_shift.certainty, profile.baseline_shift.bonding,
                profile.primacy_weight, profile.interaction_count,
                now, profile.person_id,
            ),
        )
        self._conn.commit()

    def all_profiles(self) -> list[PersonProfile]:
        """Return all person profiles."""
        rows = self._conn.execute(
            "SELECT * FROM person_profiles ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row_to_profile(row) for row in rows]

    # ------------------------------------------------------------------
    # Trust dynamics (asymmetric)
    # ------------------------------------------------------------------

    def update_trust(self, person_id: str, valence: float) -> float:
        """Update trust with asymmetric curves. Returns new trust value.

        Positive interactions: +0.02 * valence (slow build)
        Negative interactions: -0.15 * |valence| (fast break)
        """
        profile = self.get_or_create(person_id)

        if valence >= 0:
            delta = TRUST_POSITIVE_DELTA * valence
        else:
            delta = TRUST_NEGATIVE_DELTA * abs(valence)

        profile.trust = max(0.0, min(1.0, profile.trust + delta))
        self.save(profile)
        return profile.trust

    # ------------------------------------------------------------------
    # Interaction recording with primacy
    # ------------------------------------------------------------------

    def record_interaction(
        self,
        person_id: str,
        traits: dict[str, float],
        context: str = "",
    ) -> None:
        """Record observed traits from an interaction.

        First N interactions get primacy flag for stronger weighting.
        """
        profile = self.get_or_create(person_id)
        profile.interaction_count += 1
        is_primacy = profile.interaction_count <= PRIMACY_INTERACTION_THRESHOLD

        # Decay primacy weight as interactions accumulate (config-driven)
        if profile.interaction_count > PRIMACY_INTERACTION_THRESHOLD:
            decay = PRIMACY_DECAY_PER_INTERACTION * (
                profile.interaction_count - PRIMACY_INTERACTION_THRESHOLD
            )
            profile.primacy_weight = max(PRIMACY_FLOOR, profile.primacy_weight - decay)

        self.save(profile)

        for trait, value in traits.items():
            obs = Observation(
                entity_id=person_id,
                trait=trait,
                value=value,
                context=context,
                is_primacy=is_primacy,
            )
            self._store.record_observation(obs)

    def get_trait_scores(self, person_id: str) -> dict[str, float]:
        """Extract current trait scores with primacy weighting."""
        profile = self.get_or_create(person_id)
        return self._store.extract_traits(person_id, primacy_weight=profile.primacy_weight)

    # ------------------------------------------------------------------
    # Context switching
    # ------------------------------------------------------------------

    def get_baseline_shift(self, person_id: str) -> BaselineShift:
        """Get the modulator baseline shift for context switching."""
        profile = self.get_or_create(person_id)
        return profile.baseline_shift

    def set_baseline_shift(self, person_id: str, shift: BaselineShift) -> None:
        """Set the modulator baseline shift for a person."""
        profile = self.get_or_create(person_id)
        profile.baseline_shift = shift
        self.save(profile)

    # ------------------------------------------------------------------
    # Expectations (for contradiction detection)
    # ------------------------------------------------------------------

    def get_expected_traits(self, person_id: str) -> dict[str, float]:
        """Return expected trait scores for contradiction comparison.

        This is the same as get_trait_scores — the profile IS the expectation.
        """
        return self.get_trait_scores(person_id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> PersonProfile:
        return PersonProfile(
            person_id=row["person_id"],
            name=row["name"],
            trust=row["trust"],
            reliability=row["reliability"],
            emotional_volatility=row["emotional_volatility"],
            stress_response=row["stress_response"],
            baseline_shift=BaselineShift(
                arousal=row["baseline_arousal"],
                valence=row["baseline_valence"],
                certainty=row["baseline_certainty"],
                bonding=row["baseline_bonding"],
            ),
            primacy_weight=row["primacy_weight"],
            interaction_count=row["interaction_count"],
        )

    def close(self) -> None:
        self._conn.close()
