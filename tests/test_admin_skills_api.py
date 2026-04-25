from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import interface.api as interface_api
from interface.api import app, set_pipeline, set_session_manager
from runtime.config import RuntimeConfig


SKILL_MD = """---
name: report-writer
description: Write files and search the web for report drafts.
---

Read files, write files, and search the web.
"""


@pytest.fixture
def client(monkeypatch, tmp_path):
    path = tmp_path / "runtime_config.yaml"
    RuntimeConfig(data_dir=str(tmp_path / "data")).write_yaml(str(path))
    monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))
    set_pipeline(None)
    set_session_manager(None)
    with TestClient(app) as c:
        yield c
    set_pipeline(None)
    set_session_manager(None)


def test_admin_skills_import_list_and_enable(client):
    empty = client.get("/admin/skills")
    assert empty.status_code == 200
    assert empty.json()["count"] == 0

    imported = client.post(
        "/admin/skills/import",
        json={"skill_markdown": SKILL_MD},
    )
    assert imported.status_code == 200
    skill = imported.json()["skill"]
    assert skill["id"] == "report-writer"
    assert skill["enabled"] is False
    assert "fs.write_file" in skill["compatibility"]["required_tools"]

    listing = client.get("/admin/skills").json()
    assert listing["count"] == 1
    assert listing["skills"][0]["status"] == "needs_review"

    enabled = client.post("/admin/skills/report-writer/enable")
    assert enabled.status_code == 200
    assert enabled.json()["skill"]["enabled"] is True

    audited = client.post("/admin/skills/report-writer/audit")
    assert audited.status_code == 200
    assert audited.json()["skill"]["status"] == "enabled"

    disabled = client.post("/admin/skills/report-writer/disable")
    assert disabled.status_code == 200
    assert disabled.json()["skill"]["status"] == "disabled"


def test_admin_skills_import_requires_one_source(client):
    resp = client.post("/admin/skills/import", json={})

    assert resp.status_code == 400
    assert "exactly one" in resp.json()["detail"]
