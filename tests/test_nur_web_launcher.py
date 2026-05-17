"""Startup behavior tests for the ``nur-web`` launcher.

The launcher is first-run friendly: it auto-creates ``runtime_config.yaml``
with safe defaults if the file is missing, and for non-loopback binds it
auto-generates a strong ``api_key`` rather than refusing. This preserves
the v0.30.3 safety guarantee (never bind a non-loopback interface without
a bearer-auth requirement) while removing the "edit YAML by hand" wall
new operators hit on first install. Override remains
``--allow-unauthenticated-bind`` for external-auth-layer deployments.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from interface import api as nur_api
from runtime.config import RuntimeConfig


def _write_config(path, *, api_key: str = "") -> str:
    cfg_path = str(path / "runtime_config.yaml")
    cfg = RuntimeConfig(data_dir=str(path / "data"), api_key=api_key)
    cfg.write_yaml(cfg_path)
    return cfg_path


@pytest.fixture
def fake_uvicorn(monkeypatch):
    calls: list[dict] = []

    def fake_run(app, *, host, port):  # signature matches uvicorn.run
        calls.append({"app": app, "host": host, "port": port})

    import uvicorn
    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


@pytest.fixture(autouse=True)
def _restore_runtime_config_globals(monkeypatch):
    """``nur_api.main()`` mutates ``RUNTIME_CONFIG_PATH`` and the
    ``NUR_RUNTIME_CONFIG`` env var as a side effect of CLI arg parsing,
    so subsequent tests that import the FastAPI app would otherwise pick
    up our tmp configs (which may have an auto-generated api_key, causing
    them to hit 401 on endpoints under bearer auth). Restore both
    after every test in this module.
    """
    original_path = nur_api.RUNTIME_CONFIG_PATH
    original_env = os.environ.get("NUR_RUNTIME_CONFIG")
    yield
    nur_api.RUNTIME_CONFIG_PATH = original_path
    if original_env is None:
        os.environ.pop("NUR_RUNTIME_CONFIG", None)
    else:
        os.environ["NUR_RUNTIME_CONFIG"] = original_env


class TestLoopbackBinds:
    def test_loopback_with_empty_api_key_is_allowed(
        self, tmp_path, fake_uvicorn, monkeypatch,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "127.0.0.1", "--config", cfg_path],
        )
        nur_api.main()
        assert fake_uvicorn[-1]["host"] == "127.0.0.1"

    @pytest.mark.parametrize("loopback", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_aliases_skip_guard(
        self, tmp_path, fake_uvicorn, monkeypatch, loopback,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", loopback, "--config", cfg_path],
        )
        nur_api.main()
        assert fake_uvicorn[-1]["host"] == loopback

    def test_loopback_does_not_generate_or_persist_api_key(
        self, tmp_path, fake_uvicorn, monkeypatch,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "127.0.0.1", "--config", cfg_path],
        )
        nur_api.main()
        reloaded = RuntimeConfig.from_yaml(cfg_path)
        assert reloaded.api_key == ""


class TestNonLoopbackAutoApiKey:
    def test_non_loopback_with_empty_api_key_auto_generates(
        self, tmp_path, fake_uvicorn, monkeypatch, capsys,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "0.0.0.0", "--config", cfg_path],
        )
        nur_api.main()
        # Server actually starts now — no refuse/exit.
        assert fake_uvicorn[-1]["host"] == "0.0.0.0"
        # Key is persisted to config.
        reloaded = RuntimeConfig.from_yaml(cfg_path)
        assert reloaded.api_key
        assert len(reloaded.api_key) >= 32
        # Key is printed once for the operator to copy.
        err = capsys.readouterr().err
        assert "Generated api_key" in err
        assert reloaded.api_key in err
        assert "Authorization: Bearer" in err

    def test_non_loopback_preserves_existing_api_key(
        self, tmp_path, fake_uvicorn, monkeypatch, capsys,
    ):
        cfg_path = _write_config(tmp_path, api_key="operator-set-token-abc")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "0.0.0.0", "--config", cfg_path],
        )
        nur_api.main()
        assert fake_uvicorn[-1]["host"] == "0.0.0.0"
        reloaded = RuntimeConfig.from_yaml(cfg_path)
        assert reloaded.api_key == "operator-set-token-abc"
        # No "Generated api_key" banner when key was already set.
        err = capsys.readouterr().err
        assert "Generated api_key" not in err

    def test_non_loopback_with_override_flag_skips_key_generation(
        self, tmp_path, fake_uvicorn, monkeypatch, capsys,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv",
            [
                "nur-web", "--host", "0.0.0.0",
                "--allow-unauthenticated-bind", "--config", cfg_path,
            ],
        )
        nur_api.main()
        assert fake_uvicorn[-1]["host"] == "0.0.0.0"
        # api_key stays empty when the operator explicitly waives bearer auth.
        reloaded = RuntimeConfig.from_yaml(cfg_path)
        assert reloaded.api_key == ""
        err = capsys.readouterr().err
        assert "Generated api_key" not in err


class TestFirstRunBootstrap:
    def test_missing_config_is_auto_created_with_safe_defaults(
        self, tmp_path, fake_uvicorn, monkeypatch,
    ):
        cfg_path = str(tmp_path / "fresh" / "runtime_config.yaml")
        assert not Path(cfg_path).exists()
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "127.0.0.1", "--config", cfg_path],
        )
        nur_api.main()
        # Config file exists after first run.
        assert Path(cfg_path).is_file()
        # Defaults are safe: tools off, shell off, no api_key on loopback.
        cfg = RuntimeConfig.from_yaml(cfg_path)
        assert cfg.tools_enabled is False
        assert cfg.shell_tool_enabled is False
        assert cfg.api_key == ""
        # Data dir was created.
        assert Path(cfg.data_dir).is_dir()
        assert fake_uvicorn[-1]["host"] == "127.0.0.1"

    def test_missing_config_with_public_bind_bootstraps_and_generates_key(
        self, tmp_path, fake_uvicorn, monkeypatch, capsys,
    ):
        cfg_path = str(tmp_path / "fresh" / "runtime_config.yaml")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "0.0.0.0", "--config", cfg_path],
        )
        nur_api.main()
        assert Path(cfg_path).is_file()
        cfg = RuntimeConfig.from_yaml(cfg_path)
        assert cfg.api_key
        err = capsys.readouterr().err
        assert "Generated api_key" in err
        assert cfg.api_key in err
        assert fake_uvicorn[-1]["host"] == "0.0.0.0"
