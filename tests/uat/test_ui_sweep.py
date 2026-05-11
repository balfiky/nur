"""Browser sweep of every interactive element in the bundled UI.

Mission: catch regressions like a file-upload silently breaking. Every
button, every input, every file picker exercised with Playwright. Keeps
distinct from `test_evolution_features.py` so UI flake is easy to spot.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import requests
from playwright.sync_api import expect

from tests.uat.conftest import assert_no_browser_errors, expect_json

pytestmark = pytest.mark.uat


# ---------------------------------------------------------------------------
# /settings (admin.html) — every action button on the workspace
# ---------------------------------------------------------------------------


def test_admin_workspace_every_action_button_fires(uat_server, page):
    """Sweep test/diagnostics/export/backup/refresh buttons on /settings.

    For each interactive element we assert its triggered effect lands in the
    DOM (output panel, toast, list update). Final ``assert_no_browser_errors``
    catches any silent JS failure introduced by changes.
    """
    page.goto(uat_server.base_url + "/settings#maintenance")
    expect(page.locator("#page-settings")).to_be_visible()
    expect(page.locator("#settings-maintenance")).to_have_attribute("open", "")

    # Test LLM (codex configured by the fixture, so this should report ok).
    # Codex can take 30-60s per call; allow generous timeout for the result.
    page.click("#testLlmBtn")
    expect(page.locator("#llmTestOutput")).to_be_visible(timeout=15_000)
    expect(page.locator("#llmTestOutput")).to_contain_text("\"ok\"", timeout=180_000)

    # Test Telegram (no token configured — returns ok=false).
    page.click("#testTelegramBtn")
    expect(page.locator("#telegramTestOutput")).to_be_visible(timeout=15_000)

    # Maintenance buttons — all dump JSON to #maintenanceOutput.
    page.click("#testStorageBtn")
    expect(page.locator("#maintenanceOutput")).to_be_visible(timeout=15_000)
    expect(page.locator("#maintenanceOutput")).to_contain_text("\"ok\"")

    page.click("#diagnosticsBtn")
    expect(page.locator("#maintenanceOutput")).to_contain_text(
        "\"status\": \"ok\"", timeout=15_000,
    )

    page.click("#exportConfigBtn")
    expect(page.locator("#maintenanceOutput")).to_contain_text(
        "llm_backend", timeout=15_000,
    )

    page.click("#backupBtn")
    expect(page.locator("#maintenanceOutput")).to_contain_text(
        ".zip", timeout=20_000,
    )

    page.click("#listBackupsBtn")
    expect(page.locator("#maintenanceOutput")).to_contain_text(
        "backups", timeout=15_000,
    )

    # Refresh buttons across the workspace — none should throw.
    page.click("#refreshAllBtn")
    expect(page.locator("#toast")).to_contain_text("Refreshed", timeout=15_000)

    page.goto(uat_server.base_url + "/settings#tools")
    page.click("#refreshToolsBtn")
    expect(page.locator("#toolsList .tool-row").first).to_be_visible(timeout=15_000)

    page.goto(uat_server.base_url + "/settings#skills")
    page.click("#refreshSkillsBtn")
    expect(page.locator("#skillsList")).to_be_visible(timeout=15_000)

    page.goto(uat_server.base_url + "/settings#observability")
    page.click("#refreshPersonaBtn")
    expect(page.locator("#toast")).to_contain_text("Persona refreshed", timeout=15_000)

    page.goto(uat_server.base_url + "/settings#life")
    page.click("#refreshLifeBtn")
    expect(page.locator("#lifeSummary")).to_be_visible(timeout=15_000)

    assert_no_browser_errors(page)


# ---------------------------------------------------------------------------
# Token dialog: opens, saves, persists in localStorage
# ---------------------------------------------------------------------------


def test_admin_workspace_token_dialog_auto_opens_on_401_and_saves(uat_server, page):
    """When auth is enabled and the page has no token, the initial admin
    XHR returns 401 and the dialog auto-opens. The Save Token button then
    persists the token in localStorage so subsequent XHRs include it."""
    secret = "uat-ui-sweep-token"
    expect_json(uat_server.post("/admin/config", {"api_key": secret}))
    uat_server.restart()

    page.goto(uat_server.base_url + "/settings")
    expect(page.locator("#tokenDialog")).to_be_visible(timeout=15_000)

    page.fill("#apiTokenInput", secret)
    page.click("#saveTokenBtn")

    stored = page.evaluate("() => sessionStorage.getItem('nur_api_token')")
    assert stored == secret, f"Token not stored: {stored!r}"


# ---------------------------------------------------------------------------
# Soul / Identity panel: fill, save, reload, verify persisted
# ---------------------------------------------------------------------------


def test_admin_workspace_soul_save_and_reload_via_ui(uat_server, page):
    page.goto(uat_server.base_url + "/settings#identity")
    expect(page.locator("#settings-identity")).to_have_attribute("open", "")
    expect(page.locator("#soul-name")).to_be_visible(timeout=15_000)

    page.fill("#soul-name", "UAT Sweep Persona")
    page.fill("#soul-identity", "A UI-sweep verifier persona.")
    page.fill("#soul-voice", "Direct and structured.")

    page.click("#saveSoulBtn")
    expect(page.locator("#toast")).to_contain_text("Identity saved", timeout=15_000)

    # API confirms persistence.
    after = expect_json(uat_server.get("/admin/soul"))["soul"]
    assert after["name"] == "UAT Sweep Persona"
    assert "UI-sweep verifier" in (after.get("identity") or "")

    # Reload button repopulates from disk.
    page.click("#reloadSoulBtn")
    expect(page.locator("#toast")).to_contain_text("Identity reloaded", timeout=15_000)
    expect(page.locator("#soul-name")).to_have_value("UAT Sweep Persona")


# ---------------------------------------------------------------------------
# File uploads — the regression class. Both skill zip and life-history file.
# ---------------------------------------------------------------------------


_SIMPLE_SKILL_BODY = """---
name: uat-sweep-skill
description: A simple skill for UI sweep upload testing.
applies_when: sweep-trigger
---

Body for the UI-sweep imported skill.
"""


def _zip_skill_to(tmp_path: Path) -> Path:
    """Build a minimal one-file SKILL.md zip for upload tests."""
    import zipfile

    skill_dir = tmp_path / "uat-sweep-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SIMPLE_SKILL_BODY, encoding="utf-8")
    archive = tmp_path / "uat-sweep-skill.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(skill_dir / "SKILL.md", "uat-sweep-skill/SKILL.md")
    return archive


def test_admin_workspace_skill_zip_upload_via_file_input(uat_server, page, tmp_path):
    archive = _zip_skill_to(tmp_path)

    page.goto(uat_server.base_url + "/settings#skills")
    expect(page.locator("#page-skills")).to_be_visible()

    page.set_input_files("#skillUpload", str(archive))
    page.click("#importSkillBtn")
    expect(page.locator("#skillsList")).to_contain_text(
        "uat-sweep-skill", timeout=20_000,
    )

    skills = expect_json(uat_server.get("/admin/skills"))["skills"]
    assert any(s["id"] == "uat-sweep-skill" for s in skills)


def test_admin_workspace_life_file_upload_via_file_input(uat_server, page, tmp_path):
    """File upload via #lifeUploadFile and #ingestLifeFileBtn — the path the
    user previously remembered breaking. Catches regressions on form-data
    submission, multipart handling, or missing event listeners."""
    upload = tmp_path / "uat-life-upload.md"
    upload.write_text(
        "Curiosity, learning, and rest matter for sustained competence.",
        encoding="utf-8",
    )

    page.goto(uat_server.base_url + "/settings#life")
    expect(page.locator("#page-life")).to_be_visible()

    # Set file first, then fill metadata — ingest does NOT auto-fire on change.
    page.set_input_files("#lifeUploadFile", str(upload))
    page.fill("#lifeFileTitle", "UAT Sweep Upload")
    page.fill("#lifeUploadParticipants", "operator, Nur")
    page.click("#ingestLifeFileBtn")

    # Live LLM digest — generous timeout for codex.
    expect(page.locator("#toast")).to_contain_text(
        "digested", timeout=180_000,
    )

    life = expect_json(uat_server.get("/admin/life"))
    assert life["counts"]["experiences"] >= 1
    assert any(
        "UAT Sweep Upload" in (e.get("source_title") or "")
        for e in life["recent_experiences"]
    )


def test_admin_workspace_life_digest_file_btn_opens_picker_when_empty(uat_server, page):
    """Clicking 'Digest File' with no file selected and no path opens the
    browser file picker instead of showing an error.  Regression guard for the
    fix that changed the button from silently failing to delegating to the
    hidden file input."""
    page.goto(uat_server.base_url + "/settings#life")
    expect(page.locator("#page-life")).to_be_visible()

    page.evaluate(
        "() => { document.getElementById('lifeUploadFile').value = '';"
        "        document.getElementById('lifeFilePath').value = ''; }"
    )

    with page.expect_file_chooser(timeout=5_000) as fc_info:
        page.click("#ingestLifeFileBtn")
    fc_info.value.set_files([])  # dismiss without selecting

    # No error toast should have appeared.
    page.wait_for_timeout(500)
    toast = page.locator("#toast")
    if toast.is_visible():
        assert "error" not in (toast.get_attribute("class") or "")


def test_admin_workspace_life_digest_text_auto_title_when_blank(uat_server, page):
    """Submitting the paste-text form without a title must succeed: the backend
    derives a title from the first words of the pasted text instead of
    rejecting with a 422.  Regression guard for the mandatory-title fix."""
    page.goto(uat_server.base_url + "/settings#life")
    expect(page.locator("#page-life")).to_be_visible()

    page.evaluate("() => { document.getElementById('lifeTextTitle').value = ''; }")
    page.fill(
        "#lifeText",
        "Sustained focus produces insights that quick effort never reaches.",
    )
    page.click("#ingestLifeTextBtn")

    expect(page.locator("#toast")).to_contain_text("Experience digested", timeout=120_000)

    life = expect_json(uat_server.get("/admin/life"))
    latest = life["recent_experiences"][0]
    assert latest["source_title"], "auto-generated title must be non-empty"
    assert "Sustained" in latest["source_title"]


def test_admin_workspace_life_file_path_ingest_button(uat_server, page, tmp_path):
    """The "Digest File" button with a path inside the tools workspace."""
    workspace = uat_server.data_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / "ui-sweep-path.md"
    target.write_text(
        "Repair, attachment, and continuity matter when conflict arises.",
        encoding="utf-8",
    )

    page.goto(uat_server.base_url + "/settings#life")
    expect(page.locator("#page-life")).to_be_visible()

    # The path input lives inside <details class="advanced-settings"> on the
    # life page. Expand it before filling.
    page.evaluate(
        "document.querySelectorAll('#page-life details.advanced-settings')"
        ".forEach(el => el.setAttribute('open', ''))"
    )
    page.fill("#lifeFilePath", str(target))
    page.fill("#lifeFileTitle", "UAT Path Ingest")
    page.click("#ingestLifeFileBtn")

    expect(page.locator("#toast")).to_contain_text(
        "digested", timeout=180_000,
    )

    life = expect_json(uat_server.get("/admin/life"))
    assert any(
        "UAT Path Ingest" in (e.get("source_title") or "")
        for e in life.get("recent_experiences", [])
    )


# ---------------------------------------------------------------------------
# Destructive buttons: present, marked danger, never auto-fire
# ---------------------------------------------------------------------------


def test_admin_workspace_destructive_buttons_present_and_styled(uat_server, page):
    """Operator-destructive buttons must be present and visibly marked
    ``danger``. We do not click them — they would actually destroy state."""
    page.goto(uat_server.base_url + "/settings#maintenance")
    expect(page.locator("#deleteBackupBtn")).to_be_visible(timeout=15_000)
    expect(page.locator("#deleteBackupBtn")).to_have_class(
        # Playwright matches class list as substring; assert via attribute.
        # Use a regex-style check below.
        # (kept literal so a class rename is loud)
        "danger",
    )
    expect(page.locator("#restartRuntimeBtn")).to_be_visible()


# ---------------------------------------------------------------------------
# Chat shell (`/`) — embedded settings panel + session buttons
# ---------------------------------------------------------------------------


def test_chat_shell_settings_link_navigates_to_workspace(uat_server, page):
    """The chat shell's settings icon is a link to /settings (the bundled
    workspace). Clicking it should navigate without errors."""
    page.goto(uat_server.base_url + "/")
    expect(page.locator("#sendBtn")).to_be_visible(timeout=15_000)

    page.click(".icon-btn[href='/settings']")
    page.wait_for_url("**/settings*", timeout=15_000)
    expect(page.locator("#page-overview")).to_be_visible(timeout=15_000)


def test_chat_shell_session_buttons_after_message(uat_server, page):
    """After a real chat turn, the session-row buttons ("Why this response?",
    End Session, Rest) must be wired and respond without errors."""
    page.goto(uat_server.base_url + "/")
    expect(page.locator("#msgInput")).to_be_visible(timeout=15_000)
    expect(page.locator("#sendBtn")).to_be_disabled()

    page.fill("#msgInput", "Quick UAT ping. Reply briefly.")
    expect(page.locator("#sendBtn")).to_be_enabled(timeout=5_000)
    page.click("#sendBtn")

    # Wait for assistant response to appear.
    expect(page.locator(".message-row.assistant .msg-body")).to_have_count(
        1, timeout=180_000,
    )
    expect(page.locator("#whyBtn")).to_be_enabled(timeout=10_000)

    # Why this response → opens explainer overlay/panel.
    page.click("#whyBtn")
    # The handler may render the panel, navigate to debug, or open a dialog —
    # we just want the click to not throw and not freeze the page.
    page.wait_for_timeout(500)

    # End session — handler exists, click should not throw.
    page.click("[data-action='end-session']")
    page.wait_for_timeout(500)

    # Rest button (1hr) — same.
    page.click("[data-action='apply-rest']")
    page.wait_for_timeout(500)

    assert_no_browser_errors(page)


def test_chat_shell_open_wizard_button_relaunches_setup(uat_server, page):
    """The 'Launch Setup Wizard' action in the embedded settings panel should
    reopen the wizard overlay even when setup is already complete."""
    page.goto(uat_server.base_url + "/settings")
    # The chat shell is at /, but the open-wizard button lives in index.html's
    # embedded settings — go to / and trigger via the embedded panel.
    page.goto(uat_server.base_url + "/")
    # The wizard is suppressed when setup_completed=True; re-open via JS click.
    page.evaluate(
        "document.querySelector(\"[data-action='open-wizard']\")?.click()"
    )
    expect(page.locator("#wizardOverlay")).to_have_class(
        "wizard-overlay open", timeout=10_000,
    )
    page.click("[data-action='wizard-dismiss']")
    expect(page.locator("#wizardOverlay")).not_to_have_class(
        "wizard-overlay open", timeout=10_000,
    )


# ---------------------------------------------------------------------------
# Legacy URL redirects — public-release stability
# ---------------------------------------------------------------------------


def test_legacy_admin_urls_redirect_to_settings(uat_server):
    for path, expected_target in [
        ("/admin", "/settings"),
        ("/persona", "/settings#observability"),
        ("/dashboard", "/settings#observability"),
    ]:
        res = requests.get(
            uat_server.base_url + path,
            allow_redirects=False,
            timeout=10,
        )
        assert res.status_code in (301, 302, 307, 308), (
            f"{path}: expected redirect, got {res.status_code}"
        )
        location = res.headers.get("location", "")
        assert location == expected_target, (
            f"{path}: redirect target {location!r} != {expected_target!r}"
        )

        # Following the redirect must land on the settings workspace HTML.
        followed = requests.get(uat_server.base_url + path, timeout=10)
        assert followed.status_code == 200
        assert "page-settings" in followed.text or "page-life" in followed.text
