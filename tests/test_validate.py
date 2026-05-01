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


def test_docs_drift_check_flags_stale_phrase(tmp_path):
    (tmp_path / "README.md").write_text("five-modulator\n", encoding="utf-8")

    try:
        validate._check_docs_drift(tmp_path)
    except validate.ValidationFailure as exc:
        assert "five-modulator" in str(exc)
    else:
        raise AssertionError("expected stale phrase failure")


def test_docs_drift_check_allows_historical_changelog(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("1383 tests\n", encoding="utf-8")

    assert "phrase checks" in validate._check_docs_drift(tmp_path)
