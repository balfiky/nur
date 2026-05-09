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


def _write_repo(tmp_path: Path, *, version: str, badge_color: str, changelog_body: str,
                pyproject_extra: str = "") -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "project-nur"\nversion = "{version}"\n\n'
        "[tool.setuptools.package-data]\n"
        f'interface = ["static/*"{pyproject_extra}]\n'
        'config = ["*.yaml", "prompts/*.md"]\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        f"![](https://img.shields.io/badge/version-{version}-{badge_color}?style=for-the-badge)\n"
        f"Version `{version}`.\n",
        encoding="utf-8",
    )
    (tmp_path / "CHANGELOG.md").write_text(changelog_body, encoding="utf-8")


def test_version_consistency_accepts_any_badge_color(tmp_path):
    _write_repo(
        tmp_path,
        version="0.28.13",
        badge_color="1A1428",
        changelog_body="## Unreleased\n\n- something new\n",
    )

    assert validate._check_version_consistency(tmp_path) == "0.28.13"


def test_version_consistency_accepts_unreleased_section(tmp_path):
    _write_repo(
        tmp_path,
        version="0.28.13",
        badge_color="informational",
        changelog_body="## Unreleased\n\n- queued change\n",
    )

    assert validate._check_version_consistency(tmp_path) == "0.28.13"


def test_version_consistency_accepts_released_heading(tmp_path):
    _write_repo(
        tmp_path,
        version="0.28.13",
        badge_color="2A8F6E",
        changelog_body="## v0.28.13 — 2026-05-09 (release notes)\n\n- shipped\n",
    )

    assert validate._check_version_consistency(tmp_path) == "0.28.13"


def test_version_consistency_rejects_missing_badge(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "project-nur"\nversion = "0.28.13"\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("Version `0.28.13`.\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("## Unreleased\n", encoding="utf-8")

    try:
        validate._check_version_consistency(tmp_path)
    except validate.ValidationFailure as exc:
        assert "badge" in str(exc).lower()
    else:
        raise AssertionError("expected badge failure")


def test_version_consistency_rejects_missing_changelog_section(tmp_path):
    _write_repo(
        tmp_path,
        version="0.28.13",
        badge_color="1A1428",
        changelog_body="# Old log\n\nno headings\n",
    )

    try:
        validate._check_version_consistency(tmp_path)
    except validate.ValidationFailure as exc:
        assert "CHANGELOG" in str(exc)
    else:
        raise AssertionError("expected changelog failure")


def test_package_data_accepts_extended_interface_list(tmp_path):
    for path in validate.EXPECTED_PACKAGE_FILES:
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools.package-data]\n'
        'interface = ["static/*", "static/lib/*"]\n'
        'config = ["*.yaml", "prompts/*.md"]\n',
        encoding="utf-8",
    )

    detail = validate._check_package_data_sources(tmp_path)
    assert "files" in detail


def test_package_data_rejects_missing_static_glob(tmp_path):
    for path in validate.EXPECTED_PACKAGE_FILES:
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.setuptools.package-data]\n'
        'interface = ["other/*"]\n'
        'config = ["*.yaml", "prompts/*.md"]\n',
        encoding="utf-8",
    )

    try:
        validate._check_package_data_sources(tmp_path)
    except validate.ValidationFailure as exc:
        assert "static" in str(exc)
    else:
        raise AssertionError("expected interface failure")
