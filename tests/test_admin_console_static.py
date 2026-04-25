from __future__ import annotations

import pytest
from fastapi import HTTPException

from interface import api as interface_api


class TestAdminConsoleStatic:
    def test_admin_route_serves_full_console(self):
        resp = interface_api.admin_index()
        html = resp.body.decode()

        assert resp.status_code == 200
        assert "Nūr Admin" in html
        assert 'href="/admin/assets/admin.css"' in html
        assert 'src="/admin/assets/admin.js"' in html
        assert "Operator Workspace" in html
        assert "Skills" in html
        assert "Tool Inventory" in html

    def test_admin_assets_are_whitelisted(self):
        css = interface_api.admin_asset("admin.css")
        js = interface_api.admin_asset("admin.js")

        assert css.status_code == 200
        assert b"--bg: #ffffff" in css.body
        assert js.status_code == 200
        assert b"fieldSections" in js.body

    def test_unknown_admin_asset_404s(self):
        with pytest.raises(HTTPException) as exc:
            interface_api.admin_asset("index.html")

        assert exc.value.status_code == 404
