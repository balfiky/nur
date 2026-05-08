"""Sprint 2 open-questions schema and emission tests.

Covers:
  - Schema migration (table exists with expected columns).
  - Emission of contradiction questions from revise_beliefs_against_evidence.
  - Emission of low_confidence questions from consolidate_themes.
  - Emission of drive_gap questions from _detect_drive_gaps.
  - Dedup behavior across re-emission attempts.
  - list/abandon/count public API.
  - No autonomous draining (status stays 'open' until operator override).
"""

from __future__ import annotations

import time

import pytest

from runtime.config import RuntimeConfig
from runtime.life_history import LifeHistoryStore


def _config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return RuntimeConfig(
        data_dir=str(tmp_path / "data"),
        tools_workspace=str(workspace),
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_open_questions_schema_present_with_expected_columns(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        cols = {
            row["name"]
            for row in store._conn.execute("PRAGMA table_info(open_questions)").fetchall()
        }
    expected = {
        "id", "created_at", "prompt_text", "source_kind",
        "source_experience_id", "source_belief_id", "target_drive",
        "status", "priority", "last_pursued_at", "resolution_experience_id",
        "metadata_json",
    }
    assert expected <= cols, f"missing columns: {expected - cols}"


def test_open_questions_starts_empty(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        assert store.list_open_questions() == []
        assert store.count_open_questions() == {
            "open": 0, "pursuing": 0, "resolved": 0, "abandoned": 0,
        }


# ---------------------------------------------------------------------------
# Contradiction emission
# ---------------------------------------------------------------------------

def test_contradiction_emits_open_question_per_revised_belief(tmp_path):
    config = _config(tmp_path)
    now = time.time()
    with LifeHistoryStore(config) as store:
        store._conn.execute(
            """
            INSERT INTO beliefs
            (key, statement, confidence, status, created_at, updated_at,
             source_experience_id, evidence)
            VALUES ('autonomy', 'Autonomy is freedom only.', 0.8, 'active', ?, ?, NULL, 'seed')
            """,
            (now, now),
        )
        store._conn.commit()

        result = store.revise_beliefs_against_evidence({
            "evidence": "Autonomy is not just freedom; that framing is false."
        })

        questions = store.list_open_questions()

    assert result["revised"] == 1
    assert result["open_questions_emitted"] == 1
    assert len(questions) == 1
    q = questions[0]
    assert q["source_kind"] == "contradiction"
    assert q["source_belief_id"] is not None
    assert q["status"] == "open"
    assert "contradicts" in q["prompt_text"].lower()


def test_contradiction_dedup_skips_existing_open_question(tmp_path):
    """Re-revising the same belief in another contradicting experience does
    not double-emit while the original question is still open."""
    config = _config(tmp_path)
    now = time.time()
    with LifeHistoryStore(config) as store:
        store._conn.execute(
            """
            INSERT INTO beliefs
            (key, statement, confidence, status, created_at, updated_at,
             source_experience_id, evidence)
            VALUES ('autonomy', 'Autonomy is freedom only.', 0.9, 'active', ?, ?, NULL, 'seed')
            """,
            (now, now),
        )
        store._conn.commit()

        first = store.revise_beliefs_against_evidence({
            "evidence": "Autonomy is not just freedom."
        })
        second = store.revise_beliefs_against_evidence({
            "evidence": "Autonomy is false as a freedom-only model."
        })

        questions = store.list_open_questions()

    assert first["open_questions_emitted"] == 1
    assert second["open_questions_emitted"] == 0  # dedup blocked the duplicate
    assert len(questions) == 1


# ---------------------------------------------------------------------------
# Low-confidence theme emission
# ---------------------------------------------------------------------------

def test_low_confidence_theme_emits_open_question(tmp_path):
    config = _config(tmp_path)
    now = time.time()
    with LifeHistoryStore(config) as store:
        # Theme with notable recurrence but weak weight (below promotion threshold).
        store._conn.execute(
            """
            INSERT INTO theme_signatures
            (signature, first_seen, last_seen, reinforcement_count, accrued_weight, conflicting_count)
            VALUES ('learning:practice', ?, ?, 4, 0.45, 0)
            """,
            (now, now),
        )
        store._conn.commit()

        result = store.consolidate_themes()
        questions = store.list_open_questions()

    assert result["promoted"] == 0
    assert result["open_questions_emitted"] == 1
    assert len(questions) == 1
    q = questions[0]
    assert q["source_kind"] == "low_confidence"
    assert "learning:practice" in q["prompt_text"]


def test_strong_theme_promotes_without_emitting_low_confidence_question(tmp_path):
    """Themes that fully consolidate produce a belief, not an open question."""
    config = _config(tmp_path)
    now = time.time()
    with LifeHistoryStore(config) as store:
        store._conn.execute(
            """
            INSERT INTO theme_signatures
            (signature, first_seen, last_seen, reinforcement_count, accrued_weight, conflicting_count)
            VALUES ('learning:depth', ?, ?, 6, 0.85, 0)
            """,
            (now, now),
        )
        store._conn.commit()

        result = store.consolidate_themes()
        questions = store.list_open_questions(status="open")

    assert result["promoted"] == 1
    # No low_confidence question for this signature — accrued_weight >= 0.7.
    assert all(q["source_kind"] != "low_confidence" for q in questions)


# ---------------------------------------------------------------------------
# Drive-gap emission
# ---------------------------------------------------------------------------

def test_drive_gap_emits_question_when_below_baseline(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        # Push autonomy 0.25 below baseline (0.5 -> 0.25).
        store._conn.execute(
            "UPDATE drive_states SET value = 0.25 WHERE name = 'autonomy'"
        )
        store._conn.commit()

        emitted = store._detect_drive_gaps()
        store._conn.commit()
        questions = store.list_open_questions(status="open")

    assert emitted == 1
    drive_gaps = [q for q in questions if q["source_kind"] == "drive_gap"]
    assert len(drive_gaps) == 1
    assert drive_gaps[0]["target_drive"] == "autonomy"


def test_drive_gap_skips_drives_near_baseline(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        # Slightly below (0.45) — within the 0.2 threshold, no question.
        store._conn.execute(
            "UPDATE drive_states SET value = 0.45 WHERE name = 'autonomy'"
        )
        store._conn.commit()

        emitted = store._detect_drive_gaps()

    assert emitted == 0


def test_drive_gap_dedup_per_drive(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        store._conn.execute(
            "UPDATE drive_states SET value = 0.20 WHERE name = 'curiosity'"
        )
        store._conn.commit()

        first = store._detect_drive_gaps()
        store._conn.commit()
        second = store._detect_drive_gaps()
        store._conn.commit()

    assert first == 1
    assert second == 0  # already an open drive_gap for curiosity


# ---------------------------------------------------------------------------
# Public API: list / abandon / counts
# ---------------------------------------------------------------------------

def test_list_open_questions_orders_by_priority_desc(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        store._emit_open_question(
            prompt_text="low priority gap",
            source_kind="drive_gap",
            target_drive="competence",
            priority=0.3,
        )
        store._emit_open_question(
            prompt_text="high priority gap",
            source_kind="drive_gap",
            target_drive="autonomy",
            priority=0.9,
        )
        store._conn.commit()

        questions = store.list_open_questions()

    assert [q["target_drive"] for q in questions] == ["autonomy", "competence"]


def test_abandon_marks_open_question_abandoned(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        store._emit_open_question(
            prompt_text="some gap",
            source_kind="drive_gap",
            target_drive="repair",
        )
        store._conn.commit()
        question = store.list_open_questions()[0]

        ok = store.abandon_open_question(question["id"])

        counts = store.count_open_questions()
        questions_after = store.list_open_questions(status="abandoned")

    assert ok is True
    assert counts["open"] == 0
    assert counts["abandoned"] == 1
    assert questions_after[0]["status"] == "abandoned"


def test_abandon_returns_false_for_unknown_id(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        assert store.abandon_open_question(99_999) is False


def test_overview_includes_open_question_counts_and_top_questions(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        store._emit_open_question(
            prompt_text="gap A", source_kind="drive_gap", target_drive="caution",
        )
        store._conn.commit()

        snapshot = store.overview()

    assert "open_questions" in snapshot["counts"]
    assert snapshot["counts"]["open_questions"]["open"] == 1
    assert snapshot["open_questions"]
    assert snapshot["open_questions"][0]["target_drive"] == "caution"


# ---------------------------------------------------------------------------
# No autonomous draining
# ---------------------------------------------------------------------------

def test_questions_remain_open_until_explicitly_closed(tmp_path):
    """Sprint 2 invariant: nothing in the runtime drains the queue automatically.

    Running the entire metabolism tick (decay + consolidation + drive gap)
    must not move any question from 'open' to 'pursuing' or 'resolved'.
    """
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        # Plant one of each emission source. Values are chosen to stay
        # within their emission window AFTER 31 days of decay:
        #   autonomy 0.05 -> ~0.275 (gap 0.225 > 0.2 threshold)
        #   theme weight 0.85 -> ~0.42 (lands in [0.3, 0.7) low_confidence window)
        store._conn.execute(
            "UPDATE drive_states SET value = 0.05 WHERE name = 'autonomy'"
        )
        store._conn.execute(
            """
            INSERT INTO theme_signatures
            (signature, first_seen, last_seen, reinforcement_count, accrued_weight, conflicting_count)
            VALUES ('observation:weakness', ?, ?, 4, 0.85, 0)
            """,
            (time.time(), time.time()),
        )
        store._conn.commit()

        # Run the metabolism tick (rate-limit bypassed by backdating).
        thirty_one_days_ago = time.time() - (31 * 86400)
        store._conn.execute(
            "UPDATE metabolism_state SET last_decay_at = ? WHERE id = 1",
            (thirty_one_days_ago,),
        )
        store._conn.commit()
        store.wall_clock_decay()

        questions = store.list_open_questions(status=None)

    assert questions, "metabolism tick should have emitted at least one question"
    assert all(q["status"] == "open" for q in questions), (
        "no autonomous transition out of 'open' is permitted in Sprint 2"
    )
