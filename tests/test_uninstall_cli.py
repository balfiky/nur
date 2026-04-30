from __future__ import annotations

from pathlib import Path

import pytest

from interface.uninstall import build_uninstall_plan, execute_plan
from runtime.config import RuntimeConfig


def test_uninstall_plan_removes_config_and_data(tmp_path):
    config_path = tmp_path / "runtime_config.yaml"
    data_dir = tmp_path / "data"
    RuntimeConfig(data_dir=str(data_dir)).write_yaml(str(config_path))
    data_dir.mkdir()
    (data_dir / "memory.txt").write_text("memory", encoding="utf-8")

    plan = build_uninstall_plan(config_path=config_path)

    assert (config_path, "config") in {(item.path, item.kind) for item in plan.remove}
    assert (data_dir.resolve(), "data") in {(item.path, item.kind) for item in plan.remove}

    removed = execute_plan(plan)

    assert {item.kind for item in removed} == {"config", "data"}
    assert not config_path.exists()
    assert not data_dir.exists()


def test_uninstall_dry_run_removes_nothing(tmp_path):
    config_path = tmp_path / "runtime_config.yaml"
    data_dir = tmp_path / "data"
    RuntimeConfig(data_dir=str(data_dir)).write_yaml(str(config_path))
    data_dir.mkdir()

    plan = build_uninstall_plan(config_path=config_path)
    removed = execute_plan(plan, dry_run=True)

    assert removed == []
    assert config_path.exists()
    assert data_dir.exists()


def test_uninstall_skips_external_workspace_by_default(tmp_path):
    config_path = tmp_path / "runtime_config.yaml"
    data_dir = tmp_path / "data"
    external_workspace = tmp_path / "workspace"
    RuntimeConfig(
        data_dir=str(data_dir),
        tools_workspace=str(external_workspace),
    ).write_yaml(str(config_path))
    data_dir.mkdir()
    external_workspace.mkdir()

    plan = build_uninstall_plan(config_path=config_path)

    assert "workspace" not in {item.kind for item in plan.remove}
    assert (external_workspace.resolve(), "workspace") in {
        (item.path, item.kind) for item in plan.skipped
    }


def test_uninstall_removes_identity_only_when_requested(tmp_path):
    config_path = tmp_path / "runtime_config.yaml"
    data_dir = tmp_path / "data"
    identity_dir = tmp_path / "identity"
    identity_dir.mkdir()
    soul_path = identity_dir / "soul.yaml"
    soul_path.write_text("soul:\n  name: Test\n", encoding="utf-8")
    RuntimeConfig(data_dir=str(data_dir)).write_yaml(str(config_path))
    data_dir.mkdir()
    env = {"NUR_CONFIG_DIR": str(identity_dir)}

    default_plan = build_uninstall_plan(config_path=config_path, environ=env)
    assert "identity" not in {item.kind for item in default_plan.remove}

    full_plan = build_uninstall_plan(
        config_path=config_path,
        remove_identity=True,
        environ=env,
    )
    assert (soul_path.resolve(), "identity") in {
        (item.path, item.kind) for item in full_plan.remove
    }


def test_uninstall_refuses_home_directory(tmp_path):
    config_path = tmp_path / "runtime_config.yaml"
    RuntimeConfig(data_dir=str(Path.home())).write_yaml(str(config_path))

    plan = build_uninstall_plan(config_path=config_path)

    with pytest.raises(SystemExit) as exc:
        execute_plan(plan)

    assert "Refusing to remove dangerous data path" in str(exc.value)
    assert config_path.exists()
