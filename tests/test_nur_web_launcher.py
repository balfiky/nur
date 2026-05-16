"""Startup-guard tests for the ``nur-web`` launcher.

The launcher refuses to bind a non-loopback interface when ``api_key``
is empty in the runtime config, because the bundled HTTP surface exposes
memory, identity, admin, and (when enabled) tool endpoints. An open bind
without bearer auth is a footgun, so the launcher exits with code 2 and
a clear message. Operators with an external auth layer (reverse proxy,
VPN, Tailscale) can override the guard with ``--allow-unauthenticated-bind``.
"""

from __future__ import annotations

import sys

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


class TestUnauthenticatedBindGuard:
    def test_loopback_with_empty_api_key_is_allowed(
        self, tmp_path, fake_uvicorn, monkeypatch,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "127.0.0.1", "--config", cfg_path],
        )
        nur_api.main()
        assert fake_uvicorn[-1]["host"] == "127.0.0.1"

    def test_non_loopback_with_empty_api_key_refused(
        self, tmp_path, fake_uvicorn, monkeypatch, capsys,
    ):
        cfg_path = _write_config(tmp_path, api_key="")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "0.0.0.0", "--config", cfg_path],
        )
        with pytest.raises(SystemExit) as exc:
            nur_api.main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "refusing to bind" in err
        assert "api_key" in err
        assert not fake_uvicorn  # uvicorn.run never reached

    def test_non_loopback_with_api_key_set_is_allowed(
        self, tmp_path, fake_uvicorn, monkeypatch,
    ):
        cfg_path = _write_config(tmp_path, api_key="some-bearer-token")
        monkeypatch.setattr(
            sys, "argv", ["nur-web", "--host", "0.0.0.0", "--config", cfg_path],
        )
        nur_api.main()
        assert fake_uvicorn[-1]["host"] == "0.0.0.0"

    def test_non_loopback_with_override_flag_is_allowed(
        self, tmp_path, fake_uvicorn, monkeypatch,
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
