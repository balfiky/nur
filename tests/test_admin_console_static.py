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
        assert "Life History" in html
        assert "Evolution Snapshot" in html
        assert "Tool Inventory" in html
        assert 'id="refreshToolsBtn"' in html
        assert 'id="toolCategoryFilter"' in html
        assert 'id="importSkillBtn"' in html
        assert 'id="skillUpload"' in html
        assert 'id="skillsList"' in html
        assert 'id="lifeUploadFile"' in html
        assert 'id="lifeSnapshotNarrative"' in html
        assert 'id="lifeDriveDrift"' in html
        assert 'id="lifeTimeline"' in html
        assert 'id="lifeExperiences"' in html
        assert 'id="rollbackLifeBatchBtn"' in html

    def test_admin_assets_are_whitelisted(self):
        css = interface_api.admin_asset("admin.css")
        js = interface_api.admin_asset("admin.js")

        assert css.status_code == 200
        assert b"--bg: #ffffff" in css.body
        assert b".tool-row" in css.body
        assert b".evolution-grid" in css.body
        assert b".metric-foot" in css.body
        assert b"overflow-wrap: anywhere" in css.body
        assert b"prefers-reduced-motion" in css.body
        assert js.status_code == 200
        assert b"fieldSections" in js.body
        assert b"/v1/tools" in js.body
        assert b"/admin/skills" in js.body
        assert b"/admin/skills/import/upload" in js.body
        assert b"/admin/life" in js.body
        assert b"/admin/life/experiences/upload" in js.body
        assert b"/admin/life/rollback" in js.body
        assert b"renderLifeSnapshot" in js.body

    def test_unknown_admin_asset_404s(self):
        with pytest.raises(HTTPException) as exc:
            interface_api.admin_asset("index.html")

        assert exc.value.status_code == 404
