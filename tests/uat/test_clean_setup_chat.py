from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.uat.conftest import assert_no_browser_errors, expect_json

pytestmark = pytest.mark.uat


def test_clean_web_chat_exposes_debug_panels(uat_server, page):
    page.goto(uat_server.base_url + "/")
    expect(page.locator("#msgInput")).to_be_visible()
    expect(page.locator("#wizardOverlay")).not_to_have_class("wizard-overlay open")

    page.fill("#msgInput", "hello, can you help me remember concise updates?")
    page.click("#sendBtn")

    assistant_messages = page.locator(".message-row.assistant .msg-body")
    expect(assistant_messages).to_have_count(1, timeout=45_000)
    expect(page.locator("#whyBtn")).to_be_enabled()

    page.click("#debugToggle")
    expect(page.locator("#debugPanel")).to_have_class("debug-panel open")
    expect(page.locator("#turnExplanation")).to_contain_text("Interpretation")
    expect(page.locator("#relationshipState")).to_contain_text("Strategy")
    expect(page.locator("#memoryInspector")).to_contain_text("Relationship")

    assert_no_browser_errors(page)


def test_v1_chat_include_debug_has_structural_payload(uat_server):
    response = uat_server.post(
        "/v1/chat",
        {
            "message": "I am worried about my deadline and need practical help.",
            "user_id": "uat_api",
            "chat_id": "default",
            "include_debug": True,
        },
    )
    payload = expect_json(response)
    debug = payload["debug"]

    assert debug["strategy_trace"]["selected"] == debug["response_strategy"]
    assert "explanation" in debug
    assert set(debug["explanation"]) == {
        "interpretation",
        "strategy",
        "state",
        "memory",
        "life_history",
        "tools",
        "limits",
    }
    relationship_view = debug["relationship_view"]
    assert relationship_view["modulators"]["arousal"]["delta"] is None
    assert "life_influence" in relationship_view
    assert "memory_used" in relationship_view
