from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.uat.conftest import (
    assert_no_browser_errors,
    configured_uat_backend,
    expect_json,
)

pytestmark = pytest.mark.uat


ADMIN_PAGES = [
    "overview",
    "runtime",
    "models",
    "tools",
    "skills",
    "life",
    "identity",
    "access",
    "channels",
    "maintenance",
]


def test_admin_pages_and_core_options_are_operable(uat_server, page):
    page.goto(uat_server.base_url + "/admin")
    expect(page.locator("#overviewCards .card")).to_have_count(8, timeout=15_000)

    for page_name in ADMIN_PAGES:
        page.click(f".nav-item[data-page='{page_name}']")
        expect(page.locator(f"#page-{page_name}")).to_be_visible()
        expect(page.locator("#pageTitle")).to_contain_text(page_name.title())

    page.click("button.nav-item[data-page='runtime']")
    expect(page.locator("#page-runtime")).to_be_visible()
    page.locator("#page-runtime details.advanced-settings summary").click()
    page.locator("#page-runtime #cfg-max_queue_per_user").fill("4")
    page.click("#saveBtn")
    expect(page.locator("#toast")).to_contain_text("Configuration saved", timeout=15_000)

    config_payload = expect_json(uat_server.get("/admin/config"))
    assert config_payload["config"]["max_queue_per_user"] == 4
    assert config_payload["apply_state"]["restart_required"] is False

    page.click(".nav-item[data-page='maintenance']")
    page.click("#diagnosticsBtn")
    expect(page.locator("#maintenanceOutput")).to_contain_text('"runtime"', timeout=15_000)
    page.click("#reloadRuntimeBtn")
    expect(page.locator("#maintenanceOutput")).to_contain_text("active_sessions_evicted", timeout=15_000)
    expect(page.locator("#restartRuntimeBtn")).to_be_visible()

    assert_no_browser_errors(page)


def test_public_health_and_ready_endpoints_match_clean_runtime(uat_server):
    health = expect_json(uat_server.get("/v1/health"))
    ready = expect_json(uat_server.get("/v1/ready"))

    assert health["status"] == "ok"
    assert ready["status"] == "ready"
    assert ready["llm_backend"] == configured_uat_backend()
    assert ready["active_sessions"] == 0
