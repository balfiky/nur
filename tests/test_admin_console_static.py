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
        assert "Persona Overview" in html
        assert 'id="overviewPersona"' in html
        assert 'data-page="persona"' in html
        assert 'id="page-persona"' in html
        assert 'id="personaAdminPage"' in html
        assert "Settings" in html
        assert 'id="page-settings"' in html
        assert 'class="settings-accordion"' in html
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
        assert 'id="rollbackLifeBatchBtn"' not in html
        assert "Open Persona" in html

    def test_persona_route_redirects_to_admin_persona_section(self):
        resp = interface_api.persona_index()
        alias = interface_api.dashboard_index()

        assert resp.status_code == 307
        assert alias.status_code == 307
        assert resp.headers["location"] == "/admin#persona"
        assert alias.headers["location"] == "/admin#persona"

    def test_admin_assets_are_whitelisted(self):
        css = interface_api.admin_asset("admin.css")
        js = interface_api.admin_asset("admin.js")
        persona_css = interface_api.admin_asset("persona.css")
        persona_js = interface_api.admin_asset("persona.js")

        assert css.status_code == 200
        assert b'@import url("/admin/assets/tokens.css")' in css.body
        assert b".tool-row" in css.body
        assert b".evolution-grid" in css.body
        assert b".metric-foot" in css.body
        assert b"overflow-wrap: anywhere" in css.body
        assert b"prefers-reduced-motion" in css.body
        assert js.status_code == 200
        assert b"fieldSections" in js.body
        assert b"/admin/persona/state" in js.body
        assert b"renderOverviewPersona" in js.body
        assert b"renderPersonaAdminPage" in js.body
        assert b"refreshPersonaBtn" in js.body
        assert b"/v1/tools" in js.body
        assert b"/admin/skills" in js.body
        assert b"/admin/skills/import/upload" in js.body
        assert b"data-skill-action=\"delete\"" in js.body
        assert b"/admin/backups" in js.body
        assert b"/admin/life" in js.body
        assert b"/admin/life/experiences/upload" in js.body
        assert b"/admin/life/rollback" not in js.body
        assert b"renderLifeSnapshot" in js.body
        assert persona_css.status_code == 200
        assert b".dashboard-grid" in persona_css.body
        assert persona_js.status_code == 200
        assert b"/admin/persona/state" in persona_js.body
        assert b"renderPersona" in persona_js.body
        assert b"renderObservability" in persona_js.body
        assert b"renderStateRadar" in persona_js.body

    def test_unknown_admin_asset_404s(self):
        with pytest.raises(HTTPException) as exc:
            interface_api.admin_asset("index.html")

        assert exc.value.status_code == 404
