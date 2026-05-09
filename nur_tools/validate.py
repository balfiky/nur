"""Repository and release validation for Project Nūr."""

from __future__ import annotations

import argparse
from importlib import metadata
from importlib import resources
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from dataclasses import dataclass
from pathlib import Path

from runtime.config import RuntimeConfig


PROJECT_NAME = "project-nur"
REQUIRED_PYTHONS = ("3.10", "3.11", "3.12")
EXPECTED_CONSOLE_SCRIPTS = {
    "nur": "main:main",
    "nur-setup": "interface.setup:main",
    "nur-uninstall": "interface.uninstall:main",
    "nur-web": "interface.api:main",
    "nur-emotional-qa": "evals.emotional_quality:main",
    "nur-uat": "nur_tools.uat:main",
    "nur-validate": "nur_tools.validate:main",
}
EXPECTED_PACKAGE_FILES = {
    "interface/static/index.html",
    "interface/static/admin.html",
    "interface/static/admin.js",
    "interface/static/admin.css",
    "config/soul.yaml",
    "config/modulators.yaml",
    "config/attachment.yaml",
    "config/values_seed.yaml",
    "config/semantic_memory.yaml",
    "config/prompts/generator.md",
    "config/prompts/self_check.md",
    "config/prompts/digestion.md",
}
EXPECTED_DOCS = {
    "README.md",
    "CHANGELOG.md",
    "PRIVACY.md",
    "SECURITY.md",
    "docs/OVERVIEW.md",
    "docs/STUDY_GUIDE.md",
    "docs/ARCHITECTURE.md",
    "docs/DEPLOYMENT_AND_ADMIN.md",
}
STALE_PHRASES = {
    "five-modulator": ("CHANGELOG.md",),
    "v0.26.1": ("CHANGELOG.md",),
    "1383 tests": ("CHANGELOG.md",),
    "pip install project-nur": ("CHANGELOG.md", "docs/DEPLOYMENT_AND_ADMIN.md"),
}
DRIFT_CHECK_LIVE_FILES = {
    "README.md",
    "CONTRIBUTING.md",
    "docs/OVERVIEW.md",
    "docs/ARCHITECTURE.md",
    "docs/DEPLOYMENT_AND_ADMIN.md",
}
DRIFT_CHECK_EXCLUDED_FILES = {
    "nur_tools/validate.py",
    "tests/test_validate.py",
}
FORBIDDEN_WHEEL_PREFIXES = ("tools/",)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


class ValidationFailure(AssertionError):
    """Raised by individual validation checks."""


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    root = Path(args.root).resolve()
    results: list[CheckResult] = []
    target = _resolve_target(args.target, root)

    if target == "install":
        if args.mode == "release":
            results.append(CheckResult(
                name="validation target",
                ok=False,
                detail="release validation requires a source checkout, not an installed package",
            ))
            _print_results(results)
            raise SystemExit(1)
        results.extend(_run_install_checks(root))
        _print_results(results)
        if any(not result.ok for result in results):
            raise SystemExit(1)
        return

    checks = [
        ("validation target", lambda: "source checkout"),
        ("python version", lambda: _check_python_version()),
        ("version and changelog consistency", lambda: _check_version_consistency(root)),
        ("runtime safe defaults", lambda: _check_runtime_safe_defaults(root)),
        ("package data source files", lambda: _check_package_data_sources(root)),
        ("console script metadata", lambda: _check_console_scripts(root)),
        ("public documentation surface", lambda: _check_docs(root)),
        ("documentation drift phrases", lambda: _check_docs_drift(root)),
        ("CI workflow coverage", lambda: _check_ci_workflow(root)),
        ("whitespace diff", lambda: _check_git_diff_whitespace(root)),
        ("critical imports", lambda: _check_critical_imports()),
    ]
    for name, check in checks:
        results.append(_run_named_check(name, check))

    if args.mode in {"full", "release"} and not args.skip_tests:
        results.append(_run_named_check("pytest suite", lambda: _run_pytest(root)))

    wheel_path: Path | None = None
    if args.mode in {"ci", "full", "release"} and not args.skip_build:
        build_result = _run_named_check("build wheel and sdist", lambda: _build_package(root))
        results.append(build_result)
        if build_result.ok:
            wheel_path = Path(build_result.detail)
            results.append(
                _run_named_check("wheel package data", lambda: _check_wheel_contents(wheel_path))
            )

    if args.mode == "release":
        results.append(
            _run_named_check(
                "release git state",
                lambda: _check_release_git_state(root, strict_untracked=args.strict_untracked),
            )
        )
        if wheel_path is not None and not args.skip_wheel_smoke:
            results.append(
                _run_named_check(
                    "wheel install smoke",
                    lambda: _smoke_install_wheel(root, wheel_path),
                )
            )

    _print_results(results)
    if any(not result.ok for result in results):
        raise SystemExit(1)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nur-validate",
        description=(
            "Run neutral validation checks. In a source checkout this validates "
            "repository/release readiness; in an installed workspace it validates "
            "the installed package and local runtime config."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["quick", "ci", "full", "release"],
        default="full",
        help=(
            "quick=static checks, ci=static+package build, full=ci+pytest, "
            "release=full+release/tag/wheel install checks."
        ),
    )
    parser.add_argument(
        "--target",
        choices=["auto", "repo", "install"],
        default="auto",
        help="Validation target. auto uses repo checks only when --root looks like a checkout.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository/workspace root (default: current directory).",
    )
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest in full/release mode.")
    parser.add_argument("--skip-build", action="store_true", help="Skip wheel/sdist build checks.")
    parser.add_argument(
        "--skip-wheel-smoke",
        action="store_true",
        help="Skip temporary virtualenv install smoke in release mode.",
    )
    parser.add_argument(
        "--strict-untracked",
        action="store_true",
        help="Fail release mode when untracked files exist.",
    )
    return parser.parse_args(argv)


def _resolve_target(target: str, root: Path) -> str:
    if target == "repo":
        if not _looks_like_repo_root(root):
            raise SystemExit(f"{root} does not look like a Project Nūr source checkout.")
        return "repo"
    if target == "install":
        return "install"
    return "repo" if _looks_like_repo_root(root) else "install"


def _looks_like_repo_root(root: Path) -> bool:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return 'name = "project-nur"' in text and (root / "runtime").is_dir()


def _run_install_checks(root: Path) -> list[CheckResult]:
    checks = [
        ("validation target", lambda: "installed package/workspace"),
        ("python version", lambda: _check_python_version()),
        ("installed package version", lambda: _check_installed_version()),
        ("installed package resources", lambda: _check_installed_resources()),
        ("installed console scripts", lambda: _check_installed_console_scripts()),
        ("workspace runtime config", lambda: _check_workspace_runtime_config(root)),
        ("critical imports", lambda: _check_critical_imports()),
    ]
    return [_run_named_check(name, check) for name, check in checks]


def _run_named_check(name: str, check) -> CheckResult:
    try:
        detail = check() or ""
        return CheckResult(name=name, ok=True, detail=str(detail))
    except ValidationFailure as exc:
        return CheckResult(name=name, ok=False, detail=str(exc))
    except Exception as exc:
        return CheckResult(name=name, ok=False, detail=f"{exc.__class__.__name__}: {exc}")


def _check_python_version() -> str:
    current = sys.version_info
    if current < (3, 10):
        raise ValidationFailure("Python 3.10+ is required.")
    return f"{current.major}.{current.minor}.{current.micro}"


def _check_installed_version() -> str:
    try:
        return metadata.version(PROJECT_NAME)
    except metadata.PackageNotFoundError as exc:
        raise ValidationFailure(f"{PROJECT_NAME} is not installed in this environment") from exc


def _check_installed_resources() -> str:
    missing = []
    for rel_path in sorted(EXPECTED_PACKAGE_FILES):
        package_name, *parts = rel_path.split("/")
        try:
            resource = resources.files(package_name).joinpath(*parts)
            if not resource.is_file():
                missing.append(rel_path)
        except (ModuleNotFoundError, FileNotFoundError):
            missing.append(rel_path)
    if missing:
        raise ValidationFailure("missing installed resources: " + ", ".join(missing))
    return f"{len(EXPECTED_PACKAGE_FILES)} files"


def _check_installed_console_scripts() -> str:
    try:
        entry_points = metadata.entry_points(group="console_scripts")
    except TypeError:
        entry_points = metadata.entry_points().get("console_scripts", [])
    found = {entry.name: entry.value for entry in entry_points}
    missing = [
        f"{name} -> {target}"
        for name, target in EXPECTED_CONSOLE_SCRIPTS.items()
        if found.get(name) != target
    ]
    if missing:
        raise ValidationFailure("missing installed console scripts: " + ", ".join(missing))
    return f"{len(EXPECTED_CONSOLE_SCRIPTS)} scripts"


def _check_workspace_runtime_config(root: Path) -> str:
    path = root / "runtime_config.yaml"
    if not path.exists():
        return "runtime_config.yaml not found; run nur-setup if this is a new workspace"
    cfg = RuntimeConfig.from_yaml(str(path))
    warnings = []
    if cfg.tools_enabled and not cfg.api_key:
        warnings.append("tools enabled without api_key")
    if cfg.shell_tool_enabled and not cfg.api_key:
        warnings.append("shell enabled without api_key")
    if cfg.cors_origins:
        warnings.append("custom CORS origins configured")
    detail = f"llm_backend={cfg.llm_backend}, data_dir={cfg.data_dir}"
    if warnings:
        detail += "; warnings: " + ", ".join(warnings)
    return detail


def _check_version_consistency(root: Path) -> str:
    version = _project_version(root)
    readme = _read(root / "README.md")
    changelog = _read(root / "CHANGELOG.md")
    badge_pattern = re.compile(rf"version-{re.escape(version)}-[^?\"\s]+")
    _require(bool(badge_pattern.search(readme)), "README badge version mismatch.")
    _require(f"Version `{version}`" in readme, "README current-status version mismatch.")
    has_release_heading = f"## v{version} " in changelog or f"## v{version}\n" in changelog
    has_unreleased = "## Unreleased" in changelog
    _require(
        has_release_heading or has_unreleased,
        "CHANGELOG missing release heading or ## Unreleased section.",
    )
    return version


def _check_runtime_safe_defaults(root: Path) -> str:
    cfg = RuntimeConfig.from_yaml(str(root / "runtime_config.example.yaml"))
    failures = []
    if cfg.llm_backend == "mock":
        failures.append("example llm_backend must not be 'mock' — Nūr requires a real backend")
    if cfg.api_key:
        failures.append("example api_key must be empty")
    if cfg.telegram_token:
        failures.append("example telegram_token must be empty")
    if cfg.tools_enabled:
        failures.append("example tools_enabled must be false")
    if cfg.shell_tool_enabled:
        failures.append("example shell_tool_enabled must be false")
    if cfg.cors_origins:
        failures.append("example cors_origins must be empty")
    if failures:
        raise ValidationFailure("; ".join(failures))
    return "mock backend, no token, tools off, shell off"


def _check_package_data_sources(root: Path) -> str:
    missing = [path for path in sorted(EXPECTED_PACKAGE_FILES) if not (root / path).is_file()]
    if missing:
        raise ValidationFailure("missing package data sources: " + ", ".join(missing))
    pyproject = _read(root / "pyproject.toml")
    interface_match = re.search(r"^interface\s*=\s*\[(?P<items>[^\]]*)\]", pyproject, re.MULTILINE)
    _require(
        interface_match is not None and '"static/*"' in interface_match.group("items"),
        "pyproject must ship interface/static/*.",
    )
    config_match = re.search(r"^config\s*=\s*\[(?P<items>[^\]]*)\]", pyproject, re.MULTILINE)
    _require(
        config_match is not None
        and '"*.yaml"' in config_match.group("items")
        and '"prompts/*.md"' in config_match.group("items"),
        "pyproject must ship config data.",
    )
    return f"{len(EXPECTED_PACKAGE_FILES)} files"


def _check_console_scripts(root: Path) -> str:
    pyproject = _read(root / "pyproject.toml")
    missing = [
        f"{name} -> {target}"
        for name, target in EXPECTED_CONSOLE_SCRIPTS.items()
        if f'{name} = "{target}"' not in pyproject
    ]
    if missing:
        raise ValidationFailure("missing console script metadata: " + ", ".join(missing))
    return f"{len(EXPECTED_CONSOLE_SCRIPTS)} scripts"


def _check_docs(root: Path) -> str:
    missing = [path for path in sorted(EXPECTED_DOCS) if not (root / path).is_file()]
    if missing:
        raise ValidationFailure("missing public docs: " + ", ".join(missing))
    readme = _read(root / "README.md")
    for path in sorted(EXPECTED_DOCS - {"README.md"}):
        _require(path in readme, f"README does not link {path}.")
    return f"{len(EXPECTED_DOCS)} public docs"


def _check_docs_drift(root: Path) -> str:
    offenders: list[str] = []
    paths = [root / rel for rel in sorted(DRIFT_CHECK_LIVE_FILES)]
    core_dir = root / "core"
    if core_dir.is_dir():
        paths.extend(sorted(core_dir.rglob("*.py")))
    for path in paths:
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in {".md", ".py", ".html", ".css", ".js", ".yaml", ".yml", ".toml"}:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in DRIFT_CHECK_EXCLUDED_FILES:
            continue
        text = _read(path)
        for phrase, allowed_paths in STALE_PHRASES.items():
            if phrase not in text:
                continue
            if rel in allowed_paths:
                continue
            offenders.append(f"{rel}: {phrase}")
    if offenders:
        raise ValidationFailure("stale phrases found: " + ", ".join(offenders[:10]))
    return f"{len(STALE_PHRASES)} phrase checks"


def _check_ci_workflow(root: Path) -> str:
    workflow = _read(root / ".github/workflows/ci.yml")
    missing_versions = [version for version in REQUIRED_PYTHONS if version not in workflow]
    if missing_versions:
        raise ValidationFailure("CI missing Python versions: " + ", ".join(missing_versions))
    _require("python -m nur_tools.validate --mode ci" in workflow, "CI must run nur_tools.validate.")
    _require("python -m pytest" in workflow, "CI must run pytest.")
    return "validate + pytest on 3.10/3.11/3.12"


def _check_git_diff_whitespace(root: Path) -> str:
    _run(["git", "diff", "--check"], cwd=root)
    return "clean"


def _check_critical_imports() -> str:
    import interface.api  # noqa: F401
    import runtime.learning_intake  # noqa: F401
    import runtime.sessions.manager  # noqa: F401
    import runtime.skills  # noqa: F401

    return "interface/api, learning intake, sessions, skills"


def _run_pytest(root: Path) -> str:
    _run([sys.executable, "-m", "pytest", "-q"], cwd=root)
    return "pytest -q"


def _build_package(root: Path) -> str:
    _clean_build_artifacts(root)
    try:
        with tempfile.TemporaryDirectory(prefix="nur-build-") as tmp:
            out_dir = Path(tmp) / "dist"
            _run(
                [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(out_dir)],
                cwd=root,
            )
            wheels = sorted(out_dir.glob("*.whl"))
            sdists = sorted(out_dir.glob("*.tar.gz"))
            if len(wheels) != 1 or len(sdists) != 1:
                raise ValidationFailure("expected exactly one wheel and one sdist")
            persistent = root / ".validate-artifacts"
            if persistent.exists():
                shutil.rmtree(persistent)
            persistent.mkdir()
            wheel_copy = persistent / wheels[0].name
            shutil.copy2(wheels[0], wheel_copy)
            shutil.copy2(sdists[0], persistent / sdists[0].name)
            return str(wheel_copy)
    finally:
        _clean_build_artifacts(root)


def _check_wheel_contents(wheel_path: Path) -> str:
    if not wheel_path.is_file():
        raise ValidationFailure(f"wheel not found: {wheel_path}")
    with zipfile.ZipFile(wheel_path) as zf:
        names = set(zf.namelist())
        forbidden = [
            name
            for name in names
            if any(name.startswith(prefix) for prefix in FORBIDDEN_WHEEL_PREFIXES)
        ]
        if forbidden:
            raise ValidationFailure(
                "wheel contains stale/forbidden paths: " + ", ".join(sorted(forbidden)[:10])
            )
        missing = [path for path in EXPECTED_PACKAGE_FILES if path not in names]
        if missing:
            raise ValidationFailure("wheel missing package data: " + ", ".join(sorted(missing)))
        entry_points = _read_wheel_text(zf, ".dist-info/entry_points.txt")
    missing_scripts = [
        name
        for name, target in EXPECTED_CONSOLE_SCRIPTS.items()
        if f"{name} = {target}" not in entry_points
    ]
    if missing_scripts:
        raise ValidationFailure("wheel missing entry points: " + ", ".join(sorted(missing_scripts)))
    return wheel_path.name


def _check_release_git_state(root: Path, *, strict_untracked: bool) -> str:
    version = _project_version(root)
    _run(["git", "diff", "--quiet"], cwd=root)
    _run(["git", "diff", "--cached", "--quiet"], cwd=root)
    status = _run(["git", "status", "--porcelain"], cwd=root, capture=True).stdout.strip()
    if strict_untracked and status:
        raise ValidationFailure("working tree has untracked files:\n" + status)

    head = _run(["git", "rev-parse", "HEAD"], cwd=root, capture=True).stdout.strip()
    tag = f"v{version}"
    tagged = _run(["git", "rev-list", "-n", "1", tag], cwd=root, capture=True).stdout.strip()
    if head != tagged:
        raise ValidationFailure(f"{tag} does not point at HEAD")
    return tag


def _smoke_install_wheel(root: Path, wheel_path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="nur-wheel-smoke-") as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        (workspace / "runtime_config.yaml").write_text("llm_backend: mock\n", encoding="utf-8")
        venv_dir = Path(tmp) / "venv"
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        python = _venv_python(venv_dir)
        _run([str(python), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
        _run([str(python), "-m", "pip", "install", str(wheel_path)], cwd=root)
        _run([str(python), "-c", "import interface.api, runtime.learning_intake"], cwd=workspace)
        bin_dir = "Scripts" if os.name == "nt" else "bin"
        commands = [
            [str(venv_dir / bin_dir / "nur"), "--version"],
            [str(venv_dir / bin_dir / "nur-web"), "--version"],
            [str(venv_dir / bin_dir / "nur-setup"), "--help"],
            [str(venv_dir / bin_dir / "nur-uninstall"), "--help"],
            [str(venv_dir / bin_dir / "nur-emotional-qa"), "--help"],
            [str(venv_dir / bin_dir / "nur-validate"), "--help"],
            [
                str(venv_dir / bin_dir / "nur-validate"),
                "--mode",
                "quick",
                "--root",
                str(workspace),
            ],
        ]
        for cmd in commands:
            _run(cmd, cwd=workspace)
    return "entry points import, respond, and validate installed workspace"


def _print_results(results: list[CheckResult]) -> None:
    for result in results:
        marker = "OK" if result.ok else "FAIL"
        suffix = f" - {result.detail}" if result.detail else ""
        print(f"[{marker}] {result.name}{suffix}")


def _clean_build_artifacts(root: Path) -> None:
    for path in (root / "build", root / "project_nur.egg-info"):
        if path.exists():
            shutil.rmtree(path)


def _project_version(root: Path) -> str:
    pyproject = _read(root / "pyproject.toml")
    match = re.search(r'^version = "([^"]+)"', pyproject, flags=re.M)
    if not match:
        raise ValidationFailure("pyproject.toml has no project version.")
    return match.group(1)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValidationFailure(f"missing file: {path}") from exc


def _read_wheel_text(zf: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in zf.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise ValidationFailure(f"wheel must contain one {suffix}")
    return zf.read(matches[0]).decode("utf-8", errors="replace")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationFailure(message)


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            check=True,
            capture_output=capture,
        )
    except subprocess.CalledProcessError as exc:
        output = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
        if len(output) > 4000:
            output = output[-4000:]
        command = " ".join(cmd)
        raise ValidationFailure(
            f"{command} failed with exit code {exc.returncode}\n{output}".strip()
        ) from exc


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


if __name__ == "__main__":
    main()
