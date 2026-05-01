from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.uat.conftest import assert_no_browser_errors, expect_json

pytestmark = pytest.mark.uat


def test_persona_dashboard_reads_runtime_sessions_across_channels(uat_server, page):
    expect_json(uat_server.post(
        "/v1/chat",
        {
            "message": "I am worried about the release and need a steady plan.",
            "user_id": "uat_persona",
            "chat_id": "webtab",
        },
    ))

    page.goto(uat_server.base_url + "/persona")

    expect(page.locator("#dashboardStatus")).to_contain_text("Live", timeout=15_000)
    expect(page.locator("#sessionList")).to_contain_text("web:uat_persona:webtab")
    expect(page.locator("#emotionLabel")).not_to_be_empty()
    expect(page.locator("#modulatorGrid .mod-card")).to_have_count(6)
    expect(page.locator("#perceptionList")).to_contain_text("Summary")
    expect(page.locator("#relationshipList")).to_contain_text("Strategy")
    expect(page.locator("#lifeList")).to_contain_text("Context available")
    expect(page.locator("#memoryList")).to_contain_text("Relationship used")
    expect(page.locator("#skillsList")).to_contain_text("Enabled skills")

    assert_no_browser_errors(page)
