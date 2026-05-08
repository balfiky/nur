from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.uat.conftest import assert_no_browser_errors, expect_json

pytestmark = pytest.mark.uat


def test_first_run_wizard_configures_identity_life_and_allows_chat(uat_server_first_run, page):
    page.goto(uat_server_first_run.base_url + "/")
    expect(page.locator("#wizardOverlay")).to_have_class("wizard-overlay open", timeout=15_000)

    page.click("text=Get Started")
    page.click(".wizard-preset[data-preset='codex']")
    page.click("#wizPanel-2 button.primary")
    expect(page.locator("#wizPanel-3")).to_be_visible(timeout=15_000)

    page.click("#wizPanel-3 button.primary")
    expect(page.locator("#wizPanel-4")).to_be_visible(timeout=15_000)

    page.fill("#wiz-soul-name", "UAT Nur")
    page.select_option("#wiz-soul-archetype", "researcher")
    page.fill("#wiz-soul-notes", "Keep UAT answers concise and inspectable.")
    page.click("#wizPanel-4 button.primary")
    expect(page.locator("#wizPanel-5")).to_be_visible(timeout=15_000)

    page.fill("#wiz-life-title", "First UAT experience")
    page.fill("#wiz-life-participants", "operator, UAT Nur")
    page.fill(
        "#wiz-life-text",
        "Learning, curiosity, autonomy, and relationship repair are formative for this setup.",
    )
    page.click("#wizPanel-5 button.primary")
    # Life ingest calls the LLM — allow extra time for live backends
    expect(page.locator("#wizPanel-6")).to_be_visible(timeout=120_000)
    expect(page.locator("#wiz-summary")).to_contain_text("First experience")

    page.click("#wizPanel-6 button.primary")
    expect(page.locator("#wizardOverlay")).not_to_have_class("wizard-overlay open", timeout=15_000)

    setup = expect_json(uat_server_first_run.get("/admin/config"))["setup"]
    assert setup["completed"] is True
    life = expect_json(uat_server_first_run.get("/admin/life"))
    assert life["counts"]["experiences"] >= 1

    page.fill("#msgInput", "hello after setup")
    page.click("#sendBtn")
    expect(page.locator(".message-row.assistant .msg-body")).to_have_count(1, timeout=90_000)

    assert_no_browser_errors(page)
