from __future__ import annotations

import json

from runtime.config import RuntimeConfig
from interface.setup import _browser_url, _initialize_workspace, _run_terminal_setup


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


def test_terminal_setup_writes_config_identity_and_completion(tmp_path):
    config_path = tmp_path / "runtime_config.yaml"
    identity_dir = tmp_path / "identity"
    answers = iter([
        "",        # data directory
        "2",       # vLLM
        "",        # base URL
        "qwen-test",
        "",        # API key
        "n",       # Telegram
        "n",       # tools
        "Jarvis",  # identity name
        "2",       # operator
    ])

    config = _run_terminal_setup(
        config_path=config_path,
        data_dir="data",
        force=False,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _line: None,
        environ={"NUR_CONFIG_DIR": str(identity_dir)},
    )

    saved = RuntimeConfig.from_yaml(str(config_path))
    assert config.llm_model == "qwen-test"
    assert saved.llm_backend == "openai_compatible"
    assert saved.llm_base_url == "http://localhost:8002/v1"
    assert saved.llm_model == "qwen-test"
    assert (tmp_path / "data" / "workspace").is_dir()
    assert "name: Jarvis" in (identity_dir / "soul.yaml").read_text(encoding="utf-8")

    admin_state = json.loads((tmp_path / "data" / "admin_state.json").read_text(encoding="utf-8"))
    assert admin_state["setup_completed"] is True


def test_terminal_setup_can_configure_tools_and_custom_workspace(tmp_path):
    config_path = tmp_path / "runtime_config.yaml"
    identity_dir = tmp_path / "identity"
    workspace = tmp_path / "tools"
    answers = iter([
        "",       # data directory
        "1",      # mock
        "n",      # Telegram
        "y",      # tools
        "2",      # autonomous
        "y",      # shell
        str(workspace),
        "",       # identity name
        "",       # steady
    ])

    _run_terminal_setup(
        config_path=config_path,
        data_dir="data",
        force=False,
        input_fn=lambda _prompt: next(answers),
        output_fn=lambda _line: None,
        environ={"NUR_CONFIG_DIR": str(identity_dir)},
    )

    saved = RuntimeConfig.from_yaml(str(config_path))
    assert saved.tools_enabled is True
    assert saved.shell_tool_enabled is True
    assert saved.autonomy_level == "autonomous"
    assert saved.tools_workspace == str(workspace)
    assert workspace.is_dir()
