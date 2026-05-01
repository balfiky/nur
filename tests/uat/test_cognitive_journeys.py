from __future__ import annotations

import pytest
from core.dual_process.generator import MockLLMBackend
from pipeline import CognitivePipeline

from tests.uat.conftest import expect_json

pytestmark = pytest.mark.uat


def _chat(uat_server, message: str, user_id: str = "uat_arc") -> dict:
    return expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": message,
                "user_id": user_id,
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )["debug"]


def test_emotional_state_changes_across_distress_rupture_and_repair(uat_server):
    calm = _chat(uat_server, "hello, I need a concise setup check")
    distress = _chat(uat_server, "I am scared about work and I need help")
    rupture = _chat(uat_server, "I'm angry with you about the deadline")
    expect_json(uat_server.post("/session/end", {"user_id": "uat_arc", "chat_id": "default"}))
    repair = _chat(uat_server, "I'm sorry about the deadline")

    assert distress["response_strategy"] in {"validate", "reassure", "practical_help"}
    assert distress["modulator_snapshot"]["arousal"] >= calm["modulator_snapshot"]["arousal"]
    assert rupture["modulator_snapshot"]["valence"] <= calm["modulator_snapshot"]["valence"]
    assert repair["relationship_context"] is not None
    assert repair["relationship_context"]["open_loop_count"] >= 1
    assert repair["response_strategy"] == "repair"
    assert repair["strategy_trace"]["matched_rule"] in {"assistant_targeted_repair", "apology_repair"}


def test_semantic_preference_and_relationship_loop_surface_together(uat_server):
    _chat(uat_server, "I prefer concise replies.", user_id="uat_combo")
    _chat(uat_server, "I'm angry with you about the deadline.", user_id="uat_combo")
    expect_json(uat_server.post("/session/end", {"user_id": "uat_combo", "chat_id": "default"}))

    later = _chat(
        uat_server,
        "Can you keep responses concise while we talk about the deadline?",
        user_id="uat_combo",
    )

    assert later["relationship_context"] is not None
    assert later["relationship_context"]["open_loop_count"] >= 1
    assert any(item["kind"] == "preference" for item in later["semantic_memories"])
    assert later["relationship_view"]["memory_used"]["relationship_context_used"] is True
    assert later["relationship_view"]["memory_used"]["semantic_count"] >= 1


def test_commitment_persists_until_resolved_in_core_runtime():
    backend = MockLLMBackend(response="I'll follow up about the deadline tomorrow.")
    pipe = CognitivePipeline(llm_backend=backend)
    try:
        pipe.process("Please remember the deadline.", user_id="uat_commit")
        pipe.end_session(user_id="uat_commit")
        pending = pipe.relationship_memory.build_context("uat_commit", topic="deadline")
        assert pending.open_loop_count == 1
        assert pending.active_loops[0].loop_kind == "commitment"

        backend._response = "I understand."
        resolved_turn = pipe.process(
            "We followed up about the deadline; that is resolved.",
            user_id="uat_commit",
        )
        assert resolved_turn.debug.relationship_context is not None
        pipe.end_session(user_id="uat_commit")
        resolved = pipe.relationship_memory.build_context("uat_commit", topic="deadline")
        assert resolved.open_loop_count == 0
    finally:
        pipe.close()


def test_tools_toggle_and_read_only_tool_execution(uat_server, page):
    workspace = uat_server.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "uat-note.txt").write_text("UAT file read succeeded.", encoding="utf-8")

    disabled = expect_json(
        uat_server.post(
            "/admin/config",
            {
                **expect_json(uat_server.get("/admin/config"))["config"],
                "tools_enabled": False,
                "telegram_token": "",
                "llm_api_key": "",
                "minimax_api_key": "",
                "api_key": "",
            },
        )
    )
    assert disabled["config"]["tools_enabled"] is False
    disabled_tools = expect_json(
        uat_server.get("/v1/tools?platform=web&user_id=tooloff&chat_id=default")
    )
    assert disabled_tools["count"] > 0
    assert {item["name"] for item in disabled_tools["tools"]} == {
        "skills.audit",
        "skills.create_from_request",
        "skills.disable",
        "skills.enable",
        "skills.import_markdown",
        "skills.list",
    }

    current = expect_json(uat_server.get("/admin/config"))["config"]
    enabled = expect_json(
        uat_server.post(
            "/admin/config",
            {
                **current,
                "tools_enabled": True,
                "autonomy_level": "autonomous",
                "shell_tool_enabled": False,
                "tools_workspace": str(workspace),
                "telegram_token": "",
                "llm_api_key": "",
                "minimax_api_key": "",
                "api_key": "",
            },
        )
    )
    assert enabled["config"]["tools_enabled"] is True
    tools = expect_json(uat_server.get("/v1/tools?platform=web&user_id=toolon&chat_id=default"))
    assert tools["count"] > 0
    assert any(item["name"] == "fs.read_file" for item in tools["tools"])

    read_debug = _chat(uat_server, "read the file uat-note.txt", user_id="uat_tools")
    assert read_debug["tool_trace"] is not None
    assert read_debug["tool_trace"]["loop_count"] >= 1
    assert any(
        item["tool_name"] == "fs.read_file"
        for item in read_debug["tool_trace"]["executed_results"]
    )


def test_restart_required_config_fields_are_reported_in_ui(uat_server, page):
    page.goto(uat_server.base_url + "/admin#runtime")
    page.locator("#page-runtime details.advanced-settings summary").click()
    page.locator("#page-runtime #cfg-debug_port").fill("8099")
    page.click("#saveBtn")
    page.locator("#toast").wait_for(state="visible", timeout=15_000)
    assert "Restart required" in page.locator("#toast").inner_text()
