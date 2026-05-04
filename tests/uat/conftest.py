"""Fixtures for installed/browser user-acceptance tests."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import requests

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Page, sync_playwright

from runtime.config import RuntimeConfig


REPO_ROOT = Path(__file__).resolve().parents[2]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "uat: installed/browser user acceptance tests")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    setattr(item, f"rep_{call.when}", outcome.get_result())


@dataclass
class UATServer:
    base_url: str
    config_path: Path
    data_dir: Path
    workspace: Path
    artifacts: Path
    process: subprocess.Popen | None = None

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return requests.get(self.base_url + path, timeout=15, **kwargs)

    def post(self, path: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> requests.Response:
        if payload is None and ("files" in kwargs or "data" in kwargs):
            return requests.post(self.base_url + path, timeout=30, **kwargs)
        return requests.post(self.base_url + path, json=payload or {}, timeout=30, **kwargs)

    def restart(self) -> None:
        self.stop()
        self.start()

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        port = int(self.base_url.rsplit(":", 1)[1])
        log_path = self.artifacts / "nur-web.log"
        log = log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["NUR_CONFIG_DIR"] = str(self.workspace / "config")
        cmd = [
            sys.executable,
            "-m",
            "interface.api",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--config",
            str(self.config_path),
        ]
        self.process = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        _wait_for_server(self.base_url, self.process, log_path)

    def stop(self) -> None:
        proc = self.process
        self.process = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


@pytest.fixture
def uat_artifacts(request: pytest.FixtureRequest) -> Path:
    root = Path(os.environ.get("NUR_UAT_ARTIFACTS", "reports/uat")).resolve()
    safe_name = request.node.nodeid.replace("/", "_").replace("::", "__").replace(" ", "_")
    path = root / safe_name
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def uat_server(tmp_path: Path, uat_artifacts: Path) -> UATServer:
    server = _make_server(tmp_path, uat_artifacts, setup_completed=True)
    server.start()
    try:
        yield server
    finally:
        server.stop()
        if os.environ.get("NUR_UAT_KEEP_WORKSPACE") != "1":
            shutil.rmtree(server.workspace, ignore_errors=True)


@pytest.fixture
def uat_server_first_run(tmp_path: Path, uat_artifacts: Path) -> UATServer:
    server = _make_server(tmp_path, uat_artifacts, setup_completed=False)
    server.start()
    try:
        yield server
    finally:
        server.stop()
        if os.environ.get("NUR_UAT_KEEP_WORKSPACE") != "1":
            shutil.rmtree(server.workspace, ignore_errors=True)


def _make_server(tmp_path: Path, uat_artifacts: Path, *, setup_completed: bool) -> UATServer:
    workspace = tmp_path / "nur-uat"
    data_dir = workspace / "data"
    config_dir = workspace / "config"
    data_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    config = _runtime_config(data_dir=data_dir)
    config_path = workspace / "runtime_config.yaml"
    config.write_yaml(str(config_path))
    if setup_completed:
        (data_dir / "admin_state.json").write_text(
            json.dumps({"setup_completed": True, "completed_at": time.time()}),
            encoding="utf-8",
        )

    port = _free_port()
    return UATServer(
        base_url=f"http://127.0.0.1:{port}",
        config_path=config_path,
        data_dir=data_dir,
        workspace=workspace,
        artifacts=uat_artifacts,
    )


@pytest.fixture
def page(uat_artifacts: Path, request: pytest.FixtureRequest):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=os.environ.get("NUR_UAT_HEADED") != "1")
        context = browser.new_context(viewport={"width": 1440, "height": 980})
        page = context.new_page()
        events: list[str] = []
        page.on("console", lambda msg: events.append(f"console:{msg.type}:{msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda exc: events.append(f"pageerror:{exc}"))
        page.on(
            "response",
            lambda res: events.append(f"http:{res.status}:{res.url}")
            if res.status >= 500
            else None,
        )
        page._nur_uat_events = events  # type: ignore[attr-defined]
        try:
            yield page
        finally:
            if getattr(request.node, "rep_call", None) and request.node.rep_call.failed:
                page.screenshot(path=str(uat_artifacts / "failure.png"), full_page=True)
            context.close()
            browser.close()


def assert_no_browser_errors(page: Page) -> None:
    events = [
        event for event in getattr(page, "_nur_uat_events", [])
        if "favicon.ico" not in event
    ]
    assert events == []


def expect_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise AssertionError(f"Response was not JSON: {response.status_code} {response.text[:300]}") from exc
    assert response.ok, data
    return data


def configured_uat_backend() -> str:
    return os.environ.get("NUR_UAT_BACKEND", "mock")


def is_live_uat() -> bool:
    return os.environ.get("NUR_UAT_LIVE") == "1"


def make_claude_style_skill(root: Path, *, name: str = "claude-video-helper") -> Path:
    """Create a representative skill folder with scripts and resources."""
    skill_root = root / name
    (skill_root / "scripts").mkdir(parents=True)
    (skill_root / "resources").mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        f"""---
name: {name}
description: Download and summarize video links for an operator.
---

Fetch URLs over https, download metadata, write files when explicitly asked,
and use shell commands only after review. Keep the final note concise.
""",
        encoding="utf-8",
    )
    (skill_root / "scripts" / "download.py").write_text(
        "print('download placeholder')\n",
        encoding="utf-8",
    )
    (skill_root / "resources" / "formats.md").write_text(
        "Prefer mp4 metadata summaries during UAT.\n",
        encoding="utf-8",
    )
    return skill_root


def zip_directory(source: Path, target: Path) -> Path:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(source.parent))
    return target


def _runtime_config(*, data_dir: Path) -> RuntimeConfig:
    backend = os.environ.get("NUR_UAT_BACKEND", "mock")
    live = os.environ.get("NUR_UAT_LIVE") == "1"
    config = RuntimeConfig(
        data_dir=str(data_dir),
        llm_backend=backend,
        tools_enabled=True,
        autonomy_level="assisted",
        shell_tool_enabled=False,
    )
    if backend in {"provider", "openai_compatible"}:
        api_key_env = os.environ.get("NUR_UAT_API_KEY_ENV", "LLM_API_KEY")
        config.llm_base_url = os.environ.get("NUR_UAT_BASE_URL", "")
        config.llm_model = os.environ.get("NUR_UAT_MODEL", "")
        config.llm_api_key = os.environ.get(api_key_env, "")
    elif backend == "minimax":
        api_key_env = os.environ.get("NUR_UAT_API_KEY_ENV", "LLM_API_KEY")
        config.llm_model = os.environ.get("NUR_UAT_MODEL", "")
        config.llm_api_key = os.environ.get(api_key_env, "")
    if live:
        _assert_live_config(config)
    return config


def _assert_live_config(config: RuntimeConfig) -> None:
    if config.llm_backend == "mock":
        pytest.fail("NUR_UAT_LIVE=1 requires a non-mock backend")
    if config.llm_backend in {"provider", "openai_compatible"}:
        if not config.llm_base_url or not config.llm_model or not config.llm_api_key:
            pytest.fail("Live provider UAT requires base URL, model, and API key")
    if config.llm_backend == "minimax":
        if not config.llm_model or not config.llm_api_key:
            pytest.fail("Live MiniMax UAT requires model and LLM_API_KEY")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.time() + 25
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            raise AssertionError(f"nur-web exited early with {proc.returncode}\n{log_text[-4000:]}")
        try:
            response = requests.get(base_url + "/v1/health", timeout=1)
            if response.ok:
                return
            last_error = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)
        time.sleep(0.2)
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    raise AssertionError(f"nur-web did not become ready: {last_error}\n{log_text[-4000:]}")
