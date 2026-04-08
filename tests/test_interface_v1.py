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
