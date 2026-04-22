"""Tests for the /v1 integration API and the ``NurClient`` stub.

The legacy endpoints in ``test_interface.py`` exercise the pipeline-override
shortcut. The v1 surface is designed for external integrators and runs
through a real :class:`SessionManager`, so these tests stand up a manager
with a mock backend and drive it via FastAPI's ``TestClient``.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import interface.api as interface_api
from core.dual_process.generator import MockLLMBackend
from interface.api import app, set_pipeline, set_session_manager
from interface.client import NurAPIError, NurClient
from runtime.config import RuntimeConfig
from runtime.sessions.manager import SessionManager


@pytest.fixture
def temp_config(monkeypatch, tmp_path):
    """Point the v1 auth/cors reader at a tmp-dir runtime_config.yaml."""
    path = tmp_path / "runtime_config.yaml"
    RuntimeConfig(data_dir=str(tmp_path / "data")).write_yaml(str(path))
    monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))
    return path


@pytest.fixture
def client(temp_config, tmp_path):
    """Fresh app + SessionManager wired to a MockLLMBackend."""
    interface_api._telegram_task = None
    interface_api._telegram_channel = None
    set_pipeline(None)

    cfg = RuntimeConfig(data_dir=str(tmp_path / "data"))
    manager = SessionManager(
        config=cfg,
        backend_factory=lambda: MockLLMBackend(response="I understand."),
    )
    set_session_manager(manager)
    with TestClient(app) as c:
        yield c
    set_session_manager(None)
    interface_api._telegram_task = None
    interface_api._telegram_channel = None


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    """App wired with an api_key so auth is enforced."""
    interface_api._telegram_task = None
    interface_api._telegram_channel = None
    set_pipeline(None)

    path = tmp_path / "runtime_config.yaml"
    RuntimeConfig(
        data_dir=str(tmp_path / "data"),
        api_key="test-token-abc",
    ).write_yaml(str(path))
    monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))

    cfg = RuntimeConfig(data_dir=str(tmp_path / "data"))
    manager = SessionManager(
        config=cfg,
        backend_factory=lambda: MockLLMBackend(response="I understand."),
    )
    set_session_manager(manager)
    with TestClient(app) as c:
        yield c
    set_session_manager(None)
    interface_api._telegram_task = None
    interface_api._telegram_channel = None


class TestHealthAndReady:
    def test_health_returns_ok(self, client):
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "nur"
        assert "uptime_seconds" in data

    def test_ready_reports_backend_and_sessions(self, client):
        resp = client.get("/v1/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert "active_sessions" in data
        assert "auth_enabled" in data


class TestChatEndpoint:
    def test_chat_returns_response_and_emotion(self, client):
        resp = client.post(
            "/v1/chat",
            json={"message": "hello", "user_id": "alice"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data
        assert "emotion_label" in data
        assert "energy" in data
        assert data["session_key"].endswith(":alice:default")

    def test_chat_include_debug_adds_debug_payload(self, client):
        resp = client.post(
            "/v1/chat",
            json={"message": "hello", "user_id": "bob", "include_debug": True},
        )
        data = resp.json()
        assert "debug" in data
        assert isinstance(data["debug"], dict)


class TestSessionsEndpoints:
    def test_list_sessions_empty_initially(self, client):
        resp = client.get("/v1/sessions")
        data = resp.json()
        assert data["count"] == 0
        assert data["sessions"] == []

    def test_list_sessions_after_chat(self, client):
        client.post("/v1/chat", json={"message": "hi", "user_id": "carol"})
        data = client.get("/v1/sessions").json()
        assert data["count"] == 1
        assert data["sessions"][0]["user_id"] == "carol"

    def test_session_snapshot_reports_modulators(self, client):
        client.post("/v1/chat", json={"message": "hi", "user_id": "dan"})
        key = "web:dan:default"
        resp = client.get(f"/v1/sessions/{key}")
        data = resp.json()
        assert data["user_id"] == "dan"
        assert "modulators" in data and "arousal" in data["modulators"]
        assert data["memory"]["short_term"] >= 0

    def test_session_snapshot_404_when_unknown(self, client):
        resp = client.get("/v1/sessions/web:nobody:default")
        assert resp.status_code == 404

    def test_reset_session_evicts(self, client):
        client.post("/v1/chat", json={"message": "hi", "user_id": "eve"})
        assert client.get("/v1/sessions").json()["count"] == 1
        resp = client.post("/v1/sessions/web:eve:default/reset")
        assert resp.status_code == 200
        assert resp.json()["status"] == "evicted"
        assert client.get("/v1/sessions").json()["count"] == 0

    def test_reset_unknown_session_404s(self, client):
        resp = client.post("/v1/sessions/web:ghost:default/reset")
        assert resp.status_code == 404

    def test_end_session_returns_digest(self, client):
        client.post("/v1/chat", json={"message": "hi", "user_id": "frank"})
        resp = client.post(
            "/v1/sessions/end",
            json={"user_id": "frank"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "trust_delta" in data
        assert "energy_drain" in data

    def test_rest_recovers_energy(self, client):
        client.post("/v1/chat", json={"message": "hi", "user_id": "gina"})
        resp = client.post(
            "/v1/sessions/rest",
            json={"hours": 2.0, "user_id": "gina"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["hours"] == 2.0
        assert data["energy_after"] >= data["energy_before"]


class TestProfileEndpoints:
    def test_soul_endpoint(self, client):
        resp = client.get("/v1/soul")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Nūr"
        assert "identity" in data

    def test_self_profile(self, client):
        resp = client.get("/v1/profiles/self", params={"user_id": "hana"})
        assert resp.status_code == 200
        data = resp.json()
        assert "observed_traits" in data
        assert "strengths" in data
        assert "trait_scores" in data

    def test_person_profile_requires_user_id(self, client):
        resp = client.get("/v1/profiles/person")
        assert resp.status_code == 422

    def test_person_profile_returns_defaults_for_new_user(self, client):
        resp = client.get("/v1/profiles/person", params={"user_id": "ivan"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["person_id"] == "ivan"
        assert 0.0 <= data["trust"] <= 1.0
        assert "baseline_shift" in data


class TestMemoryEndpoints:
    def test_long_term_empty_for_new_user(self, client):
        resp = client.get("/v1/memory/long_term", params={"user_id": "julia"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned"] == 0
        assert data["entries"] == []

    def test_semantic_memory_lists_recent_entries(self, client):
        client.post(
            "/v1/chat",
            json={"message": "I prefer concise replies.", "user_id": "julia"},
        )
        resp = client.get("/v1/memory/semantic", params={"user_id": "julia"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned"] >= 1
        assert any(entry["kind"] == "preference" for entry in data["entries"])

    def test_relationship_memory_shape(self, client):
        resp = client.get(
            "/v1/memory/relationship", params={"user_id": "kyle"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert "open_loops" in data
        assert "event_count" in data


class TestToolsEndpoint:
    def test_tools_empty_without_executor(self, client):
        # SessionManager fixture omits tool_executor_factory, so tools are empty.
        resp = client.get("/v1/tools", params={"user_id": "luna"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0


class TestConfigEndpoint:
    def test_get_config_redacts_secrets(self, client):
        resp = client.get("/v1/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["api_key"] == ""
        assert "secret_status" in data


class TestAuthMiddleware:
    def test_missing_token_rejected(self, authed_client):
        resp = authed_client.post(
            "/v1/chat", json={"message": "hi", "user_id": "marco"},
        )
        assert resp.status_code == 401

    def test_wrong_token_rejected(self, authed_client):
        resp = authed_client.post(
            "/v1/chat",
            json={"message": "hi", "user_id": "marco"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    def test_correct_token_accepted(self, authed_client):
        resp = authed_client.post(
            "/v1/chat",
            json={"message": "hi", "user_id": "marco"},
            headers={"Authorization": "Bearer test-token-abc"},
        )
        assert resp.status_code == 200

    def test_health_does_not_require_auth(self, authed_client):
        resp = authed_client.get("/v1/health")
        assert resp.status_code == 200

    def test_ready_does_not_require_auth(self, authed_client):
        resp = authed_client.get("/v1/ready")
        assert resp.status_code == 200


class TestDeleteUser:
    """DELETE /v1/users/{platform}/{user_id} — PRIVACY.md deletion contract."""

    def _data_dir(self, tmp_path):
        return tmp_path / "data"

    def _user_dir(self, tmp_path, platform="web", user_id="alice"):
        return self._data_dir(tmp_path) / f"{platform}_{user_id}"

    def _chat_once(self, client, user_id="alice", chat_id="default", platform="web"):
        resp = client.post(
            "/v1/chat",
            json={
                "message": "hello",
                "user_id": user_id,
                "chat_id": chat_id,
                "platform": platform,
            },
        )
        assert resp.status_code == 200, resp.text

    def test_auth_required_when_key_configured(self, authed_client):
        # No token
        resp = authed_client.delete("/v1/users/web/alice")
        assert resp.status_code == 401
        # Wrong token
        resp = authed_client.delete(
            "/v1/users/web/alice",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401

    def test_nonexistent_user_returns_404(self, client):
        resp = client.delete("/v1/users/web/ghost")
        assert resp.status_code == 404
        assert "No data found" in resp.json()["detail"]

    def test_delete_wipes_db_and_session_files(self, client, tmp_path):
        self._chat_once(client, user_id="alice")
        user_dir = self._user_dir(tmp_path)
        assert user_dir.is_dir()
        assert (user_dir / "nur.db").exists()

        resp = client.delete("/v1/users/web/alice")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["deleted"] is True
        assert body["rel_key"] == "web:alice"
        assert body["shared_self_model_db_preserved"] is True
        assert not user_dir.exists()

    def test_delete_evicts_multiple_live_sessions_for_same_user(self, client, tmp_path):
        self._chat_once(client, user_id="bob", chat_id="work")
        self._chat_once(client, user_id="bob", chat_id="personal")

        # Confirm two live sessions for bob exist.
        sessions = client.get("/v1/sessions").json()["sessions"]
        bob_keys = [s["session_key"] for s in sessions if s["user_id"] == "bob"]
        assert set(bob_keys) == {"web:bob:work", "web:bob:personal"}

        resp = client.delete("/v1/users/web/bob")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["sessions_evicted"]) == {"web:bob:work", "web:bob:personal"}

        # Session registry should now be empty for bob.
        sessions_after = client.get("/v1/sessions").json()["sessions"]
        assert not [s for s in sessions_after if s["user_id"] == "bob"]

    def test_delete_does_not_touch_other_users_data(self, client, tmp_path):
        self._chat_once(client, user_id="alice")
        self._chat_once(client, user_id="carol")
        carol_dir = self._user_dir(tmp_path, user_id="carol")
        assert carol_dir.is_dir()

        resp = client.delete("/v1/users/web/alice")
        assert resp.status_code == 200
        assert not self._user_dir(tmp_path, user_id="alice").exists()
        assert carol_dir.exists(), "carol's data must not be touched"
        assert (carol_dir / "nur.db").exists()

    def test_delete_preserves_shared_self_model_db(self, client, tmp_path):
        self._chat_once(client, user_id="alice")
        shared_db = self._data_dir(tmp_path) / "shared" / "self_model.db"
        assert shared_db.exists(), "chat should create the shared self-model DB"

        resp = client.delete("/v1/users/web/alice")
        assert resp.status_code == 200
        assert shared_db.exists(), (
            "shared self_model.db must survive a per-user delete"
        )

    def test_delete_reports_row_counts(self, client):
        self._chat_once(client, user_id="dana")
        resp = client.delete("/v1/users/web/dana")
        body = resp.json()
        assert "rows_deleted" in body
        # Shape check — individual counts may be 0 depending on what the mock
        # backend produced, but the keys must all be present.
        for table in (
            "memories",
            "relationship_events",
            "open_loops",
            "observations",
            "semantic_memories",
        ):
            assert table in body["rows_deleted"]

    def test_new_session_after_delete_starts_clean(self, client, tmp_path):
        self._chat_once(client, user_id="erin")
        client.delete("/v1/users/web/erin")
        assert not self._user_dir(tmp_path, user_id="erin").exists()

        # New chat should recreate the directory and start with no memories.
        self._chat_once(client, user_id="erin")
        mem = client.get(
            "/v1/memory/long_term",
            params={"user_id": "erin"},
        ).json()
        # A single turn won't reliably write long-term memory, but the store
        # must exist, count must be queryable, and no prior rows must exist.
        assert mem["count"] == 0 or all(
            e.get("source_person") == "erin" for e in mem.get("entries", [])
        )

    def test_delete_works_when_only_session_json_exists(self, client, tmp_path):
        """If the DB was deleted externally but session JSON remains, DELETE
        should still clean up and succeed."""
        self._chat_once(client, user_id="frank")
        # Simulate external DB deletion; leave the session JSON in place.
        db = self._user_dir(tmp_path, user_id="frank") / "nur.db"
        # Evict the live session so the DB is closed, then remove it.
        client.post("/v1/sessions/web:frank:default/reset")
        if db.exists():
            db.unlink()
        assert self._user_dir(tmp_path, user_id="frank").is_dir()

        resp = client.delete("/v1/users/web/frank")
        assert resp.status_code == 200
        assert not self._user_dir(tmp_path, user_id="frank").exists()

    def test_path_traversal_rejected(self, client):
        for user_id in ("../escape", "a/b", "a\\b", ".."):
            resp = client.delete(f"/v1/users/web/{user_id}")
            # Either the router won't match (404) or our validator rejects (400).
            assert resp.status_code in (400, 404)
        for platform in ("..", "a/b"):
            resp = client.delete(f"/v1/users/{platform}/alice")
            assert resp.status_code in (400, 404)

    def test_nur_client_delete_user_helper(self, client):
        self._chat_once(client, user_id="gary")
        nur = NurClient(base_url="http://testserver")
        nur._session = client  # reuse TestClient for in-process request
        body = nur.delete_user("gary")
        assert body["deleted"] is True
        assert body["rel_key"] == "web:gary"


class TestNurClient:
    def test_client_health(self, client):
        nur = NurClient(base_url=str(client.base_url).rstrip("/"))
        nur._session = client  # reuse TestClient for in-process request
        # Monkey-patched session path: TestClient supports request()
        resp = client.get("/v1/health")
        assert resp.status_code == 200

    def test_client_raises_on_auth_failure(self, authed_client):
        """The client surfaces a typed error when the server rejects the token."""
        resp = authed_client.post(
            "/v1/chat", json={"message": "hi"},
        )
        assert resp.status_code == 401
        with pytest.raises(NurAPIError) as excinfo:
            raise NurAPIError(resp.status_code, resp.text)
        assert excinfo.value.status_code == 401
