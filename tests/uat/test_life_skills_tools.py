from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.uat.conftest import (
    assert_no_browser_errors,
    expect_json,
    make_claude_style_skill,
    zip_directory,
)

pytestmark = pytest.mark.uat


SKILL_MD = """---
name: uat-note-keeper
description: Keep concise implementation notes during UAT.
---

Use this skill when the operator asks for a compact implementation note.
Return three bullets: context, action, and verification.
"""


def test_admin_skills_import_enable_and_tools_inventory(uat_server, page):
    page.goto(uat_server.base_url + "/admin#skills")
    expect(page.locator("#page-skills")).to_be_visible()

    page.fill("#skillNameHint", "uat-note-keeper")
    page.locator("#page-skills details.advanced-settings summary").click()
    page.fill("#skillMarkdown", SKILL_MD)
    page.click("#importSkillBtn")
    expect(page.locator("#skillsList")).to_contain_text("uat-note-keeper", timeout=15_000)
    expect(page.locator("#skillsList")).to_contain_text("needs review")

    page.locator("[data-skill-action='enable']").first.click()
    expect(page.locator("#skillsList")).to_contain_text("enabled", timeout=15_000)

    skills_payload = expect_json(uat_server.get("/admin/skills"))
    assert any(item["id"] == "uat-note-keeper" and item["enabled"] for item in skills_payload["skills"])

    page.goto(uat_server.base_url + "/admin#tools")
    expect(page.locator("#page-settings")).to_be_visible()
    expect(page.locator("#settings-tools")).to_have_attribute("open", "")
    expect(page.locator("#toolsSummary")).to_contain_text("Total", timeout=15_000)
    expect(page.locator("#toolsList .tool-row").first).to_be_visible()

    assert_no_browser_errors(page)


def test_claude_style_skill_folder_zip_and_enabled_context(uat_server, page, tmp_path):
    source = make_claude_style_skill(tmp_path)

    path_import = expect_json(
        uat_server.post(
            "/admin/skills/import",
            {"source_path": str(source), "name_hint": "claude-video-helper"},
        )
    )["skill"]
    assert path_import["id"] == "claude-video-helper"
    assert path_import["status"] == "needs_review"
    assert path_import["compatibility"]["files"]["script_count"] == 1
    assert path_import["compatibility"]["files"]["resource_count"] == 1
    assert "bundled_scripts" in path_import["compatibility"]["risk_flags"]
    assert "web.fetch" in path_import["compatibility"]["required_tools"]

    zip_path = zip_directory(source, tmp_path / "claude-video-helper.zip")
    page.goto(uat_server.base_url + "/admin#skills")
    expect(page.locator("#page-skills")).to_be_visible()
    page.set_input_files("#skillUpload", str(zip_path))
    page.click("#importSkillBtn")
    expect(page.locator("#skillsList")).to_contain_text("claude-video-helper", timeout=15_000)

    skills = expect_json(uat_server.get("/admin/skills"))["skills"]
    zip_record = sorted(
        [item for item in skills if item["name"] == "claude-video-helper"],
        key=lambda item: item["installed_at"],
    )[-1]
    expect_json(uat_server.post(f"/admin/skills/{zip_record['id']}/enable"))

    turn = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "Use the video helper skill to outline the download steps.",
                "user_id": "uat_skill_context",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    skill_context = turn["debug"]["skill_context"]
    assert skill_context["count"] >= 1
    assert any(item["id"] == zip_record["id"] for item in skill_context["skills"])

    assert_no_browser_errors(page)


def test_conversation_cannot_fake_permanent_skill_creation(tmp_path):
    from core.dual_process.generator import MockLLMBackend
    from pipeline import CognitivePipeline

    backend = MockLLMBackend(
        response=(
            "Added. The requested capability is now part of my durable "
            "runtime context. Send the link."
        )
    )
    pipe = CognitivePipeline(llm_backend=backend)
    try:
        result = pipe.process("Make it a permanent skill for yourself first.", user_id="uat")
        assert "I did not create, import, or enable" in result.response
        assert "No skill-registry Tool Execution Result ran" in result.response
        assert any("Unverified external-action claim" in item for item in result.debug.self_check_issues)
    finally:
        pipe.close()


def test_admin_life_history_ingest_is_visible_in_ui_and_api(uat_server, page):
    page.goto(uat_server.base_url + "/admin#life")
    expect(page.locator("#page-life")).to_be_visible()

    page.fill("#lifeTextTitle", "UAT formative note")
    page.fill("#lifeSourceType", "admin_pasted_text")
    page.fill("#lifeParticipants", "operator, Nur")
    page.fill(
        "#lifeText",
        (
            "Autonomy should be treated as self-directed interpretation. "
            "Learning through practice increases competence. Curiosity asks "
            "better questions, and relationship repair preserves continuity."
        ),
    )
    page.click("#ingestLifeTextBtn")
    expect(page.locator("#toast")).to_contain_text("Experience digested", timeout=20_000)
    expect(page.locator("#lifeExperiences")).to_contain_text("UAT formative note", timeout=20_000)
    expect(page.locator("#lifeTimeline")).not_to_contain_text("No evolution events yet", timeout=20_000)

    life_payload = expect_json(uat_server.get("/admin/life"))
    assert life_payload["counts"]["experiences"] >= 1
    assert life_payload["counts"]["evolution_events"] >= 1
    assert any(item["domain"] == "drive" for item in life_payload["recent_evolution"])

    turn = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "What changed in your worldview after that formative note?",
                "user_id": "uat_life_context",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    debug = turn["debug"]
    assert debug["life_history_context"]["all_drives"]
    assert debug["life_history_context"]["recent_evolution"]
    assert debug["life_influence"]["competence_pressure"] > 0
    assert debug["relationship_view"]["life_influence"]["curiosity_pressure"] > 0

    assert_no_browser_errors(page)


def test_life_upload_local_file_and_rollback_paths(uat_server, tmp_path):
    upload_path = tmp_path / "uploaded-life.md"
    upload_path.write_text(
        "Curiosity, learning, and autonomy should shape future interpretation.",
        encoding="utf-8",
    )
    with upload_path.open("rb") as handle:
        uploaded = uat_server.post(
            "/admin/life/experiences/upload",
            files={"file": ("uploaded-life.md", handle, "text/markdown")},
            data={"title": "Uploaded Life UAT", "participants": "operator, Nur"},
        )
    upload_payload = expect_json(uploaded)
    assert upload_payload["experience"]["source_title"] == "Uploaded Life UAT"

    workspace = uat_server.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    local_file = workspace / "workspace-note.md"
    local_file.write_text(
        "Relationship repair and continuity should be remembered after conflict.",
        encoding="utf-8",
    )
    local_payload = expect_json(
        uat_server.post(
            "/admin/life/experiences/file",
            {
                "file_path": str(local_file),
                "title": "Local Life UAT",
                "participants": ["operator", "Nur"],
            },
        )
    )
    batch_id = local_payload["policy"]["batch_id"]
    assert local_payload["evolution_events"]

    after_ingest = expect_json(uat_server.get("/admin/life"))
    assert after_ingest["counts"]["experiences"] >= 2

    rollback = uat_server.post("/admin/life/rollback", {"batch_id": batch_id})
    assert rollback.status_code == 410
    assert "rollback was removed" in rollback.json()["detail"]


def test_life_digest_changes_later_policy_signals(uat_server):
    expect_json(
        uat_server.post(
            "/admin/life/experiences/text",
            {
                "title": "Repair drive seed",
                "source_type": "admin_pasted_text",
                "participants": ["operator", "Nur"],
                "text": (
                    "Relationship repair, trust, apology, and continuity matter. "
                    "A rupture should be revisited carefully until it is resolved."
                ),
            },
        )
    )
    for _ in range(5):
        expect_json(
            uat_server.post(
                "/v1/chat",
                {
                    "message": "Thank you, that helped.",
                    "user_id": "uat_life_policy",
                    "chat_id": "default",
                    "include_debug": True,
                },
            )
        )
    expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "I'm angry with you about the deadline.",
                "user_id": "uat_life_policy",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    expect_json(uat_server.post("/session/end", {"user_id": "uat_life_policy", "chat_id": "default"}))

    later = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "The deadline still matters.",
                "user_id": "uat_life_policy",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    debug = later["debug"]
    assert debug["life_influence"]["repair_pressure"] > 0
    assert debug["relationship_context"]["open_loop_count"] >= 1
    assert debug["life_influence_effects"]
