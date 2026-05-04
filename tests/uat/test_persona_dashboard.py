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

    page.goto(uat_server.base_url + "/settings#observability")

    expect(page.locator("#page-observability")).to_be_visible(timeout=15_000)
    expect(page.locator("#personaAdminPage")).to_contain_text("web:uat_persona:webtab")
    expect(page.locator("#personaAdminPage")).to_contain_text("Mental And Emotional State")
    expect(page.locator("#personaAdminPage .persona-mod-row")).to_have_count(6)
    expect(page.locator("#personaAdminPage")).to_contain_text("Perception")
    expect(page.locator("#personaAdminPage")).to_contain_text("Relationship")
    expect(page.locator("#personaAdminPage")).to_contain_text("Life Influence")
    expect(page.locator("#personaAdminPage")).to_contain_text("Memory")
    expect(page.locator("#personaAdminPage")).to_contain_text("Skills And Tools")

    assert_no_browser_errors(page)
