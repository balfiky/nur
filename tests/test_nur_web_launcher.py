"""Startup behavior tests for the ``nur-web`` launcher.

Nūr follows the local-LLM convention (Ollama, LM Studio, Jupyter):
the HTTP surface is open by default, and bearer auth is opt-in by
setting ``api_key`` in ``runtime_config.yaml``. The launcher is
first-run friendly: it auto-creates ``runtime_config.yaml`` with safe
defaults if the file is missing, then binds. On a non-loopback bind
without ``api_key`` set, it prints a one-screen warning so the operator
knows the surface is reachable from the network.
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
def _restore_runtime_config_globals():
    """``nur_api.main()`` mutates ``RUNTIME_CONFIG_PATH`` and the
    ``NUR_RUNTIME_CONFIG`` env var as a side effect of CLI arg parsing,
    so subsequent tests that import the FastAPI app would otherwise pick
    up our tmp configs. Restore both after every test in this module.
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
    @pytest.mark.parametrize("loopback", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_binds_with_no_warning(
        self, tmp_path, fake_uvicorn, monkeypatch, capsys, loopback,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", loopback, "--config", cfg_path],
        )
        nur_api.main()
        assert fake_uvicorn[-1]["host"] == loopback
        err = capsys.readouterr().err
        assert "WARNING" not in err

    def test_loopback_does_not_mutate_api_key(
        self, tmp_path, fake_uvicorn, monkeypatch,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "127.0.0.1", "--config", cfg_path],
        )
        nur_api.main()
        reloaded = RuntimeConfig.from_yaml(cfg_path)
        assert reloaded.api_key == ""


class TestNonLoopbackBinds:
    def test_non_loopback_with_empty_api_key_binds_with_warning(
        self, tmp_path, fake_uvicorn, monkeypatch, capsys,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "0.0.0.0", "--config", cfg_path],
        )
        nur_api.main()
        # Server actually binds — no refusal, no exit.
        assert fake_uvicorn[-1]["host"] == "0.0.0.0"
        # api_key stays empty — launcher never invents one.
        reloaded = RuntimeConfig.from_yaml(cfg_path)
        assert reloaded.api_key == ""
        # Warning was printed to stderr.
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "0.0.0.0" in err
        assert "api_key" in err

    def test_non_loopback_with_api_key_set_binds_silently(
        self, tmp_path, fake_uvicorn, monkeypatch, capsys,
    ):
        cfg_path = _write_config(tmp_path, api_key="operator-set-token")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "0.0.0.0", "--config", cfg_path],
        )
        nur_api.main()
        assert fake_uvicorn[-1]["host"] == "0.0.0.0"
        # api_key untouched.
        reloaded = RuntimeConfig.from_yaml(cfg_path)
        assert reloaded.api_key == "operator-set-token"
        # No warning when auth is in place.
        err = capsys.readouterr().err
        assert "WARNING" not in err


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
        # Defaults are safe: tools off, shell off, no api_key.
        cfg = RuntimeConfig.from_yaml(cfg_path)
        assert cfg.tools_enabled is False
        assert cfg.shell_tool_enabled is False
        assert cfg.api_key == ""
        # Data dir was created.
        assert Path(cfg.data_dir).is_dir()
        assert fake_uvicorn[-1]["host"] == "127.0.0.1"

    def test_missing_config_with_public_bind_still_bootstraps_and_warns(
        self, tmp_path, fake_uvicorn, monkeypatch, capsys,
    ):
        cfg_path = str(tmp_path / "fresh" / "runtime_config.yaml")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "0.0.0.0", "--config", cfg_path],
        )
        nur_api.main()
        # File created; api_key not auto-generated.
        assert Path(cfg_path).is_file()
        cfg = RuntimeConfig.from_yaml(cfg_path)
        assert cfg.api_key == ""
        # Warning printed; server still binds.
        err = capsys.readouterr().err
        assert "WARNING" in err
        assert fake_uvicorn[-1]["host"] == "0.0.0.0"
