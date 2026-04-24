"""Tests for the web interface — Phase 7."""

import tempfile

import pytest
import interface.api as interface_api

from interface.api import (
    ChatRequest,
    ConfigUpdateRequest,
    EndSessionRequest,
    RestRequest,
    app,
    chat,
    debug,
    end_session,
    get_config,
    index,
    rest,
    set_pipeline,
    set_session_manager,
    update_config,
)
from pipeline import CognitivePipeline
from core.dual_process.generator import MockLLMBackend
from runtime.config import RuntimeConfig
from runtime.sessions.manager import SessionManager

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def reset_pipeline():
    """Reset the global pipeline before each test."""
    set_session_manager(None)
    interface_api._telegram_task = None
    interface_api._telegram_channel = None
    backend = MockLLMBackend(response="Test response.")
    pipe = CognitivePipeline(llm_backend=backend)
    set_pipeline(pipe)
    yield
    set_pipeline(None)
    set_session_manager(None)
    interface_api._telegram_task = None
    interface_api._telegram_channel = None


async def _chat(
    message: str,
    *,
    user_id: str = "default",
    chat_id: str = "default",
):
    return await chat(ChatRequest(message=message, user_id=user_id, chat_id=chat_id))


async def _debug(*, user_id: str = "default", chat_id: str = "default"):
    return await debug(user_id=user_id, chat_id=chat_id)


async def _end_session(*, user_id: str = "default", chat_id: str = "default"):
    return await end_session(EndSessionRequest(user_id=user_id, chat_id=chat_id))


async def _rest(
    *,
    hours: float,
    user_id: str = "default",
    chat_id: str = "default",
):
    return await rest(RestRequest(hours=hours, user_id=user_id, chat_id=chat_id))


class TestChatEndpoint:
    async def test_chat_returns_response(self):
        resp = await _chat("Hello", user_id="alice")
        assert resp.response == "Test response."
        assert resp.debug

    async def test_chat_debug_has_modulators(self):
        resp = await _chat("Hello")
        debug = resp.debug
        assert "modulator_snapshot" in debug
        assert "arousal" in debug["modulator_snapshot"]
        assert "energy_after" in debug

    async def test_chat_debug_has_event(self):
        resp = await _chat("I'm so angry!")
        debug = resp.debug
        assert debug["event_classified"] != ""
        assert debug["event_intensity"] > 0

    async def test_chat_debug_has_profiles(self):
        resp = await _chat("Hello", user_id="bob")
        debug = resp.debug
        assert debug["person_profile"] is not None
        assert debug["person_profile"]["person_id"] == "bob"
        assert debug["self_profile"] is not None

    async def test_chat_default_user_id(self):
        resp = await _chat("Hi")
        debug = resp.debug
        assert debug["user_id"] == "default"

    async def test_session_manager_path_isolates_web_sessions(self):
        set_pipeline(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = SessionManager(
                RuntimeConfig(data_dir=tmpdir),
                backend_factory=lambda: MockLLMBackend(response="Test response."),
            )
            set_session_manager(manager)
            try:
                await _chat(
                    "You betrayed and deceived me completely!",
                    user_id="alice",
                    chat_id="alice-tab",
                )
                await _chat("Hello", user_id="bob", chat_id="bob-tab")

                alice_debug = await _debug(user_id="alice", chat_id="alice-tab")
                bob_debug = await _debug(user_id="bob", chat_id="bob-tab")

                assert alice_debug["unresolved_count"] > 0
                assert bob_debug["unresolved_count"] == 0
                assert bob_debug["emotion_label"] != "fearful"
            finally:
                await manager.shutdown()


class TestDebugEndpoint:
    async def test_debug_returns_state(self):
        data = await _debug()
        assert "modulator_snapshot" in data
        assert "emotion_label" in data
        assert "energy" in data

    async def test_debug_reflects_chat(self):
        await _chat("Hello")
        data = await _debug()
        assert data["short_term_count"] > 0


class TestSessionEndpoint:
    async def test_end_session(self):
        await _chat("Hello")
        await _chat("Thanks!")
        data = await _end_session()
        assert "summary" in data
        assert "trust_delta" in data
        assert "memories_written" in data

    async def test_end_session_clears_short_term(self):
        await _chat("Hello")
        await _end_session()
        debug_state = await _debug()
        assert debug_state["short_term_count"] == 0


class TestRestEndpoint:
    async def test_rest_recovers_energy(self):
        # Drain some energy
        for i in range(10):
            await _chat(f"Message {i}")
        debug_before = await _debug()
        energy_before = debug_before["energy"]

        data = await _rest(hours=2.0)
        assert data["energy_after"] > energy_before


class TestV2DebugFields:
    """Verify v2 debug fields are present in chat and debug endpoints."""

    async def test_chat_has_anticipation(self):
        debug = (await _chat("Hello")).debug
        assert "anticipation" in debug
        ant = debug["anticipation"]
        assert ant is not None
        assert "predicted_topics" in ant
        assert "confidence" in ant
        assert "basis" in ant

    async def test_chat_has_dialogue_trace(self):
        debug = (await _chat("Hello")).debug
        assert "dialogue_trace" in debug
        trace = debug["dialogue_trace"]
        assert trace is not None
        assert "rounds" in trace
        # Calm messages may have 0 rounds (inner dialogue skipped)
        assert isinstance(trace["rounds"], list)
        assert "tension_level" in trace
        assert "dominant_path" in trace
        assert "total_llm_calls" in trace

    async def test_chat_has_defense_field(self):
        debug = (await _chat("Hello")).debug
        assert "defense_activation" in debug
        # Calm message — defense should be None
        assert debug["defense_activation"] is None

    async def test_chat_has_unresolved_count(self):
        debug = (await _chat("Hello")).debug
        assert "unresolved_count" in debug
        assert isinstance(debug["unresolved_count"], int)

    async def test_chat_has_unresolved_items(self):
        debug = (await _chat("Hello")).debug
        assert "unresolved_items" in debug
        assert isinstance(debug["unresolved_items"], list)

    async def test_chat_has_resolution_in_snapshot(self):
        debug = (await _chat("Hello")).debug
        assert "resolution" in debug["modulator_snapshot"]

    async def test_debug_endpoint_has_resolution(self):
        resp = await _debug()
        assert "resolution" in resp["modulator_snapshot"]
        assert "unresolved_count" in resp
        assert "unresolved_items" in resp

    async def test_spike_creates_unresolved_in_debug(self):
        debug = (
            await _chat("You betrayed and deceived me completely!")
        ).debug
        if debug["is_spike"]:
            assert debug["unresolved_count"] > 0
            assert len(debug["unresolved_items"]) > 0
            item = debug["unresolved_items"][0]
            assert "source" in item
            assert "intensity" in item
            assert "decay_rate" in item


class TestIndexPage:
    async def test_serves_html(self):
        resp = index()
        html = resp.body.decode()
        assert resp.status_code == 200
        assert "Nūr" in html
        assert "<!DOCTYPE html>" in html

    async def test_markdown_renderer_avoids_invalid_lookbehind_regex(self):
        html = index().body.decode()
        assert "(?<!" not in html

    async def test_has_v2_sections(self):
        html = index().body.decode()
        assert "Anticipation" in html
        assert "Inner Dialogue" in html
        assert "Defense" in html
        assert "Unresolved" in html
        assert "Resolution" in html
        assert "Settings" in html
        assert "Save" in html

    async def test_has_admin_console_hooks(self):
        html = index().body.decode()
        assert "Admin" in html
        assert "/admin/config" in html
        assert "/admin/test/llm" in html
        assert "/admin/test/telegram" in html
        assert "/admin/test/storage" in html
        assert "/admin/diagnostics" in html
        assert "/admin/export/config" in html
        assert "/admin/backup" in html
        assert "/admin/sessions/reset" in html
        assert "/admin/users/delete" in html
        assert "Agentic Tools" in html
        assert "Maintenance" in html
        assert "Delete User Data" in html
        assert "Mark first-run setup complete" in html
        assert '<link rel="icon" href="data:,' in html
        assert "clearGenericKey" in html
        assert "wizardPreset === 'local' && !apiKey" in html

    async def test_admin_route_serves_same_shell(self):
        resp = interface_api.admin_index()
        html = resp.body.decode()
        assert resp.status_code == 200
        assert "Nūr" in html
        assert "Admin" in html


class TestConfigEndpoint:
    async def test_get_config_hides_secret_values(self, monkeypatch, tmp_path):
        path = tmp_path / "runtime_config.yaml"
        RuntimeConfig(
            telegram_token="secret-token",
            llm_api_key="generic-key",
            minimax_api_key="minimax-key",
            telegram_allowlist={"123", "456"},
            llm_backend="openai_compatible",
            llm_model="demo-model",
        ).write_yaml(str(path))
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))

        data = await get_config()

        assert data["config"]["telegram_token"] == ""
        assert data["config"]["llm_api_key"] == ""
        assert data["config"]["minimax_api_key"] == ""
        assert data["secret_status"]["telegram_token"] is True
        assert data["secret_status"]["llm_api_key"] is True
        assert data["secret_status"]["minimax_api_key"] is True
        assert data["config"]["telegram_allowlist"] == ["123", "456"]

    async def test_update_config_preserves_secret_when_blank(self, monkeypatch, tmp_path):
        path = tmp_path / "runtime_config.yaml"
        RuntimeConfig(
            telegram_token="keep-me",
            llm_backend="mock",
        ).write_yaml(str(path))
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))

        await update_config(ConfigUpdateRequest(
            data_dir="custom-data",
            llm_backend="openai_compatible",
            llm_base_url="http://127.0.0.1:8000/v1",
            llm_model="demo-model",
            telegram_token="",
        ))

        saved = RuntimeConfig.from_yaml(str(path))
        assert saved.data_dir == "custom-data"
        assert saved.llm_backend == "openai_compatible"
        assert saved.telegram_token == "keep-me"

    async def test_update_config_can_clear_secret(self, monkeypatch, tmp_path):
        path = tmp_path / "runtime_config.yaml"
        RuntimeConfig(
            telegram_token="remove-me",
            llm_backend="mock",
        ).write_yaml(str(path))
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))

        result = await update_config(ConfigUpdateRequest(
            clear_telegram_token=True,
        ))

        saved = RuntimeConfig.from_yaml(str(path))
        assert saved.telegram_token == ""
        assert result["secret_status"]["telegram_token"] is False

    async def test_update_config_preserves_tool_settings_when_omitted(self, monkeypatch, tmp_path):
        """Saving the web Settings form must not silently flip tool flags back.

        The UI form does not surface ``tools_enabled`` /
        ``shell_tool_enabled`` / ``tools_workspace``. Before the fix, a
        plain save wiped them to False/"" because Pydantic defaulted the
        missing fields. Now the handler treats ``None`` as "leave
        unchanged" and only overrides when the client explicitly sets a
        value.
        """
        path = tmp_path / "runtime_config.yaml"
        RuntimeConfig(
            llm_backend="mock",
            tools_enabled=True,
            tools_workspace="/tmp/nur_ws",
            shell_tool_enabled=True,
        ).write_yaml(str(path))
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))

        await update_config(ConfigUpdateRequest(llm_backend="mock"))

        saved = RuntimeConfig.from_yaml(str(path))
        assert saved.tools_enabled is True
        assert saved.tools_workspace == "/tmp/nur_ws"
        assert saved.shell_tool_enabled is True

    async def test_update_config_can_toggle_tool_settings(self, monkeypatch, tmp_path):
        """An explicit value in the request does take effect."""
        path = tmp_path / "runtime_config.yaml"
        RuntimeConfig(llm_backend="mock").write_yaml(str(path))
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))

        await update_config(ConfigUpdateRequest(
            llm_backend="mock",
            tools_enabled=True,
            tools_workspace="/tmp/nur_ws2",
        ))

        saved = RuntimeConfig.from_yaml(str(path))
        assert saved.tools_enabled is True
        assert saved.tools_workspace == "/tmp/nur_ws2"
        # shell_tool_enabled was not specified → remains default False.
        assert saved.shell_tool_enabled is False

    async def test_update_config_reloads_web_session_manager(self, monkeypatch, tmp_path):
        path = tmp_path / "runtime_config.yaml"
        RuntimeConfig(llm_backend="mock").write_yaml(str(path))
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))

        set_pipeline(None)
        manager = SessionManager(
            RuntimeConfig(data_dir=str(tmp_path / "data")),
            backend_factory=lambda: MockLLMBackend(response="Test response."),
        )
        set_session_manager(manager)

        result = await update_config(ConfigUpdateRequest(
            llm_backend="mock",
            max_active_sessions=12,
        ))

        assert result["reloaded_web_manager"] is True

    async def test_update_config_restarts_telegram_polling(self, monkeypatch, tmp_path):
        path = tmp_path / "runtime_config.yaml"
        RuntimeConfig(
            llm_backend="mock",
            telegram_token="keep-me",
            telegram_allowlist={"123"},
        ).write_yaml(str(path))
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(path))

        captured: list[RuntimeConfig] = []

        async def fake_restart(config: RuntimeConfig) -> None:
            captured.append(config)

        monkeypatch.setattr(interface_api, "_restart_telegram_channel", fake_restart)

        set_pipeline(None)
        manager = SessionManager(
            RuntimeConfig(data_dir=str(tmp_path / "data")),
            backend_factory=lambda: MockLLMBackend(response="Test response."),
        )
        set_session_manager(manager)

        result = await update_config(ConfigUpdateRequest(
            llm_backend="mock",
            telegram_allowlist=["456", "789"],
        ))

        assert result["reloaded_web_manager"] is True
        assert len(captured) == 1
        assert captured[0].telegram_token == "keep-me"
        assert captured[0].telegram_allowlist == {"456", "789"}


class TestAdminSoul:
    async def test_get_admin_soul_returns_current_identity(self):
        data = await interface_api.admin_get_soul()
        assert "soul" in data
        assert data["soul"]["name"]  # non-empty
        assert isinstance(data["soul"]["core_values"], dict)
        assert isinstance(data["soul"]["likes"], list)
        assert "is_default_name" in data
        assert "notes" in data and data["notes"]

    async def test_post_admin_soul_writes_yaml(self, monkeypatch, tmp_path):
        import yaml as _yaml
        from interface.api import AdminSoulUpdateRequest

        target = tmp_path / "soul.yaml"
        monkeypatch.setattr(interface_api, "_soul_yaml_path", lambda: str(target))

        req = AdminSoulUpdateRequest(
            name="Iris",
            identity="A precise research companion.",
            voice="calm, direct",
            relational_stance="supportive",
            growth_policy="stable core, drifting voice",
            likes=["clarity", "  ", "honesty"],
            dislikes=["noise"],
            boundaries=["Never fabricate citations."],
            core_values={"honesty": 0.95, "kindness": 0.8},
            initial_traits={"calm": 0.8},
        )
        result = await interface_api.admin_update_soul(req)

        assert result["saved"] is True
        assert result["path"] == str(target)
        assert target.exists()

        loaded = _yaml.safe_load(target.read_text())
        assert loaded["soul"]["name"] == "Iris"
        assert loaded["soul"]["likes"] == ["clarity", "honesty"]
        assert loaded["soul"]["core_values"]["honesty"] == 0.95
        assert loaded["soul"]["initial_traits"]["calm"] == 0.8

    def test_admin_soul_request_rejects_out_of_range_weight(self):
        from pydantic import ValidationError
        from interface.api import AdminSoulUpdateRequest

        with pytest.raises(ValidationError):
            AdminSoulUpdateRequest(name="x", core_values={"k": 1.5})
        with pytest.raises(ValidationError):
            AdminSoulUpdateRequest(name="x", initial_traits={"k": -0.1})

    def test_admin_soul_request_rejects_empty_name(self):
        from pydantic import ValidationError
        from interface.api import AdminSoulUpdateRequest

        with pytest.raises(ValidationError):
            AdminSoulUpdateRequest(name="")
        with pytest.raises(ValidationError):
            AdminSoulUpdateRequest(name="   ")  # stripped to empty

    def test_admin_soul_request_trims_list_entries(self):
        from interface.api import AdminSoulUpdateRequest

        req = AdminSoulUpdateRequest(
            name="x",
            likes=["clarity", "  ", "", " honesty "],
            boundaries=["  Do not pretend certainty when uncertain.  "],
        )
        assert req.likes == ["clarity", "honesty"]
        assert req.boundaries == ["Do not pretend certainty when uncertain."]


class TestAdminSetupDefaultSoul:
    async def test_default_soul_reason_surfaces_when_name_is_nur(
        self, monkeypatch, tmp_path
    ):
        """Setup flagged default_soul when soul.name is still the built-in 'Nūr'."""
        runtime_cfg_path = tmp_path / "runtime_config.yaml"
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(runtime_cfg_path))
        RuntimeConfig(data_dir=str(tmp_path / "data")).write_yaml(str(runtime_cfg_path))
        # The real config/soul.yaml ships with name "Nūr", so default_soul should fire.
        data = await interface_api.admin_status()
        assert "default_soul" in data["setup"]["reasons"]

    async def test_default_soul_reason_clears_when_name_changed(
        self, monkeypatch, tmp_path
    ):
        runtime_cfg_path = tmp_path / "runtime_config.yaml"
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(runtime_cfg_path))
        RuntimeConfig(data_dir=str(tmp_path / "data")).write_yaml(str(runtime_cfg_path))
        monkeypatch.setattr(interface_api, "_soul_looks_default", lambda: False)
        data = await interface_api.admin_status()
        assert "default_soul" not in data["setup"]["reasons"]


class TestAdminSoulDraft:
    async def test_draft_requires_llm_configured(self, monkeypatch, tmp_path):
        """With backend=auto and no keys, draft endpoint refuses with 400."""
        from fastapi import HTTPException
        from interface.api import AdminSoulDraftRequest, admin_draft_soul

        runtime_cfg_path = tmp_path / "runtime_config.yaml"
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(runtime_cfg_path))
        RuntimeConfig(llm_backend="auto").write_yaml(str(runtime_cfg_path))
        # No LLM_API_KEY / MINIMAX_API_KEY env either.
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

        with pytest.raises(HTTPException) as exc:
            await admin_draft_soul(
                AdminSoulDraftRequest(description="a calm assistant that values clarity")
            )
        assert exc.value.status_code == 400
        assert "LLM not configured" in exc.value.detail

    async def test_draft_parses_llm_json_through_schema(self, monkeypatch, tmp_path):
        """Mock LLM that returns schema-valid JSON → validated draft."""
        from interface.api import AdminSoulDraftRequest, admin_draft_soul

        runtime_cfg_path = tmp_path / "runtime_config.yaml"
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(runtime_cfg_path))
        RuntimeConfig(llm_backend="provider", llm_base_url="http://fake.local/v1", llm_model="test-model").write_yaml(str(runtime_cfg_path))

        import json as _json
        fake_json = _json.dumps({
            "name": "Iris",
            "identity": "A calm research assistant.",
            "voice": "Direct, warm, concise.",
            "relational_stance": "Supportive, honest, repair-first.",
            "growth_policy": "Stable core, drifting voice.",
            "likes": ["clarity", "curiosity", "honesty"],
            "dislikes": ["performative chaos", "empty flattery", "cruelty"],
            "boundaries": [
                "Do not pretend certainty when uncertain.",
                "Do not abandon loyalty for convenience.",
                "Do not invent citations.",
            ],
            "core_values": {"honesty": 0.9, "kindness": 0.8, "loyalty": 0.85},
            "initial_traits": {"calm": 0.8, "curious": 0.85, "direct": 0.7},
        })

        class _FakeBackend:
            def generate(self, system, user):
                return "Here is your identity:\n" + fake_json + "\nDone."

        monkeypatch.setattr(interface_api, "create_llm_backend", lambda _cfg: _FakeBackend())

        result = await admin_draft_soul(
            AdminSoulDraftRequest(description="a calm research assistant who values clarity")
        )
        assert result["ok"] is True
        assert result["draft"]["name"] == "Iris"
        assert result["draft"]["core_values"]["honesty"] == 0.9
        assert len(result["draft"]["boundaries"]) == 3

    async def test_draft_rejects_non_json_llm_reply(self, monkeypatch, tmp_path):
        from fastapi import HTTPException
        from interface.api import AdminSoulDraftRequest, admin_draft_soul

        runtime_cfg_path = tmp_path / "runtime_config.yaml"
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(runtime_cfg_path))
        RuntimeConfig(llm_backend="provider", llm_base_url="http://fake.local/v1", llm_model="test-model").write_yaml(str(runtime_cfg_path))

        class _JunkBackend:
            def generate(self, system, user):
                return "I cannot do that."

        monkeypatch.setattr(interface_api, "create_llm_backend", lambda _cfg: _JunkBackend())

        with pytest.raises(HTTPException) as exc:
            await admin_draft_soul(
                AdminSoulDraftRequest(description="a warm coding mentor")
            )
        assert exc.value.status_code == 502
        assert "JSON" in exc.value.detail

    async def test_draft_rejects_schema_violation(self, monkeypatch, tmp_path):
        from fastapi import HTTPException
        from interface.api import AdminSoulDraftRequest, admin_draft_soul

        runtime_cfg_path = tmp_path / "runtime_config.yaml"
        monkeypatch.setattr(interface_api, "RUNTIME_CONFIG_PATH", str(runtime_cfg_path))
        RuntimeConfig(llm_backend="provider", llm_base_url="http://fake.local/v1", llm_model="test-model").write_yaml(str(runtime_cfg_path))

        class _OutOfRangeBackend:
            def generate(self, system, user):
                return '{"name": "X", "core_values": {"honesty": 2.5}}'

        monkeypatch.setattr(interface_api, "create_llm_backend", lambda _cfg: _OutOfRangeBackend())

        with pytest.raises(HTTPException) as exc:
            await admin_draft_soul(
                AdminSoulDraftRequest(description="something reasonable")
            )
        assert exc.value.status_code == 502
        assert "schema" in exc.value.detail.lower()

    def test_draft_request_rejects_too_short(self):
        from pydantic import ValidationError
        from interface.api import AdminSoulDraftRequest

        with pytest.raises(ValidationError):
            AdminSoulDraftRequest(description="short")
        with pytest.raises(ValidationError):
            AdminSoulDraftRequest(description="       ")


class TestAgentNamePropagation:
    def test_generator_prompt_uses_soul_name_not_hardcoded(self):
        """Changing soul.name changes the 'You are X' directive in the generator."""
        from dataclasses import replace
        from config.loader import get_config, reset_config
        from core.dual_process.generator import build_system_prompt
        from core.types import PipelineContext

        reset_config()
        base_soul = get_config().soul
        default_prompt = build_system_prompt(PipelineContext(soul_profile=base_soul))
        assert "You are Nūr" in default_prompt
        assert "{agent_name}" not in default_prompt

        iris_soul = replace(base_soul, name="Iris")
        iris_prompt = build_system_prompt(PipelineContext(soul_profile=iris_soul))
        assert "You are Iris" in iris_prompt
        assert "You are Nūr" not in iris_prompt
        assert "{agent_name}" not in iris_prompt

    def test_generator_template_has_no_hardcoded_nur_directive(self):
        """Guard the generator template itself: first line must use a placeholder."""
        from config.loader import get_config, reset_config

        reset_config()
        template = get_config().generator_prompt
        first_line = template.splitlines()[0]
        assert "{agent_name}" in first_line, (
            "generator.md first line must use {agent_name} placeholder, not hardcode the name"
        )

    def test_self_check_prompt_uses_placeholder(self):
        from config.loader import get_config, reset_config

        reset_config()
        template = get_config().self_check_prompt
        assert "{agent_name}" in template
        # And must not hardcode "Nūr" next to "quality checker"
        assert "quality checker for Nūr" not in template

    def test_digestion_prompt_uses_placeholder(self):
        from config.loader import get_config, reset_config

        reset_config()
        template = get_config().digestion_prompt
        assert "{agent_name}" in template
        assert "consolidation system for Nūr" not in template


class TestSoulSaveReloadsSessionManager:
    async def test_soul_save_shuts_down_active_session_manager(
        self, monkeypatch, tmp_path
    ):
        """POST /admin/soul must evict the cached session manager so the next
        chat turn picks up the new identity without a process restart."""
        from interface.api import AdminSoulUpdateRequest, admin_update_soul

        # Redirect soul writes to tmp; use NUR_CONFIG_DIR so the loader reads it
        monkeypatch.setenv("NUR_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(interface_api, "_soul_yaml_path", lambda: str(tmp_path / "soul.yaml"))

        # Install a sentinel session manager
        shutdown_called = {"count": 0}

        class _SentinelManager:
            active_sessions = {}

            async def shutdown(self):
                shutdown_called["count"] += 1

        interface_api._session_manager = _SentinelManager()
        interface_api._pipeline_override = None

        req = AdminSoulUpdateRequest(
            name="Iris",
            identity="Research companion.",
            voice="calm",
            relational_stance="supportive",
            growth_policy="stable core",
            likes=["clarity"],
            dislikes=["noise"],
            boundaries=["Do not pretend certainty when uncertain."],
            core_values={"honesty": 0.9},
            initial_traits={"calm": 0.8},
        )
        result = await admin_update_soul(req)

        assert shutdown_called["count"] == 1
        assert interface_api._session_manager is None
        assert result["reloaded_session_manager"] is True


class TestConfigLoaderOverride:
    def test_override_dir_replaces_packaged_soul(self, monkeypatch, tmp_path):
        """NUR_CONFIG_DIR points at a user dir; soul.yaml there wins."""
        import importlib
        import config.loader as _loader

        monkeypatch.setenv("NUR_CONFIG_DIR", str(tmp_path))
        (tmp_path / "soul.yaml").write_text(
            "soul:\n  name: Atlas\n  identity: Override identity.\n",
            encoding="utf-8",
        )
        importlib.reload(_loader)
        try:
            cfg = _loader.load_config()
            assert cfg.soul.name == "Atlas"
            assert "Override" in cfg.soul.identity
        finally:
            monkeypatch.delenv("NUR_CONFIG_DIR", raising=False)
            importlib.reload(_loader)

    def test_override_dir_falls_through_when_file_missing(self, monkeypatch, tmp_path):
        """Empty override dir → loader uses packaged defaults."""
        import importlib
        import config.loader as _loader

        monkeypatch.setenv("NUR_CONFIG_DIR", str(tmp_path))
        # No soul.yaml written — should fall through to packaged "Nūr"
        importlib.reload(_loader)
        try:
            cfg = _loader.load_config()
            assert cfg.soul.name == "Nūr"
        finally:
            monkeypatch.delenv("NUR_CONFIG_DIR", raising=False)
            importlib.reload(_loader)

    def test_override_nonexistent_dir_ignored(self, monkeypatch, tmp_path):
        """NUR_CONFIG_DIR pointing at a missing directory is ignored, not fatal."""
        import importlib
        import config.loader as _loader

        monkeypatch.setenv("NUR_CONFIG_DIR", str(tmp_path / "does-not-exist"))
        importlib.reload(_loader)
        try:
            cfg = _loader.load_config()
            assert cfg.soul.name == "Nūr"
        finally:
            monkeypatch.delenv("NUR_CONFIG_DIR", raising=False)
            importlib.reload(_loader)


class TestEntryPoints:
    def test_main_runs_uvicorn_on_localhost(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run(app_path: str, *, host: str, port: int) -> None:
            captured["app_path"] = app_path
            captured["host"] = host
            captured["port"] = port

        monkeypatch.setattr("uvicorn.run", fake_run)
        monkeypatch.setattr("sys.argv", ["nur-web"])

        interface_api.main()

        assert captured == {
            "app_path": "interface.api:app",
            "host": "127.0.0.1",
            "port": 8000,
        }

    def test_main_honors_host_and_port_flags(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run(app_path: str, *, host: str, port: int) -> None:
            captured["host"] = host
            captured["port"] = port

        monkeypatch.setattr("uvicorn.run", fake_run)
        monkeypatch.setattr("sys.argv", ["nur-web", "--host", "0.0.0.0", "--port", "9001"])

        interface_api.main()

        assert captured == {"host": "0.0.0.0", "port": 9001}
