from __future__ import annotations

import pytest

from tests.uat.conftest import expect_json

pytestmark = pytest.mark.uat


def test_session_files_survive_web_server_restart(uat_server):
    first = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "remember that my UAT preference is concise status updates",
                "user_id": "uat_persist",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    assert first["session_key"] == "web:uat_persist:default"

    session_dir = uat_server.data_dir / "web_uat_persist" / "sessions"
    state_file = session_dir / "default.json"
    history_file = session_dir / "default.history.json"
    assert state_file.exists()
    assert history_file.exists()

    uat_server.restart()
    ready = expect_json(uat_server.get("/v1/ready"))
    assert ready["status"] == "ready"

    second = expect_json(
        uat_server.post(
            "/v1/chat",
            {
                "message": "continue the setup check",
                "user_id": "uat_persist",
                "chat_id": "default",
                "include_debug": True,
            },
        )
    )
    assert second["session_key"] == "web:uat_persist:default"
    assert state_file.exists()
    assert history_file.exists()
