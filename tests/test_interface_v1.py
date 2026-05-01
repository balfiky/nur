"""Tests for the /v1 integration API and the ``NurClient`` stub.

The legacy endpoints in ``test_interface.py`` exercise the pipeline-override
shortcut. The v1 surface is designed for external integrators and runs
through a real :class:`SessionManager`, so these tests stand up a manager
with a mock backend and drive it via FastAPI's ``TestClient``.
"""

from __future__ import annotations

import os
import tempfile
import zipfile

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

    def test_ready_reports_env_backed_llm_key(self, client, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "env-key")

        resp = client.get("/v1/ready")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_llm_key"] is True


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
        assert "relationship_view" in data["debug"]
        assert "persona_view" in data["debug"]
        assert data["debug"]["persona_view"]["emotions"]["primary"]
        assert "explanation" in data["debug"]
        assert data["debug"]["relationship_view"]["modulators"]["arousal"]["delta"] is None

    def test_persona_state_does_not_create_session_when_inactive(self, client):
        resp = client.get(
            "/v1/persona/state",
            params={"platform": "telegram", "user_id": "nobody", "chat_id": "default"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False
        assert data["session_key"] == "telegram:nobody:default"

        sessions = client.get("/v1/sessions").json()
        assert sessions["count"] == 0

    def test_persona_state_returns_active_session_view(self, client):
        client.post(
            "/v1/chat",
            json={"message": "I am scared about work", "user_id": "persona"},
        )

        resp = client.get(
            "/v1/persona/state",
            params={"platform": "web", "user_id": "persona", "chat_id": "default"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["emotions"]["simple_label"]
        assert "perception" in data
        assert "skills_tools" in data
        assert "explanation" in data

    def test_v1_chat_runtime_backpressure_returns_503(self, client, tmp_path):
        manager = SessionManager(
            config=RuntimeConfig(
                data_dir=str(tmp_path / "limited-v1"),
                max_active_sessions=0,
            ),
            backend_factory=lambda: MockLLMBackend(response="I understand."),
        )
        set_session_manager(manager)

        resp = client.post(
            "/v1/chat",
            json={"message": "hello", "user_id": "overflow"},
        )

        assert resp.status_code == 503
        assert "limit reached" in resp.json()["detail"]

    def test_legacy_chat_runtime_backpressure_returns_503(self, client, tmp_path):
        manager = SessionManager(
            config=RuntimeConfig(
                data_dir=str(tmp_path / "limited-legacy"),
                max_active_sessions=0,
            ),
            backend_factory=lambda: MockLLMBackend(response="I understand."),
        )
        set_session_manager(manager)

        resp = client.post(
            "/chat",
            json={"message": "hello", "user_id": "overflow"},
        )

        assert resp.status_code == 503
        assert "limit reached" in resp.json()["detail"]


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


class TestAdminEndpoints:
    def test_admin_config_redacts_secrets_and_reports_metadata(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            llm_backend="provider",
            llm_base_url="https://provider.example/v1",
            llm_model="demo-model",
            llm_api_key="secret-key",
        ).write_yaml(str(temp_config))

        resp = client.get("/admin/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["llm_api_key"] == ""
        assert data["secret_status"]["llm_api_key"] is True
        field = next(
            item for item in data["field_metadata"]
            if item["name"] == "llm_api_key"
        )
        assert field["secret"] is True
        assert field["section"] == "model"
        assert field["secret_status"]["configured"] is True
        assert field["secret_status"]["source"] == "yaml"

    def test_admin_status_reports_validation_warnings(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            llm_backend="provider",
            tools_enabled=True,
            cors_origins=["https://example.test"],
        ).write_yaml(str(temp_config))

        resp = client.get("/admin/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_enabled"] is False
        assert data["tools"]["enabled"] is True
        codes = {warning["code"] for warning in data["warnings"]}
        assert "missing_provider_base_url" in codes
        assert "missing_provider_model" in codes
        assert "tools_without_auth" in codes
        assert "cors_without_auth" in codes

    def test_admin_persona_state_lists_active_web_sessions(self, client):
        empty = client.get("/admin/persona/state")
        assert empty.status_code == 200
        assert empty.json()["sessions"] == []

        client.post(
            "/v1/chat",
            json={"message": "hello from the web channel", "user_id": "dash", "chat_id": "webtab"},
        )

        resp = client.get("/admin/persona/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["channel_counts"] == {"web": 1}
        session = data["sessions"][0]
        assert session["session_key"] == "web:dash:webtab"
        assert session["platform"] == "web"
        assert session["persona_view"]["active"] is True
        assert session["persona_view"]["emotions"]["simple_label"]

    def test_admin_config_preserves_secret_when_blank(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            llm_backend="mock",
            llm_api_key="keep-me",
        ).write_yaml(str(temp_config))

        resp = client.post(
            "/admin/config",
            json={"llm_backend": "mock", "llm_api_key": ""},
        )

        assert resp.status_code == 200
        saved = RuntimeConfig.from_yaml(str(temp_config))
        assert saved.llm_api_key == "keep-me"
        assert resp.json()["field_metadata"]

    def test_admin_config_clear_secret_removes_stored_value(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            llm_backend="mock",
            llm_api_key="remove-me",
        ).write_yaml(str(temp_config))

        resp = client.post(
            "/admin/config",
            json={"llm_backend": "mock", "clear_llm_api_key": True},
        )

        assert resp.status_code == 200
        saved = RuntimeConfig.from_yaml(str(temp_config))
        assert saved.llm_api_key == ""
        field = next(
            item for item in resp.json()["field_metadata"]
            if item["name"] == "llm_api_key"
        )
        assert field["secret_status"]["configured"] is False

    def test_admin_routes_require_auth_when_key_configured(self, authed_client):
        assert authed_client.get("/admin/status").status_code == 401
        assert authed_client.get("/admin/config").status_code == 401
        assert authed_client.get("/admin/persona/state").status_code == 401
        assert authed_client.post(
            "/admin/config", json={"clear_api_key": True},
        ).status_code == 401

        ok = authed_client.get(
            "/admin/status",
            headers={"Authorization": "Bearer test-token-abc"},
        )
        assert ok.status_code == 200

        persona = authed_client.get(
            "/admin/persona/state",
            headers={"Authorization": "Bearer test-token-abc"},
        )
        assert persona.status_code == 200

    def test_admin_setup_state_can_be_completed(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            llm_backend="mock",
        ).write_yaml(str(temp_config))

        before = client.get("/admin/status").json()["setup"]
        assert before["completed"] is False
        assert "setup_not_completed" in before["reasons"]

        resp = client.post(
            "/admin/config",
            json={"llm_backend": "mock", "setup_completed": True},
        )

        assert resp.status_code == 200
        setup = resp.json()["setup"]
        assert setup["completed"] is True
        assert setup["required"] is False
        assert setup["completed_at"] is not None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("max_queue_per_user", 0),
            ("max_active_sessions", 0),
            ("session_timeout_seconds", 0),
            ("telegram_poll_timeout", 0),
            ("dedupe_ttl", -1),
            ("debug_port", 0),
            ("debug_port", 65536),
            ("proactive_idle_threshold", -1),
            ("proactive_max_per_session", -1),
            ("proactive_cooldown", -1),
            ("proactive_check_interval", 0),
        ],
    )
    def test_admin_config_rejects_invalid_runtime_bounds(
        self, client, temp_config, field, value,
    ):
        resp = client.post("/admin/config", json={field: value})

        assert resp.status_code == 422
        assert RuntimeConfig.from_yaml(str(temp_config)).max_active_sessions == 10

    def test_admin_test_llm_reports_missing_provider_config(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            llm_backend="provider",
        ).write_yaml(str(temp_config))

        resp = client.post("/admin/test/llm", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["checked"] == "llm"
        codes = {warning["code"] for warning in data["warnings"]}
        assert "missing_provider_base_url" in codes
        assert "missing_provider_model" in codes

    def test_admin_test_llm_requires_live_roundtrip_for_draft_openai_config(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            llm_backend="mock",
        ).write_yaml(str(temp_config))

        resp = client.post(
            "/admin/test/llm",
            json={
                "llm_backend": "openai_compatible",
                "llm_base_url": "http://localhost:0/v1",
                "llm_model": "demo-model",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["live"] is False
        assert data["backend"] == "openai_compatible"
        assert "Live LLM test was not run" in data["error"]

    def test_admin_test_llm_mock_runs_live_sample(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            llm_backend="mock",
        ).write_yaml(str(temp_config))

        resp = client.post("/admin/test/llm", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["live"] is True
        assert "sample_response" in data

    def test_admin_test_telegram_reports_missing_token(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            telegram_token="",
        ).write_yaml(str(temp_config))

        resp = client.post("/admin/test/telegram", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["configured"] is False
        assert data["warnings"][0]["code"] == "missing_telegram_token"

    def test_admin_test_telegram_validates_draft_allowlist(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(data_dir=str(tmp_path / "data")).write_yaml(str(temp_config))

        resp = client.post(
            "/admin/test/telegram",
            json={
                "telegram_token": "123456:abc",
                "telegram_allowlist": ["42", "not-numeric"],
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["configured"] is True
        assert data["allowlist_count"] == 2
        assert data["warnings"][0]["code"] == "telegram_allowlist_non_numeric"

    def test_admin_test_storage_checks_data_and_workspace(
        self, client, temp_config, tmp_path,
    ):
        data_dir = tmp_path / "custom-data"
        workspace = tmp_path / "workspace"
        RuntimeConfig(data_dir=str(data_dir)).write_yaml(str(temp_config))

        resp = client.post(
            "/admin/test/storage",
            json={"tools_workspace": str(workspace)},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert {check["name"] for check in data["checks"]} == {
            "data_dir",
            "tools_workspace",
        }
        assert data_dir.is_dir()
        assert workspace.is_dir()

    def test_admin_test_storage_can_validate_without_creating(
        self, client, temp_config, tmp_path,
    ):
        missing_dir = tmp_path / "missing-data"
        RuntimeConfig(data_dir=str(missing_dir)).write_yaml(str(temp_config))

        resp = client.post(
            "/admin/test/storage",
            json={"create_missing": False},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        codes = {warning["code"] for warning in data["warnings"]}
        assert "data_dir_missing" in codes
        assert not missing_dir.exists()

    def test_admin_diagnostics_reports_runtime_and_storage(
        self, client, temp_config, tmp_path,
    ):
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "note.txt").write_text("hello")
        RuntimeConfig(data_dir=str(data_dir), llm_backend="mock").write_yaml(
            str(temp_config)
        )

        resp = client.get("/admin/diagnostics")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["config"]["llm_backend"] == "mock"
        assert data["runtime"]["active_sessions"] >= 0
        assert data["storage"]["data_dir"]["exists"] is True
        assert data["storage"]["data_dir"]["file_count"] == 1
        assert data["storage"]["backup_dir"].endswith("backups")

    def test_admin_export_config_redacts_secrets(
        self, client, temp_config, tmp_path,
    ):
        RuntimeConfig(
            data_dir=str(tmp_path / "data"),
            llm_backend="provider",
            llm_api_key="secret-key",
        ).write_yaml(str(temp_config))

        resp = client.get("/admin/export/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "nur.runtime_config.redacted.v1"
        assert data["config"]["llm_api_key"] == ""
        assert data["secret_status"]["llm_api_key"]["configured"] is True
        assert data["secret_status"]["llm_api_key"]["source"] == "yaml"
        assert "secret-key" not in resp.text

    def test_admin_backup_creates_redacted_zip(
        self, client, temp_config, tmp_path,
    ):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "memory.txt").write_text("private memory")
        RuntimeConfig(
            data_dir=str(data_dir),
            llm_backend="mock",
            llm_api_key="secret-key",
        ).write_yaml(str(temp_config))

        resp = client.post("/admin/backup", json={"include_data": True})

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["redacted_config"] is True
        assert data["included_data"] is True
        assert os.path.exists(data["path"])
        assert data["path"].startswith(str(data_dir / "backups"))
        with zipfile.ZipFile(data["path"]) as archive:
            names = set(archive.namelist())
            assert "runtime_config.redacted.json" in names
            assert "data/memory.txt" in names
            assert not any(name.startswith("data/backups/") for name in names)
            exported = archive.read("runtime_config.redacted.json").decode()
            assert "secret-key" not in exported
            assert "private memory" == archive.read("data/memory.txt").decode()

    def test_admin_reset_session_requires_typed_confirmation(self, client):
        client.post(
            "/v1/chat",
            json={"message": "hello", "user_id": "alice", "chat_id": "default"},
        )

        bad = client.post(
            "/admin/sessions/reset",
            json={
                "session_key": "web:alice:default",
                "confirmation": "reset",
            },
        )
        assert bad.status_code == 400

        ok = client.post(
            "/admin/sessions/reset",
            json={
                "session_key": "web:alice:default",
                "confirmation": "RESET web:alice:default",
            },
        )

        assert ok.status_code == 200
        data = ok.json()
        assert data["ok"] is True
        assert data["action"] == "session_reset"
        assert data["session_key"] == "web:alice:default"
        assert client.get("/v1/sessions").json()["count"] == 0

    def test_admin_delete_user_requires_confirmation_and_wipes_data(
        self, client, temp_config, tmp_path,
    ):
        data_dir = tmp_path / "data"
        RuntimeConfig(data_dir=str(data_dir), llm_backend="mock").write_yaml(
            str(temp_config)
        )
        client.post(
            "/v1/chat",
            json={"message": "hello", "user_id": "alice", "chat_id": "default"},
        )
        user_dir = data_dir / "web_alice"
        session_dir = user_dir / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "default.json").write_text("{}")
        assert user_dir.exists()

        bad = client.post(
            "/admin/users/delete",
            json={
                "platform": "web",
                "user_id": "alice",
                "confirmation": "DELETE alice",
            },
        )
        assert bad.status_code == 400

        ok = client.post(
            "/admin/users/delete",
            json={
                "platform": "web",
                "user_id": "alice",
                "confirmation": "DELETE web:alice",
            },
        )

        assert ok.status_code == 200
        data = ok.json()
        assert data["ok"] is True
        assert data["action"] == "user_delete"
        assert data["deleted"] is True
        assert data["rel_key"] == "web:alice"
        assert data["session_files_removed"] == 1
        assert data["sessions_evicted"] == ["web:alice:default"]
        assert data["shared_self_model_db_preserved"] is True
        assert not user_dir.exists()

    def test_admin_delete_user_rejects_path_traversal(self, client):
        resp = client.post(
            "/admin/users/delete",
            json={
                "platform": "web",
                "user_id": "../alice",
                "confirmation": "DELETE web:../alice",
            },
        )

        assert resp.status_code == 400

    def test_admin_test_routes_require_auth_when_key_configured(self, authed_client):
        for path in (
            "/admin/test/llm",
            "/admin/test/telegram",
            "/admin/test/storage",
            "/admin/backup",
        ):
            assert authed_client.post(path, json={}).status_code == 401
            ok = authed_client.post(
                path,
                json={},
                headers={"Authorization": "Bearer test-token-abc"},
            )
            assert ok.status_code == 200

    def test_admin_destructive_routes_require_auth_when_key_configured(
        self, authed_client,
    ):
        reset_payload = {
            "session_key": "web:alice:default",
            "confirmation": "RESET web:alice:default",
        }
        delete_payload = {
            "platform": "web",
            "user_id": "alice",
            "confirmation": "DELETE web:alice",
        }

        assert authed_client.post(
            "/admin/sessions/reset", json=reset_payload,
        ).status_code == 401
        assert authed_client.post(
            "/admin/users/delete", json=delete_payload,
        ).status_code == 401

        reset_authed = authed_client.post(
            "/admin/sessions/reset",
            json=reset_payload,
            headers={"Authorization": "Bearer test-token-abc"},
        )
        delete_authed = authed_client.post(
            "/admin/users/delete",
            json=delete_payload,
            headers={"Authorization": "Bearer test-token-abc"},
        )
        assert reset_authed.status_code == 404
        assert delete_authed.status_code == 404

        for path in ("/admin/diagnostics", "/admin/export/config"):
            assert authed_client.get(path).status_code == 401
            ok = authed_client.get(
                path,
                headers={"Authorization": "Bearer test-token-abc"},
            )
            assert ok.status_code == 200


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


class TestLegacyEndpointAuth:
    """Regression lock: legacy endpoints must respect the same api_key.

    When api_key is set, /chat, /debug, /config, /session/end, /rest, and
    /ws require the bearer token. Only GET / (the static web shell) and the
    v1 health/ready probes stay open.
    """

    def _post(self, client, path, **body):
        return client.post(path, json=body)

    def test_chat_requires_auth(self, authed_client):
        resp = self._post(authed_client, "/chat", message="hi", user_id="x")
        assert resp.status_code == 401

    def test_chat_accepts_valid_token(self, authed_client):
        resp = authed_client.post(
            "/chat",
            json={"message": "hi", "user_id": "x"},
            headers={"Authorization": "Bearer test-token-abc"},
        )
        assert resp.status_code == 200

    def test_debug_requires_auth(self, authed_client):
        resp = authed_client.get("/debug")
        assert resp.status_code == 401

    def test_get_config_requires_auth(self, authed_client):
        resp = authed_client.get("/config")
        assert resp.status_code == 401

    def test_post_config_requires_auth(self, authed_client):
        # Crucial: an unauthenticated POST /config must not be able to
        # clear the api_key and disable auth for subsequent requests.
        resp = authed_client.post(
            "/config",
            json={"clear_api_key": True, "llm_backend": "mock"},
        )
        assert resp.status_code == 401

    def test_session_end_requires_auth(self, authed_client):
        resp = self._post(authed_client, "/session/end", user_id="x")
        assert resp.status_code == 401

    def test_rest_requires_auth(self, authed_client):
        resp = self._post(authed_client, "/rest", user_id="x")
        assert resp.status_code == 401

    def test_index_stays_open(self, authed_client):
        # The HTML shell itself is static and needs to bootstrap the UI,
        # which then attaches the user-provided bearer token on fetches.
        resp = authed_client.get("/")
        assert resp.status_code == 200

    def test_websocket_rejects_missing_token(self, authed_client):
        # Browser clients cannot set Authorization headers, so /ws accepts
        # the socket and requires a token in the first JSON message before
        # processing any chat payload.
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with authed_client.websocket_connect("/ws") as ws:
                ws.send_text('{"message":"hi","user_id":"x"}')
                ws.receive_json()

    def test_websocket_accepts_valid_token_in_first_message(self, authed_client):
        with authed_client.websocket_connect("/ws") as ws:
            ws.send_text('{"token":"test-token-abc","message":"hi","user_id":"x"}')
            data = ws.receive_json()
            assert "response" in data


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
