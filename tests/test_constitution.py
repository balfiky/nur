"""Sprint 3 constitution layer tests.

Covers store-level get/set roundtrip, persistence across reopens,
inclusion in prompt_context, rendering in the life-history section,
and admin GET/PUT endpoints.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import interface.api as interface_api
from interface.api import app, set_pipeline, set_session_manager
from runtime.config import RuntimeConfig
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
    return TestClient(app)


# ---------------------------------------------------------------------------
# Store-level
# ---------------------------------------------------------------------------

def test_get_constitution_defaults_to_empty_string(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        result = store.get_constitution()
    assert result == {"constitution": "", "updated_at": result["updated_at"]}
    assert isinstance(result["updated_at"], float)


def test_set_constitution_then_get_roundtrips(tmp_path):
    config = _config(tmp_path)
    text = "Care about software architecture and creative writing. Prefer simplicity."
    with LifeHistoryStore(config) as store:
        result = store.set_constitution(text)
        assert result["constitution"] == text
        readback = store.get_constitution()
    assert readback["constitution"] == text
    assert readback["updated_at"] >= result["updated_at"] - 0.01


def test_constitution_persists_across_store_reopens(tmp_path):
    config = _config(tmp_path)
    text = "Stable orientation across sessions."
    with LifeHistoryStore(config) as store:
        store.set_constitution(text)
    with LifeHistoryStore(config) as store2:
        readback = store2.get_constitution()
    assert readback["constitution"] == text


def test_set_constitution_clamps_to_max_chars(tmp_path):
    config = _config(tmp_path)
    text = "x" * 5000
    with LifeHistoryStore(config) as store:
        result = store.set_constitution(text)
    assert len(result["constitution"]) <= 2000


def test_set_constitution_strips_whitespace(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        result = store.set_constitution("   Trimmed value.   \n")
    assert result["constitution"] == "Trimmed value."


# ---------------------------------------------------------------------------
# prompt_context integration
# ---------------------------------------------------------------------------

def test_prompt_context_includes_constitution(tmp_path):
    config = _config(tmp_path)
    text = "Stable orientation under test."
    with LifeHistoryStore(config) as store:
        store.set_constitution(text)
        ctx = store.prompt_context()
    assert ctx["constitution"] == text


def test_prompt_context_returns_empty_constitution_when_unset(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        ctx = store.prompt_context()
    assert ctx["constitution"] == ""


# ---------------------------------------------------------------------------
# Generator section rendering
# ---------------------------------------------------------------------------

def test_life_history_section_renders_constitution_above_beliefs():
    from core.dual_process.generator import _build_life_history_section

    class Stub:
        life_history_context = {
            "constitution": "Stable orientation about architecture.",
            "beliefs": [
                {"key": "autonomy", "statement": "I value autonomy.", "confidence": 0.7},
            ],
            "drives": [],
            "recent_evolution": [],
        }
        skill_context = {}

    rendered = _build_life_history_section(Stub())
    assert "## Life History / Evolving Worldview" in rendered
    assert "Stable orientation (operator-set):" in rendered
    assert "Stable orientation about architecture." in rendered
    # Constitution must appear before the beliefs section
    constitution_idx = rendered.index("Stable orientation about architecture.")
    beliefs_idx = rendered.index("Current beliefs:")
    assert constitution_idx < beliefs_idx


def test_life_history_section_renders_when_only_constitution_set():
    """A constitution alone should produce the section; empty beliefs/drives
    must not collapse it to empty."""
    from core.dual_process.generator import _build_life_history_section

    class Stub:
        life_history_context = {
            "constitution": "Care about clarity.",
            "beliefs": [],
            "drives": [],
            "recent_evolution": [],
        }
        skill_context = {}

    rendered = _build_life_history_section(Stub())
    assert "## Life History / Evolving Worldview" in rendered
    assert "Care about clarity." in rendered


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

def test_admin_get_constitution_returns_empty_initially(monkeypatch, tmp_path):
    client = _http_client(monkeypatch, tmp_path)
    with client:
        resp = client.get("/admin/identity/constitution")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["constitution"] == ""
    set_pipeline(None)
    set_session_manager(None)


def test_admin_put_constitution_sets_value(monkeypatch, tmp_path):
    client = _http_client(monkeypatch, tmp_path)
    with client:
        resp = client.put(
            "/admin/identity/constitution",
            json={"constitution": "Stable orientation set via admin."},
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["ok"] is True
        assert payload["constitution"] == "Stable orientation set via admin."

        readback = client.get("/admin/identity/constitution")
        assert readback.json()["constitution"] == "Stable orientation set via admin."
    set_pipeline(None)
    set_session_manager(None)


def test_admin_put_constitution_rejects_oversized_input(monkeypatch, tmp_path):
    client = _http_client(monkeypatch, tmp_path)
    with client:
        resp = client.put(
            "/admin/identity/constitution",
            json={"constitution": "x" * 3000},
        )
        # Pydantic validates max_length=2000 → 422 Unprocessable Entity
        assert resp.status_code == 422
    set_pipeline(None)
    set_session_manager(None)
