from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.uat.conftest import assert_no_browser_errors

pytestmark = pytest.mark.uat


def test_chat_and_admin_controls_have_accessible_names(uat_server, page):
    for path, required in [
        (
            "/",
            ["#msgInput", "#sendBtn", "#whyBtn", "#debugToggle", "a.icon-btn[href='/admin']"],
        ),
        (
            "/admin",
            ["#saveBtn", "#refreshAllBtn", "#runtimeForm", "#maintenanceOutput"],
        ),
        (
            "/admin#persona",
            ["#refreshPersonaBtn", "#personaAdminPage"],
        ),
    ]:
        page.goto(uat_server.base_url + path)
        for selector in required:
            expect(page.locator(selector)).to_be_attached()

        unlabeled = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('button, a[href], input, textarea, select'))
              .filter((el) => {
                const style = window.getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.visibility !== 'hidden'
                  && style.display !== 'none'
                  && rect.width > 0
                  && rect.height > 0
                  && !el.closest('[hidden]');
              })
              .map((el) => {
                const id = el.getAttribute('id') || '';
                const labels = id
                  ? Array.from(document.querySelectorAll(`label[for="${CSS.escape(id)}"]`)).map((label) => label.textContent.trim())
                  : [];
                const name = [
                  el.getAttribute('aria-label'),
                  el.getAttribute('title'),
                  el.getAttribute('placeholder'),
                  el.textContent,
                  ...labels,
                ].map((value) => (value || '').trim()).filter(Boolean).join(' ');
                return {
                  tag: el.tagName.toLowerCase(),
                  id,
                  type: el.getAttribute('type') || '',
                  text: (el.textContent || '').trim(),
                  name,
                };
              })
              .filter((item) => !item.name)
            """
        )
        assert unlabeled == []

    assert_no_browser_errors(page)
