"""Repository and release validation for Project Nūr."""

from __future__ import annotations

import argparse
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


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_PYTHONS = ("3.10", "3.11", "3.12")
EXPECTED_CONSOLE_SCRIPTS = {
    "nur": "main:main",
    "nur-setup": "interface.setup:main",
    "nur-uninstall": "interface.uninstall:main",
    "nur-web": "interface.api:main",
    "nur-emotional-qa": "evals.emotional_quality:main",
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
    "docs/ARCHITECTURE.md",
    "docs/DEPLOYMENT_AND_ADMIN.md",
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

    checks = [
        ("python version", lambda: _check_python_version()),
        ("version and changelog consistency", lambda: _check_version_consistency(root)),
        ("runtime safe defaults", lambda: _check_runtime_safe_defaults(root)),
        ("package data source files", lambda: _check_package_data_sources(root)),
        ("console script metadata", lambda: _check_console_scripts(root)),
        ("public documentation surface", lambda: _check_docs(root)),
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
            "Run neutral repository validation checks. Use --mode release before "
            "cutting or verifying a tag."
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
    parser.add_argument("--root", default=str(ROOT), help="Repository root.")
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


def _check_version_consistency(root: Path) -> str:
    version = _project_version(root)
    readme = _read(root / "README.md")
    changelog = _read(root / "CHANGELOG.md")
    _require(f"version-{version}-informational" in readme, "README badge version mismatch.")
    _require(f"Version `{version}`" in readme, "README current-status version mismatch.")
    _require(f"## v{version} " in changelog, "CHANGELOG missing current release heading.")
    return version


def _check_runtime_safe_defaults(root: Path) -> str:
    cfg = RuntimeConfig.from_yaml(str(root / "runtime_config.example.yaml"))
    failures = []
    if cfg.llm_backend != "mock":
        failures.append("example llm_backend must default to mock")
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
    _require('interface = ["static/*"]' in pyproject, "pyproject must ship interface/static/*.")
    _require('config = ["*.yaml", "prompts/*.md"]' in pyproject, "pyproject must ship config data.")
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
        venv_dir = Path(tmp) / "venv"
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        python = _venv_python(venv_dir)
        _run([str(python), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
        _run([str(python), "-m", "pip", "install", str(wheel_path)], cwd=root)
        _run([str(python), "-c", "import interface.api, runtime.learning_intake"], cwd=root)
        bin_dir = "Scripts" if os.name == "nt" else "bin"
        commands = [
            [str(venv_dir / bin_dir / "nur"), "--version"],
            [str(venv_dir / bin_dir / "nur-web"), "--version"],
            [str(venv_dir / bin_dir / "nur-setup"), "--help"],
            [str(venv_dir / bin_dir / "nur-uninstall"), "--help"],
            [str(venv_dir / bin_dir / "nur-emotional-qa"), "--help"],
            [str(venv_dir / bin_dir / "nur-validate"), "--help"],
        ]
        for cmd in commands:
            _run(cmd, cwd=root)
    return "entry points import and respond"


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
