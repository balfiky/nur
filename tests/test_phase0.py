"""Tests for Phase 0 — runtime embedding changes.

Covers:
- EmotionalEngine.restore() with and without elapsed decay
- CognitivePipeline.close()
- Shared self-profile across separate per-user pipelines
- Schema version checks for per-user and shared DBs
"""

import os
import sqlite3
import tempfile
import time

import pytest

from core.emotional_engine import EmotionalEngine
from core.memory.long_term import LongTermMemory
from core.profiles.base import ProfileStore
from core.profiles.person import PersonProfileManager
from core.profiles.self_model import SelfProfileManager, SELF_ENTITY_ID
from core.profiles.topic import TopicProfileManager
from core.schema import SCHEMA_VERSION, SchemaVersionError, ensure_schema_version
from core.types import EmotionalEvent, EventType, ModulatorState
from core.dual_process.generator import MockLLMBackend
from pipeline import CognitivePipeline


# =========================================================================
# EmotionalEngine.restore()
# =========================================================================

class TestRestore:
    def test_restore_sets_modulators(self):
        engine = EmotionalEngine()
        snapshot = {
            "arousal": 0.8,
            "valence": 0.2,
            "certainty": 0.9,
            "bonding": 0.3,
            "energy": 0.4,
            "resolution": 0.6,
        }
        engine.restore(snapshot)
        assert engine.state.arousal == pytest.approx(0.8)
        assert engine.state.valence == pytest.approx(0.2)
        assert engine.state.certainty == pytest.approx(0.9)
        assert engine.state.bonding == pytest.approx(0.3)
        assert engine.state.energy == pytest.approx(0.4)
        assert engine.state.resolution == pytest.approx(0.6)

    def test_restore_clamps_values(self):
        engine = EmotionalEngine()
        engine.restore({"arousal": 1.5, "valence": -0.3})
        assert engine.state.arousal == 1.0
        assert engine.state.valence == 0.0

    def test_restore_ignores_unknown_keys(self):
        engine = EmotionalEngine()
        before = engine.snapshot()
        engine.restore({"arousal": 0.7, "nonexistent": 99.0})
        assert engine.state.arousal == pytest.approx(0.7)
        # Other modulators unchanged
        assert engine.state.valence == pytest.approx(before["valence"])

    def test_restore_partial_snapshot(self):
        engine = EmotionalEngine()
        engine.restore({"energy": 0.3})
        assert engine.state.energy == pytest.approx(0.3)
        # Non-specified modulators keep their defaults
        assert engine.state.arousal == pytest.approx(0.5)

    def test_restore_without_saved_at_no_decay(self):
        engine = EmotionalEngine()
        engine.restore({"arousal": 0.9})
        # Without saved_at, no decay applied — value stays as set
        assert engine.state.arousal == pytest.approx(0.9)

    def test_restore_with_saved_at_applies_decay(self):
        engine = EmotionalEngine()
        # Push arousal far from baseline
        snapshot = {"arousal": 0.95, "valence": 0.1}
        # Pretend state was saved 2 hours ago
        saved_at = time.time() - 7200
        engine.restore(snapshot, saved_at=saved_at)
        # After 2 hours of decay, arousal should have moved toward baseline (0.5)
        assert engine.state.arousal < 0.95
        # Valence should have moved toward baseline (0.5)
        assert engine.state.valence > 0.1

    def test_restore_with_recent_saved_at_minimal_decay(self):
        engine = EmotionalEngine()
        snapshot = {"arousal": 0.9}
        # Saved 1 second ago — negligible decay
        saved_at = time.time() - 1
        engine.restore(snapshot, saved_at=saved_at)
        assert engine.state.arousal == pytest.approx(0.9, abs=0.02)

    def test_restore_updates_last_update_time(self):
        engine = EmotionalEngine()
        before = engine._last_update_time
        time.sleep(0.01)
        engine.restore({"arousal": 0.7})
        assert engine._last_update_time > before


# =========================================================================
# CognitivePipeline.restore_state()
# =========================================================================

class TestPipelineRestoreState:
    def _make_pipeline(self):
        return CognitivePipeline(llm_backend=MockLLMBackend())

    def test_restore_state_sets_engine(self):
        pipe = self._make_pipeline()
        pipe.restore_state({"arousal": 0.8, "energy": 0.3})
        assert pipe.engine.state.arousal == pytest.approx(0.8)
        assert pipe.engine.state.energy == pytest.approx(0.3)

    def test_restore_state_with_elapsed_decay(self):
        pipe = self._make_pipeline()
        saved_at = time.time() - 3600  # 1 hour ago
        pipe.restore_state({"arousal": 0.95}, saved_at=saved_at)
        assert pipe.engine.state.arousal < 0.95


# =========================================================================
# CognitivePipeline.close()
# =========================================================================

class TestPipelineClose:
    def test_close_runs_without_error(self):
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        pipe.process("hello", user_id="alice")
        pipe.close()

    def test_close_idempotent(self):
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        pipe.close()
        # Second close should not raise
        pipe.close()

    def test_close_with_file_dbs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            self_db = os.path.join(tmpdir, "self.db")
            pipe = CognitivePipeline(
                llm_backend=MockLLMBackend(),
                db_path=db,
                self_db_path=self_db,
            )
            pipe.process("hi", user_id="u1")
            pipe.close()
            # DB files should exist
            assert os.path.exists(db)
            assert os.path.exists(self_db)


# =========================================================================
# Shared self-profile across per-user pipelines
# =========================================================================

class TestSharedSelfProfile:
    def test_shared_self_profile_across_pipelines(self):
        """Two pipelines with different per-user DBs but same self_db_path
        should share self-model observations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            user1_db = os.path.join(tmpdir, "user1.db")
            user2_db = os.path.join(tmpdir, "user2.db")
            shared_db = os.path.join(tmpdir, "self_model.db")

            pipe1 = CognitivePipeline(
                llm_backend=MockLLMBackend(),
                db_path=user1_db,
                self_db_path=shared_db,
            )
            pipe2 = CognitivePipeline(
                llm_backend=MockLLMBackend(),
                db_path=user2_db,
                self_db_path=shared_db,
            )

            # Process a message through pipe1 — records self-observations
            pipe1.process("I'm so angry about this argument!", user_id="user1")

            # pipe2 should see the self-observations from pipe1
            obs_count_pipe2 = pipe2._self_profile_store.observation_count(SELF_ENTITY_ID)
            assert obs_count_pipe2 > 0

            pipe1.close()
            pipe2.close()

    def test_per_user_data_isolated(self):
        """Two pipelines with different db_paths should have isolated
        person profiles and memories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            user1_db = os.path.join(tmpdir, "user1.db")
            user2_db = os.path.join(tmpdir, "user2.db")
            shared_db = os.path.join(tmpdir, "self_model.db")

            pipe1 = CognitivePipeline(
                llm_backend=MockLLMBackend(),
                db_path=user1_db,
                self_db_path=shared_db,
            )
            pipe2 = CognitivePipeline(
                llm_backend=MockLLMBackend(),
                db_path=user2_db,
                self_db_path=shared_db,
            )

            pipe1.process("hello", user_id="user1")
            pipe2.process("hello", user_id="user2")

            # Person profiles are per-user DB — isolated
            p1_profiles = pipe1.person_profiles.all_profiles()
            p2_profiles = pipe2.person_profiles.all_profiles()
            p1_ids = {p.person_id for p in p1_profiles}
            p2_ids = {p.person_id for p in p2_profiles}
            assert "user1" in p1_ids
            assert "user2" not in p1_ids
            assert "user2" in p2_ids
            assert "user1" not in p2_ids

            pipe1.close()
            pipe2.close()

    def test_backward_compat_single_db(self):
        """Without self_db_path, everything goes to db_path (v1 behavior)."""
        pipe = CognitivePipeline(llm_backend=MockLLMBackend())
        pipe.process("test", user_id="alice")
        # Self observations exist in the default store
        obs = pipe._self_profile_store.observation_count(SELF_ENTITY_ID)
        # May be 0 for calm messages, but the store should be functional
        assert obs >= 0
        pipe.close()


# =========================================================================
# Schema version checks
# =========================================================================

class TestSchemaVersion:
    def test_fresh_db_gets_current_version(self):
        conn = sqlite3.connect(":memory:")
        version = ensure_schema_version(conn)
        assert version == SCHEMA_VERSION
        conn.close()

    def test_matching_version_passes(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
        version = ensure_schema_version(conn)
        assert version == SCHEMA_VERSION
        conn.close()

    def test_future_version_raises(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION + 1,))
        conn.commit()
        with pytest.raises(SchemaVersionError):
            ensure_schema_version(conn)
        conn.close()

    def test_older_version_migrates(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (0,))
        conn.commit()
        version = ensure_schema_version(conn)
        assert version == 0  # returns old version; table updated to current
        # Verify the table was updated
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == SCHEMA_VERSION
        conn.close()

    def test_profile_store_has_schema_version(self):
        store = ProfileStore()
        row = store._conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION
        store.close()

    def test_long_term_memory_has_schema_version(self):
        ltm = LongTermMemory()
        row = ltm._conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION
        ltm.close()

    def test_person_profile_manager_has_schema_version(self):
        store = ProfileStore()
        ppm = PersonProfileManager(store)
        row = ppm._conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION
        ppm.close()
        store.close()

    def test_topic_profile_manager_has_schema_version(self):
        tpm = TopicProfileManager()
        row = tpm._conn.execute("SELECT version FROM schema_version").fetchone()
        assert row["version"] == SCHEMA_VERSION
        tpm.close()

    def test_schema_version_in_file_db(self):
        """Schema version persists in file-based databases."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            store = ProfileStore(db_path=db)
            store.close()

            # Reopen and verify
            conn = sqlite3.connect(db)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            assert row[0] == SCHEMA_VERSION
            conn.close()

    def test_future_version_blocks_component_init(self):
        """A DB with a future schema version should block component creation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "future.db")
            # Pre-create DB with future version
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (999,))
            conn.commit()
            # Also create the tables the component expects so it doesn't fail on table creation
            conn.execute("""
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS extracted_traits (
                    entity_id TEXT NOT NULL,
                    trait TEXT NOT NULL,
                    score REAL NOT NULL,
                    observation_count INTEGER NOT NULL DEFAULT 0,
                    last_updated REAL NOT NULL,
                    PRIMARY KEY (entity_id, trait)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS defense_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    defense_type TEXT NOT NULL,
                    raw_intensity REAL NOT NULL,
                    expressed_intensity REAL NOT NULL,
                    suppression_delta REAL NOT NULL
                )
            """)
            conn.commit()
            conn.close()

            with pytest.raises(SchemaVersionError):
                ProfileStore(db_path=db)
