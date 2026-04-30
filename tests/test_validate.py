from __future__ import annotations

from pathlib import Path

from nur_tools import validate


def test_auto_target_uses_repo_when_root_has_project_files(tmp_path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "project-nur"\n',
        encoding="utf-8",
    )

    assert validate._resolve_target("auto", tmp_path) == "repo"


def test_auto_target_uses_install_for_plain_workspace(tmp_path):
    (tmp_path / "runtime_config.yaml").write_text("llm_backend: mock\n", encoding="utf-8")

    assert validate._resolve_target("auto", tmp_path) == "install"


def test_workspace_runtime_config_missing_is_nonfatal(tmp_path):
    detail = validate._check_workspace_runtime_config(tmp_path)

    assert "runtime_config.yaml not found" in detail
