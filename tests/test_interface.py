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


class TestEntryPoints:
    def test_main_runs_uvicorn_on_localhost(self, monkeypatch):
        captured: dict[str, object] = {}

        def fake_run(app_path: str, *, host: str, port: int) -> None:
            captured["app_path"] = app_path
            captured["host"] = host
            captured["port"] = port

        monkeypatch.setattr("uvicorn.run", fake_run)

        interface_api.main()

        assert captured == {
            "app_path": "interface.api:app",
            "host": "127.0.0.1",
            "port": 8000,
        }
