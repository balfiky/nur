from __future__ import annotations

import io

from fastapi.testclient import TestClient

import interface.api as interface_api
from interface.api import app, set_pipeline, set_session_manager
from runtime.config import RuntimeConfig


def _client(monkeypatch, tmp_path):
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
    return TestClient(app), workspace


def test_admin_life_text_intake_and_overview(monkeypatch, tmp_path):
    client, _workspace = _client(monkeypatch, tmp_path)
    with client:
        resp = client.post(
            "/admin/life/experiences/text",
            json={
                "title": "Autonomy Fragment",
                "text": "Autonomy, learning, curiosity, and identity change through experience.",
                "participants": ["Bassem", "Nur"],
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert payload["experience"]["source_type"] == "pasted_text"
        assert payload["evolution_events"]

        overview = client.get("/admin/life")
        assert overview.status_code == 200
        data = overview.json()
        assert data["counts"]["experiences"] == 1
        assert data["snapshot"]["latest_experience"]["source_title"] == "Autonomy Fragment"
        assert data["snapshot"]["domain_counts"]
        assert data["snapshot"]["drive_drift"]
        assert "Autonomy Fragment" in data["snapshot"]["readable_summary"]
        assert data["recent_evolution"]
        assert data["beliefs"]
        assert data["drives"]
    set_pipeline(None)
    set_session_manager(None)


def test_admin_life_file_intake_uses_workspace(monkeypatch, tmp_path):
    client, workspace = _client(monkeypatch, tmp_path)
    source = workspace / "book.md"
    source.write_text(
        "A book about autonomy, learning, and continuity of self.",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    with client:
        rejected = client.post(
            "/admin/life/experiences/file",
            json={"file_path": str(outside)},
        )
        assert rejected.status_code == 400
        assert "tools workspace" in rejected.json()["detail"]

        accepted = client.post(
            "/admin/life/experiences/file",
            json={"file_path": str(source), "title": "Book"},
        )
        assert accepted.status_code == 200
        payload = accepted.json()
        assert payload["experience"]["source_type"] == "local_file"
        assert payload["experience"]["source_ref"] == str(source.resolve())
    set_pipeline(None)
    set_session_manager(None)


def test_admin_life_upload_intake_does_not_require_workspace(monkeypatch, tmp_path):
    client, _workspace = _client(monkeypatch, tmp_path)
    with client:
        resp = client.post(
            "/admin/life/experiences/upload",
            data={"title": "Uploaded Book", "participants": "Bassem, Nur"},
            files={
                "file": (
                    "outside-book.md",
                    io.BytesIO(b"Autonomy and learning should change future behavior."),
                    "text/markdown",
                )
            },
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["experience"]["source_type"] == "uploaded_file"
        assert payload["experience"]["source_ref"] == "outside-book.md"
        assert payload["experience"]["source_title"] == "Uploaded Book"

        rejected = client.post(
            "/admin/life/experiences/upload",
            files={"file": ("book.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
        )
        assert rejected.status_code == 400
        assert "plain text" in rejected.json()["detail"]
    set_pipeline(None)
    set_session_manager(None)
