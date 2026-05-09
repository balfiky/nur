"""Tests for runtime config loading and LLM backend selection.

Covers:
- RuntimeConfig.from_yaml: loading, missing file, empty file, unknown keys
- create_llm_backend: config-driven selection, env fallback
- Channel startup configuration: console_enabled, headless mode
"""

from __future__ import annotations

import os
import json
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from runtime.config import RuntimeConfig
from core.provider_client import FastChatCompletionsClient
from runtime.llm.backend import CodexCLIBackend, OpenAICompatibleLLMBackend, create_llm_backend, list_codex_models


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
# Learning schedule
# =========================================================================

class TestLearningSchedule:
    def test_default_learning_fields(self):
        cfg = RuntimeConfig()
        assert cfg.learning_budget_kind == "local"
        assert cfg.learning_max_questions_per_day == 3
        assert cfg.learning_max_seconds_per_day == 1800.0
        assert cfg.metabolism_min_elapsed_days == 1.0

    def test_learning_fields_round_trip_through_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "rc.yaml")
            cfg = RuntimeConfig(
                learning_budget_kind="local",
                learning_max_questions_per_day=10,
                learning_max_seconds_per_day=600.0,
                metabolism_min_elapsed_days=0.25,
            )
            cfg.write_yaml(path)
            loaded = RuntimeConfig.from_yaml(path)
            assert loaded.learning_max_questions_per_day == 10
            assert loaded.learning_max_seconds_per_day == 600.0
            assert loaded.metabolism_min_elapsed_days == 0.25

    def test_learning_budget_builder_returns_local_budget_with_caps(self):
        from runtime.learning_budget import LocalBudget
        cfg = RuntimeConfig(
            learning_max_questions_per_day=7,
            learning_max_seconds_per_day=900.0,
        )
        budget = cfg.learning_budget()
        assert isinstance(budget, LocalBudget)
        assert budget.max_questions_per_day == 7
        assert budget.max_seconds_per_day == 900.0

    def test_learning_budget_caps_pursuit_after_limit(self):
        cfg = RuntimeConfig(learning_max_questions_per_day=2)
        budget = cfg.learning_budget()
        assert budget.can_pursue() is True
        budget.consume(questions=1)
        assert budget.can_pursue() is True
        budget.consume(questions=1)
        # 2 consumed; 2/day cap reached.
        assert budget.can_pursue() is False


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

    def test_codex_backend_uses_read_only_ephemeral_cli(self, monkeypatch, tmp_path):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            output_path = cmd[cmd.index("--output-last-message") + 1]
            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write("codex response\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="ignored", stderr="")

        monkeypatch.setattr("runtime.llm.backend.shutil.which", lambda _exe: "/usr/bin/codex")
        monkeypatch.setattr("runtime.llm.backend.subprocess.run", fake_run)

        backend = CodexCLIBackend(model="gpt-test", workdir=str(tmp_path))

        assert backend.generate("system text", "user text") == "codex response"
        cmd = captured["cmd"]
        assert cmd[:2] == ["codex", "exec"]
        assert "--ephemeral" in cmd
        assert "--ignore-rules" in cmd
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"
        assert cmd[cmd.index("--model") + 1] == "gpt-test"
        assert captured["kwargs"]["cwd"] == str(tmp_path)
        assert "system text" in captured["kwargs"]["input"]
        assert "user text" in captured["kwargs"]["input"]

    def test_list_codex_models_returns_safe_dropdown_catalog(self, monkeypatch):
        raw_catalog = json.dumps({
            "models": [
                {
                    "slug": "gpt-test",
                    "display_name": "GPT Test",
                    "description": "Test model",
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}],
                    "visibility": "list",
                    "base_instructions": "must not leak",
                },
                {"slug": "hidden-model", "visibility": "hidden"},
            ],
        })

        def fake_run(cmd, **kwargs):
            assert cmd == ["codex", "debug", "models"]
            return subprocess.CompletedProcess(cmd, 0, stdout=raw_catalog, stderr="")

        monkeypatch.setattr("runtime.llm.backend.shutil.which", lambda _exe: "/usr/bin/codex")
        monkeypatch.setattr("runtime.llm.backend.subprocess.run", fake_run)

        models = list_codex_models()

        assert models == [{
            "slug": "gpt-test",
            "display_name": "GPT Test",
            "description": "Test model",
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": ["low", "medium"],
        }]
        assert "base_instructions" not in models[0]

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

    def test_codex_backend(self, monkeypatch):
        monkeypatch.setattr("runtime.llm.backend.shutil.which", lambda _exe: "/usr/bin/codex")
        config = RuntimeConfig(llm_backend="codex", llm_model="gpt-test")
        backend = create_llm_backend(config)
        assert isinstance(backend, CodexCLIBackend)

    def test_codex_backend_requires_cli(self, monkeypatch):
        monkeypatch.setattr("runtime.llm.backend.shutil.which", lambda _exe: None)
        config = RuntimeConfig(llm_backend="codex")
        with pytest.raises(ValueError, match="codex CLI"):
            create_llm_backend(config)

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

    def test_mock_backend_no_longer_accepted(self):
        """Mock has been removed entirely; the factory rejects it."""
        config = RuntimeConfig(llm_backend="mock")
        with pytest.raises(ValueError, match="mock"):
            create_llm_backend(config)

    def test_auto_without_key_raises(self, monkeypatch):
        """llm_backend='auto' with insufficient config raises (no fallback)."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        config = RuntimeConfig(llm_backend="auto")
        with pytest.raises(ValueError):
            create_llm_backend(config)

    def test_auto_with_generic_key_but_no_endpoint_raises(self, monkeypatch):
        """A generic key alone is not enough — must specify endpoint or provider."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        config = RuntimeConfig(llm_backend="auto", llm_api_key="generic-key")
        with pytest.raises(ValueError):
            create_llm_backend(config)

    def test_minimax_backend_with_config_key(self):
        """Explicit legacy MiniMax config uses the generic LLM API key."""
        config = RuntimeConfig(llm_backend="minimax", llm_api_key="sk-test")
        backend = create_llm_backend(config)
        assert isinstance(backend, FastChatCompletionsClient)

    def test_no_config_raises(self, monkeypatch):
        """Without config, the factory raises rather than falling back."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(ValueError):
            create_llm_backend()

    def test_none_config_raises(self, monkeypatch):
        """config=None — same; no silent fallback."""
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(ValueError):
            create_llm_backend(None)


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
