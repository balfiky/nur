"""Sprint 5.2 / 5.3 / 5.5 — surfacing, lifecycle, and admin tick tests."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

import interface.api as interface_api
from interface.api import app, set_pipeline, set_session_manager
from runtime.config import RuntimeConfig
from runtime.learning_budget import LocalBudget
from runtime.learning.surface import should_surface_question
from runtime.life_history import LifeHistoryStore


def _config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return RuntimeConfig(
        data_dir=str(tmp_path / "data"),
        tools_workspace=str(workspace),
    )


def _http_client(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = tmp_path / "runtime_config.yaml"
    RuntimeConfig(
        data_dir=str(tmp_path / "data"),
        tools_workspace=str(workspace),
        llm_backend="mock",
    ).write_yaml(str(path))
    monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))
    set_pipeline(None)
    set_session_manager(None)
    return TestClient(app), tmp_path / "data"


# ---------------------------------------------------------------------------
# should_surface_question
# ---------------------------------------------------------------------------

def test_surface_returns_none_when_no_open_questions(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        assert should_surface_question(store) is None


def test_surface_picks_open_question_when_budget_and_drives_align(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        # Plant an open drive_gap question (always eligible regardless of drive level).
        store._emit_open_question(
            prompt_text="What would help my curiosity?",
            source_kind="drive_gap",
            target_drive="curiosity",
            priority=0.7,
        )
        store._conn.commit()

        question = should_surface_question(
            store, budget=LocalBudget(max_questions_per_day=3),
        )
        assert question is not None
        assert question["target_drive"] == "curiosity"


def test_surface_blocks_when_budget_exhausted(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        store._emit_open_question(
            prompt_text="why",
            source_kind="drive_gap",
            target_drive="autonomy",
            priority=0.6,
        )
        store._conn.commit()

        budget = LocalBudget(max_questions_per_day=1)
        budget.consume(questions=1)  # exhaust budget
        assert should_surface_question(store, budget=budget) is None


def test_surface_prefers_elevated_drive_question(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        # Two contradiction questions, but curiosity drive is elevated.
        store._emit_open_question(
            prompt_text="reconcile X",
            source_kind="contradiction",
            source_belief_id=10,
            target_drive="curiosity",
            priority=0.6,
        )
        store._emit_open_question(
            prompt_text="reconcile Y",
            source_kind="contradiction",
            source_belief_id=20,
            target_drive="caution",
            priority=0.65,
        )
        # Make curiosity elevated; caution at baseline.
        store._conn.execute(
            "UPDATE drive_states SET value = 0.85 WHERE name = 'curiosity'"
        )
        store._conn.commit()

        question = should_surface_question(store)
        assert question is not None
        # Only the curiosity-aligned question is in the elevated tier.
        assert question["target_drive"] == "curiosity"


def test_pursuing_question_excluded_from_pool(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        store._emit_open_question(
            prompt_text="active gap",
            source_kind="drive_gap",
            target_drive="repair",
            priority=0.6,
        )
        store._conn.commit()
        first = store.list_open_questions()[0]
        assert store.mark_question_pursuing(first["id"]) is True

        # Now there are zero status='open' questions.
        assert should_surface_question(store) is None


# ---------------------------------------------------------------------------
# lifecycle: pursuing / resolved
# ---------------------------------------------------------------------------

def test_mark_question_pursuing_then_resolve(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        store._emit_open_question(
            prompt_text="x",
            source_kind="drive_gap",
            target_drive="competence",
        )
        store._conn.commit()
        q = store.list_open_questions()[0]

        assert store.mark_question_pursuing(q["id"]) is True
        # Cannot mark pursuing twice.
        assert store.mark_question_pursuing(q["id"]) is False

        assert store.resolve_open_question(q["id"], resolution_experience_id=42) is True
        row = store._conn.execute(
            "SELECT status, last_pursued_at, resolution_experience_id FROM open_questions WHERE id = ?",
            (q["id"],),
        ).fetchone()
        assert row["status"] == "resolved"
        assert row["last_pursued_at"]
        assert row["resolution_experience_id"] == 42


def test_resolve_unknown_question_returns_false(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        assert store.resolve_open_question(999) is False


def test_abandon_works_on_pursuing_questions(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        store._emit_open_question(
            prompt_text="x", source_kind="drive_gap", target_drive="autonomy",
        )
        store._conn.commit()
        q = store.list_open_questions()[0]
        store.mark_question_pursuing(q["id"])

        assert store.abandon_open_question(q["id"]) is True


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

def test_admin_resolve_endpoint(monkeypatch, tmp_path):
    client, _data = _http_client(monkeypatch, tmp_path)
    with client:
        # Reuse the actual data dir the API uses.
        with LifeHistoryStore(_load_api_config()) as store:
            store._emit_open_question(
                prompt_text="x",
                source_kind="drive_gap",
                target_drive="competence",
            )
            store._conn.commit()
            qid = store.list_open_questions()[0]["id"]

        resp = client.post(f"/admin/life/open-questions/{qid}/resolve")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "id": qid, "status": "resolved"}

        # Idempotent — second call 404s because no longer open/pursuing.
        resp2 = client.post(f"/admin/life/open-questions/{qid}/resolve")
        assert resp2.status_code == 404
    set_pipeline(None)
    set_session_manager(None)


def test_admin_metabolism_tick_endpoint(monkeypatch, tmp_path):
    client, _data = _http_client(monkeypatch, tmp_path)
    with client:
        # First call: just initialized, elapsed < 1 day, no-op.
        resp = client.post("/admin/life/metabolism/tick")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert payload["decayed"] is False

        # Backdate, then call again — should fire.
        with LifeHistoryStore(_load_api_config()) as store:
            store._conn.execute(
                "UPDATE metabolism_state SET last_decay_at = ? WHERE id = 1",
                (time.time() - 31 * 86400,),
            )
            store._conn.commit()

        resp2 = client.post("/admin/life/metabolism/tick")
        assert resp2.status_code == 200
        assert resp2.json()["decayed"] is True
    set_pipeline(None)
    set_session_manager(None)


def _load_api_config() -> RuntimeConfig:
    """Load whatever runtime config the API endpoint is using right now."""
    return interface_api._load_runtime_config()
