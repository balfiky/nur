from __future__ import annotations

from runtime.config import RuntimeConfig
from interface.setup import _browser_url, _initialize_workspace


def test_initialize_workspace_creates_config_and_dirs(tmp_path):
    config_path = tmp_path / "runtime_config.yaml"

    created = _initialize_workspace(config_path, "data", force=False)

    assert created is True
    config = RuntimeConfig.from_yaml(str(config_path))
    assert config.data_dir == str(tmp_path / "data")
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "data" / "workspace").is_dir()


def test_initialize_workspace_preserves_existing_config(tmp_path):
    config_path = tmp_path / "runtime_config.yaml"
    RuntimeConfig(data_dir=str(tmp_path / "custom")).write_yaml(str(config_path))

    created = _initialize_workspace(config_path, "data", force=False)

    assert created is False
    config = RuntimeConfig.from_yaml(str(config_path))
    assert config.data_dir == str(tmp_path / "custom")


def test_browser_url_uses_loopback_for_public_bind():
    assert _browser_url("0.0.0.0", 8000) == "http://127.0.0.1:8000"
