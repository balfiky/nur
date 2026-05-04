"""Tests for runtime config loading and LLM backend selection.

Covers:
- RuntimeConfig.from_yaml: loading, missing file, empty file, unknown keys
- create_llm_backend: config-driven selection, env fallback
- Channel startup configuration: console_enabled, headless mode
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from core.dual_process.generator import MockLLMBackend
from runtime.config import RuntimeConfig
from core.provider_client import FastChatCompletionsClient
from runtime.llm.backend import OpenAICompatibleLLMBackend, create_llm_backend


# =========================================================================
# RuntimeConfig.from_yaml
# =========================================================================

class TestConfigFromYaml:
    def test_loads_all_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.yaml")
            with open(path, "w") as f:
                f.write(
                    "data_dir: /tmp/jarvis\n"
                    "max_queue_per_user: 5\n"
                    "max_active_sessions: 20\n"
                    "session_timeout_seconds: 900\n"
                    "console_enabled: false\n"
                    "telegram_token: abc123\n"
                    "telegram_allowlist: [100, 200]\n"
                    "llm_base_url: http://localhost:8000/v1\n"
                    "llm_model: Local/ReasoningModel\n"
                    "llm_api_key: local-key\n"
                    "llm_backend: minimax\n"
                    "autonomy_level: high_risk\n"
                    "character_independence: true\n"
                    "coherence_min_score: 0.5\n"
                    "coherence_max_regenerations: 1\n"
                    "pending_intake_ttl_turns: 4\n"
                    "debug_host: 0.0.0.0\n"
                    "debug_port: 9999\n"
                )
            config = RuntimeConfig.from_yaml(path)
            assert config.data_dir == "/tmp/jarvis"
            assert config.max_queue_per_user == 5
            assert config.max_active_sessions == 20
            assert config.session_timeout_seconds == 900
            assert config.console_enabled is False
            assert config.telegram_token == "abc123"
            assert config.telegram_allowlist == {"100", "200"}
            assert config.llm_base_url == "http://localhost:8000/v1"
            assert config.llm_model == "Local/ReasoningModel"
            assert config.llm_api_key == "local-key"
            assert config.llm_backend == "minimax"
            assert config.autonomy_level == "high_risk"
            assert config.character_independence is True
            assert config.coherence_min_score == 0.5
            assert config.coherence_max_regenerations == 1
            assert config.pending_intake_ttl_turns == 4
            assert config.debug_host == "0.0.0.0"
            assert config.debug_port == 9999

    def test_missing_file_returns_defaults(self):
        config = RuntimeConfig.from_yaml("/nonexistent/file.yaml")
        assert config.data_dir == "data"
        assert config.max_active_sessions == 10
        assert config.console_enabled is True
        assert config.llm_backend == "auto"
        assert config.autonomy_level == "assisted"

    def test_empty_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "empty.yaml")
            with open(path, "w") as f:
                f.write("")
            config = RuntimeConfig.from_yaml(path)
            assert config.data_dir == "data"
            assert config.console_enabled is True

    def test_unknown_keys_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.yaml")
            with open(path, "w") as f:
                f.write(
                    "data_dir: /tmp/test\n"
                    "unknown_future_key: 42\n"
                    "another_unknown: true\n"
                )
            config = RuntimeConfig.from_yaml(path)
            assert config.data_dir == "/tmp/test"

    def test_partial_config_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "partial.yaml")
            with open(path, "w") as f:
                f.write("data_dir: /tmp/custom\n")
            config = RuntimeConfig.from_yaml(path)
            assert config.data_dir == "/tmp/custom"
            assert config.max_active_sessions == 10  # default
            assert config.console_enabled is True     # default

    def test_legacy_proactive_keys_are_loaded_as_recovery_density_aliases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.yaml")
            with open(path, "w") as f:
                f.write(
                    "proactive_max_per_session: 7\n"
                    "proactive_cooldown: 42.0\n"
                )
            config = RuntimeConfig.from_yaml(path)
            public = config.to_public_dict()

            assert config.proactive_density_reference == 7
            assert config.proactive_recovery_seconds == 42.0
            assert "proactive_max_per_session" not in public
            assert "proactive_cooldown" not in public

    def test_allowlist_converts_to_set_of_strings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.yaml")
            with open(path, "w") as f:
                f.write("telegram_allowlist: [12345, 67890]\n")
            config = RuntimeConfig.from_yaml(path)
            assert config.telegram_allowlist == {"12345", "67890"}
            assert all(isinstance(x, str) for x in config.telegram_allowlist)

    def test_empty_allowlist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.yaml")
            with open(path, "w") as f:
                f.write("telegram_allowlist: []\n")
            config = RuntimeConfig.from_yaml(path)
            assert config.telegram_allowlist == set()


# =========================================================================
# create_llm_backend
# =========================================================================

class TestBackendSelection:
    def _mock_response(self, content: str = "ok", status_code: int = 200, text: str = ""):
        from unittest.mock import MagicMock

        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        resp.json.return_value = {
            "choices": [{"message": {"content": content}}],
        }
        return resp

    @patch("requests.Session.post")
    def test_openai_compatible_disables_template_thinking(self, mock_post):
        mock_post.return_value = self._mock_response("ok")
        backend = OpenAICompatibleLLMBackend(
            base_url="http://localhost:8000/v1",
            model="Local/ReasoningModel",
            api_key="k",
        )
        backend.generate("sys", "msg")

        payload = mock_post.call_args.kwargs["json"]
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    @patch("requests.Session.post")
    def test_openai_compatible_retries_without_template_kwargs_when_rejected(self, mock_post):
        rejected = self._mock_response(
            "",
            status_code=400,
            text="unknown field chat_template_kwargs",
        )
        accepted = self._mock_response("ok")
        mock_post.side_effect = [rejected, accepted]
        backend = OpenAICompatibleLLMBackend(
            base_url="https://provider.example/v1",
            model="provider-model",
            api_key="k",
        )

        assert backend.generate("sys", "msg") == "ok"
        first_payload = mock_post.call_args_list[0].kwargs["json"]
        second_payload = mock_post.call_args_list[1].kwargs["json"]
        assert "chat_template_kwargs" in first_payload
        assert "chat_template_kwargs" not in second_payload

    def test_provider_backend(self):
        """Explicit hosted-provider config returns the generic sync backend."""
        config = RuntimeConfig(
            llm_backend="provider",
            llm_base_url="https://provider.example/v1",
            llm_model="provider-model",
            llm_api_key="provider-key",
        )
        backend = create_llm_backend(config)
        assert isinstance(backend, OpenAICompatibleLLMBackend)

    def test_provider_requires_base_url_and_model(self):
        """Explicit hosted-provider backend fails loudly if under-configured."""
        config = RuntimeConfig(llm_backend="provider")
        with pytest.raises(ValueError, match="llm_base_url|llm_model"):
            create_llm_backend(config)

    def test_openai_compatible_backend(self):
        """Explicit OpenAI-compatible config returns the generic sync backend."""
        config = RuntimeConfig(
            llm_backend="openai_compatible",
            llm_base_url="http://localhost:8000/v1",
            llm_model="Local/ReasoningModel",
        )
        backend = create_llm_backend(config)
        assert isinstance(backend, OpenAICompatibleLLMBackend)

    def test_openai_compatible_requires_base_url_and_model(self):
        """Explicit OpenAI-compatible backend fails loudly if under-configured."""
        config = RuntimeConfig(llm_backend="openai_compatible")
        with pytest.raises(ValueError, match="llm_base_url|llm_model"):
            create_llm_backend(config)

    def test_auto_prefers_openai_compatible_when_configured(self):
        """Auto mode selects local OpenAI-compatible backend when configured."""
        config = RuntimeConfig(
            llm_backend="auto",
            llm_base_url="http://localhost:8000/v1",
            llm_model="Local/ReasoningModel",
        )
        backend = create_llm_backend(config)
        assert isinstance(backend, OpenAICompatibleLLMBackend)

    def test_mock_backend_forced(self):
        """llm_backend='mock' always returns MockLLMBackend."""
        config = RuntimeConfig(llm_backend="mock")
        backend = create_llm_backend(config)
        assert isinstance(backend, MockLLMBackend)

    def test_mock_backend_forced_even_with_key(self):
        """llm_backend='mock' overrides even when API key is present."""
        config = RuntimeConfig(llm_backend="mock", llm_api_key="sk-test")
        backend = create_llm_backend(config)
        assert isinstance(backend, MockLLMBackend)

    def test_auto_without_key_returns_mock(self, monkeypatch):
        """llm_backend='auto' with no API key returns MockLLMBackend."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        config = RuntimeConfig(llm_backend="auto")
        backend = create_llm_backend(config)
        assert isinstance(backend, MockLLMBackend)

    def test_auto_with_generic_key_but_no_endpoint_returns_mock(self, monkeypatch):
        """A generic key alone is not enough to guess a provider endpoint."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        config = RuntimeConfig(llm_backend="auto", llm_api_key="generic-key")
        backend = create_llm_backend(config)
        assert isinstance(backend, MockLLMBackend)

    def test_minimax_backend_with_config_key(self):
        """Explicit legacy MiniMax config uses the generic LLM API key."""
        config = RuntimeConfig(llm_backend="minimax", llm_api_key="sk-test")
        backend = create_llm_backend(config)
        assert isinstance(backend, FastChatCompletionsClient)

    def test_no_config_falls_back_to_env(self, monkeypatch):
        """Without config, no provider-specific key fallback is used."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        backend = create_llm_backend()
        assert isinstance(backend, MockLLMBackend)

    def test_none_config_falls_back_to_env(self, monkeypatch):
        """config=None uses env var only."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        backend = create_llm_backend(None)
        assert isinstance(backend, MockLLMBackend)


# =========================================================================
# Channel startup configuration
# =========================================================================

class TestChannelConfig:
    def test_console_enabled_default(self):
        config = RuntimeConfig()
        assert config.console_enabled is True

    def test_console_disabled(self):
        config = RuntimeConfig(console_enabled=False)
        assert config.console_enabled is False

    def test_telegram_disabled_by_default(self):
        config = RuntimeConfig()
        assert config.telegram_token == ""

    def test_headless_config(self):
        """Config for Telegram-only (headless) runtime."""
        config = RuntimeConfig(
            console_enabled=False,
            telegram_token="bot123:abc",
        )
        assert config.console_enabled is False
        assert config.telegram_token == "bot123:abc"

    def test_from_yaml_console_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "config.yaml")
            with open(path, "w") as f:
                f.write(
                    "console_enabled: false\n"
                    "telegram_token: bot123:abc\n"
                )
            config = RuntimeConfig.from_yaml(path)
            assert config.console_enabled is False
            assert config.telegram_token == "bot123:abc"
