"""Tests for runtime config loading and LLM backend selection.

Covers:
- RuntimeConfig.from_yaml: loading, missing file, empty file, unknown keys
- create_llm_backend: config-driven selection, env fallback
- Channel startup configuration: console_enabled, headless mode
"""

from __future__ import annotations

import os
import tempfile

import pytest

from core.dual_process.generator import MockLLMBackend
from runtime.config import RuntimeConfig
from runtime.llm.backend import create_llm_backend


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
                    "llm_backend: minimax\n"
                    "minimax_api_key: sk-test\n"
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
            assert config.llm_backend == "minimax"
            assert config.minimax_api_key == "sk-test"
            assert config.debug_host == "0.0.0.0"
            assert config.debug_port == 9999

    def test_missing_file_returns_defaults(self):
        config = RuntimeConfig.from_yaml("/nonexistent/file.yaml")
        assert config.data_dir == "data"
        assert config.max_active_sessions == 10
        assert config.console_enabled is True
        assert config.llm_backend == "auto"

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
    def test_mock_backend_forced(self):
        """llm_backend='mock' always returns MockLLMBackend."""
        config = RuntimeConfig(llm_backend="mock")
        backend = create_llm_backend(config)
        assert isinstance(backend, MockLLMBackend)

    def test_mock_backend_forced_even_with_key(self):
        """llm_backend='mock' overrides even when API key is present."""
        config = RuntimeConfig(llm_backend="mock", minimax_api_key="sk-test")
        backend = create_llm_backend(config)
        assert isinstance(backend, MockLLMBackend)

    def test_auto_without_key_returns_mock(self, monkeypatch):
        """llm_backend='auto' with no API key returns MockLLMBackend."""
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        config = RuntimeConfig(llm_backend="auto", minimax_api_key="")
        backend = create_llm_backend(config)
        assert isinstance(backend, MockLLMBackend)

    def test_no_config_falls_back_to_env(self, monkeypatch):
        """Without config, uses MINIMAX_API_KEY env var."""
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        backend = create_llm_backend()
        assert isinstance(backend, MockLLMBackend)

    def test_none_config_falls_back_to_env(self, monkeypatch):
        """config=None uses env var only."""
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
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
