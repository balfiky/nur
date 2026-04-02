"""Tests for Agentic Tools Phase 1: executor, filesystem, shell, web, registration."""

from __future__ import annotations

import os
import tempfile
import textwrap

import pytest

from core.types import ToolCapability, ToolCategory, ToolResult
from tools.registry import ToolRegistry
from tools.executor import ToolExecutor
from tools.builtin.filesystem import CAPABILITIES as FS_CAPS, HANDLERS as FS_HANDLERS
from tools.builtin.shell import CAPABILITIES as SHELL_CAPS, HANDLERS as SHELL_HANDLERS
from tools.builtin.web_search import (
    CAPABILITIES as WEB_CAPS,
    WebProvider,
    NullWebProvider,
    create_handlers,
)
from tools import register_builtins


# ===================================================================
# Helpers
# ===================================================================

def _make_executor_with_builtins(web_provider=None):
    """Create a registry + executor with all builtins registered."""
    reg = ToolRegistry()
    exe = ToolExecutor(reg)
    register_builtins(reg, exe, web_provider=web_provider)
    return reg, exe


class FakeWebProvider:
    """Deterministic web provider for testing."""

    def __init__(self, search_results=None, fetch_text=""):
        self._search_results = search_results or []
        self._fetch_text = fetch_text

    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        return self._search_results[:limit]

    def fetch(self, url: str) -> str:
        return self._fetch_text


class ErrorWebProvider:
    """Provider that always raises."""

    def search(self, query: str, limit: int):
        raise ConnectionError("Network unreachable")

    def fetch(self, url: str):
        raise ConnectionError("Network unreachable")


# ===================================================================
# Executor
# ===================================================================

class TestExecutor:
    def test_success_path(self):
        reg = ToolRegistry()
        cap = ToolCapability(name="t", description="t", category=ToolCategory.READ_ONLY)
        reg.register(cap)
        exe = ToolExecutor(reg)
        exe.register_handler("t", lambda args: ToolResult(
            tool_name="t", success=True, output="ok",
        ))
        result = exe.execute("t", {})
        assert result.success is True
        assert result.output == "ok"
        assert result.latency_ms > 0

    def test_unknown_tool(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        result = exe.execute("nonexistent", {})
        assert result.success is False
        assert "Unknown tool" in result.error

    def test_no_handler(self):
        reg = ToolRegistry()
        reg.register(ToolCapability(name="t", description="t", category=ToolCategory.READ_ONLY))
        exe = ToolExecutor(reg)
        result = exe.execute("t", {})
        assert result.success is False
        assert "No handler" in result.error

    def test_handler_exception_normalized(self):
        reg = ToolRegistry()
        reg.register(ToolCapability(name="t", description="t", category=ToolCategory.READ_ONLY))
        exe = ToolExecutor(reg)
        exe.register_handler("t", lambda args: (_ for _ in ()).throw(ValueError("boom")))
        result = exe.execute("t", {})
        assert result.success is False
        assert "ValueError" in result.error
        assert "boom" in result.error
        assert result.latency_ms >= 0

    def test_latency_populated(self):
        reg = ToolRegistry()
        reg.register(ToolCapability(name="t", description="t", category=ToolCategory.READ_ONLY))
        exe = ToolExecutor(reg)
        exe.register_handler("t", lambda args: ToolResult(
            tool_name="t", success=True, output="ok",
        ))
        result = exe.execute("t", {})
        assert result.latency_ms > 0


# ===================================================================
# Filesystem tool
# ===================================================================

class TestFilesystemReadFile:
    def test_read_existing(self, tmp_path):
        p = tmp_path / "hello.txt"
        p.write_text("world")
        result = FS_HANDLERS["fs.read_file"]({"path": str(p)})
        assert result.success is True
        assert result.output == "world"
        assert result.metadata["size_bytes"] == 5

    def test_read_missing(self, tmp_path):
        result = FS_HANDLERS["fs.read_file"]({"path": str(tmp_path / "nope")})
        assert result.success is False
        assert "Not a file" in result.error

    def test_read_directory_fails(self, tmp_path):
        result = FS_HANDLERS["fs.read_file"]({"path": str(tmp_path)})
        assert result.success is False


class TestFilesystemListDir:
    def test_list_entries(self, tmp_path):
        (tmp_path / "a.txt").touch()
        (tmp_path / "b.txt").touch()
        (tmp_path / "subdir").mkdir()
        result = FS_HANDLERS["fs.list_dir"]({"path": str(tmp_path)})
        assert result.success is True
        entries = result.output.split("\n")
        assert "a.txt" in entries
        assert "b.txt" in entries
        assert "subdir" in entries
        assert result.metadata["count"] == 3

    def test_list_missing_dir(self):
        result = FS_HANDLERS["fs.list_dir"]({"path": "/nonexistent_xyz"})
        assert result.success is False

    def test_list_empty_dir(self, tmp_path):
        result = FS_HANDLERS["fs.list_dir"]({"path": str(tmp_path)})
        assert result.success is True
        assert result.output == ""
        assert result.metadata["count"] == 0


class TestFilesystemSearchText:
    def test_search_finds_matches(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello world\nfoo bar\nhello again")
        result = FS_HANDLERS["fs.search_text"]({"path": str(tmp_path), "pattern": "hello"})
        assert result.success is True
        assert result.metadata["match_count"] == 2

    def test_search_no_matches(self, tmp_path):
        (tmp_path / "a.txt").write_text("nothing here")
        result = FS_HANDLERS["fs.search_text"]({"path": str(tmp_path), "pattern": "zzz"})
        assert result.success is True
        assert result.metadata["match_count"] == 0

    def test_search_single_file(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("line1\nmatching_line\nline3")
        result = FS_HANDLERS["fs.search_text"]({"path": str(f), "pattern": "matching"})
        assert result.success is True
        assert result.metadata["match_count"] == 1
        assert ":2:" in result.output

    def test_search_invalid_regex(self, tmp_path):
        result = FS_HANDLERS["fs.search_text"]({"path": str(tmp_path), "pattern": "[invalid"})
        assert result.success is False
        assert "Invalid regex" in result.error

    def test_search_missing_path(self):
        result = FS_HANDLERS["fs.search_text"]({"path": "/nonexistent_xyz", "pattern": "x"})
        assert result.success is False


class TestFilesystemGlobPaths:
    def test_glob_match(self, tmp_path):
        (tmp_path / "a.py").touch()
        (tmp_path / "b.py").touch()
        (tmp_path / "c.txt").touch()
        result = FS_HANDLERS["fs.glob_paths"]({"path": str(tmp_path), "pattern": "*.py"})
        assert result.success is True
        assert result.metadata["match_count"] == 2

    def test_glob_no_match(self, tmp_path):
        (tmp_path / "a.txt").touch()
        result = FS_HANDLERS["fs.glob_paths"]({"path": str(tmp_path), "pattern": "*.rs"})
        assert result.success is True
        assert result.metadata["match_count"] == 0

    def test_glob_not_a_dir(self):
        result = FS_HANDLERS["fs.glob_paths"]({"path": "/nonexistent_xyz", "pattern": "*"})
        assert result.success is False


class TestFilesystemWriteFile:
    def test_write_new(self, tmp_path):
        target = str(tmp_path / "out.txt")
        result = FS_HANDLERS["fs.write_file"]({"path": target, "content": "data"})
        assert result.success is True
        assert os.path.isfile(target)
        assert open(target).read() == "data"
        assert result.metadata["bytes_written"] == 4
        assert "created/overwritten" in result.side_effect_summary

    def test_write_creates_parent_dirs(self, tmp_path):
        target = str(tmp_path / "sub" / "deep" / "out.txt")
        result = FS_HANDLERS["fs.write_file"]({"path": target, "content": "x"})
        assert result.success is True
        assert os.path.isfile(target)

    def test_write_overwrites(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("old")
        result = FS_HANDLERS["fs.write_file"]({"path": str(target), "content": "new"})
        assert result.success is True
        assert target.read_text() == "new"


class TestFilesystemDeletePath:
    def test_delete_file(self, tmp_path):
        f = tmp_path / "gone.txt"
        f.write_text("bye")
        result = FS_HANDLERS["fs.delete_path"]({"path": str(f)})
        assert result.success is True
        assert not f.exists()
        assert "deleted file" in result.side_effect_summary

    def test_delete_directory(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        (d / "inside.txt").write_text("x")
        result = FS_HANDLERS["fs.delete_path"]({"path": str(d)})
        assert result.success is True
        assert not d.exists()
        assert "deleted directory" in result.side_effect_summary

    def test_delete_missing(self):
        result = FS_HANDLERS["fs.delete_path"]({"path": "/nonexistent_xyz"})
        assert result.success is False
        assert "not found" in result.error.lower()


# ===================================================================
# Shell tool
# ===================================================================

class TestShellRunCommand:
    def test_success(self):
        result = SHELL_HANDLERS["shell.run_command"]({"cmd": "echo hello"})
        assert result.success is True
        assert "hello" in result.output
        assert result.metadata["exit_code"] == 0

    def test_non_zero_exit(self):
        result = SHELL_HANDLERS["shell.run_command"]({"cmd": "exit 42"})
        assert result.success is False
        assert result.metadata["exit_code"] == 42
        assert "Exit code 42" in result.error

    def test_stderr_captured(self):
        result = SHELL_HANDLERS["shell.run_command"]({"cmd": "echo err >&2"})
        assert "[stderr]" in result.output
        assert "err" in result.output

    def test_timeout(self):
        result = SHELL_HANDLERS["shell.run_command"]({
            "cmd": "sleep 60",
            "timeout_seconds": 1,
        })
        assert result.success is False
        assert "timed out" in result.error.lower()

    def test_cwd(self, tmp_path):
        result = SHELL_HANDLERS["shell.run_command"]({
            "cmd": "pwd",
            "cwd": str(tmp_path),
        })
        assert result.success is True
        assert str(tmp_path) in result.output

    def test_default_timeout(self):
        """Default timeout is 30s — just verify the handler doesn't crash."""
        result = SHELL_HANDLERS["shell.run_command"]({"cmd": "true"})
        assert result.success is True


# ===================================================================
# Web search/fetch tool
# ===================================================================

class TestWebSearch:
    def test_search_with_fake_provider(self):
        prov = FakeWebProvider(search_results=[
            {"title": "Result 1", "url": "https://example.com/1"},
            {"title": "Result 2", "url": "https://example.com/2"},
        ])
        handlers = create_handlers(prov)
        result = handlers["web.search"]({"query": "test", "limit": 5})
        assert result.success is True
        assert "Result 1" in result.output
        assert "Result 2" in result.output
        assert result.metadata["result_count"] == 2

    def test_search_respects_limit(self):
        prov = FakeWebProvider(search_results=[
            {"title": f"R{i}", "url": f"https://x.com/{i}"} for i in range(10)
        ])
        handlers = create_handlers(prov)
        result = handlers["web.search"]({"query": "test", "limit": 3})
        assert result.metadata["result_count"] == 3

    def test_search_no_results(self):
        prov = FakeWebProvider(search_results=[])
        handlers = create_handlers(prov)
        result = handlers["web.search"]({"query": "nothing"})
        assert result.success is True
        assert "(no results)" in result.output
        assert result.metadata["result_count"] == 0

    def test_fetch_with_fake_provider(self):
        prov = FakeWebProvider(fetch_text="<html>Hello</html>")
        handlers = create_handlers(prov)
        result = handlers["web.fetch"]({"url": "https://example.com"})
        assert result.success is True
        assert result.output == "<html>Hello</html>"
        assert result.metadata["length"] == 18

    def test_null_provider_errors(self):
        """NullWebProvider raises, which the executor would normalize."""
        prov = NullWebProvider()
        with pytest.raises(RuntimeError, match="No web provider"):
            prov.search("q", 5)
        with pytest.raises(RuntimeError, match="No web provider"):
            prov.fetch("http://x")

    def test_error_provider_normalized_by_executor(self):
        """Provider errors are normalized into ToolResult by executor."""
        reg, exe = _make_executor_with_builtins(web_provider=ErrorWebProvider())
        result = exe.execute("web.search", {"query": "test"})
        assert result.success is False
        assert "ConnectionError" in result.error

    def test_default_handlers_error(self):
        """Default HANDLERS use NullWebProvider — errors when called."""
        from tools.builtin.web_search import HANDLERS
        # Direct call raises; the executor would normalize this
        with pytest.raises(RuntimeError):
            HANDLERS["web.search"]({"query": "test"})


# ===================================================================
# Builtin registration
# ===================================================================

class TestBuiltinRegistration:
    def test_register_builtins_populates_registry(self):
        reg, exe = _make_executor_with_builtins()
        names = reg.names()
        assert "fs.read_file" in names
        assert "fs.list_dir" in names
        assert "fs.search_text" in names
        assert "fs.glob_paths" in names
        assert "fs.write_file" in names
        assert "fs.delete_path" in names
        assert "shell.run_command" in names
        assert "web.search" in names
        assert "web.fetch" in names

    def test_total_builtin_count(self):
        reg, exe = _make_executor_with_builtins()
        assert len(reg) == 18  # 6 fs + 1 shell + 3 web + 5 browser + 3 calendar

    def test_categories_assigned(self):
        reg, _ = _make_executor_with_builtins()
        assert reg.get("fs.read_file").category == ToolCategory.READ_ONLY
        assert reg.get("fs.write_file").category == ToolCategory.WRITE
        assert reg.get("fs.delete_path").category == ToolCategory.DESTRUCTIVE
        assert reg.get("shell.run_command").category == ToolCategory.WRITE
        assert reg.get("web.search").category == ToolCategory.READ_ONLY
        assert reg.get("web.search").requires_network is True

    def test_executor_can_run_registered_tools(self, tmp_path):
        """End-to-end: register builtins then execute a filesystem tool."""
        reg, exe = _make_executor_with_builtins()
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = exe.execute("fs.read_file", {"path": str(f)})
        assert result.success is True
        assert result.output == "content"

    def test_custom_web_provider_wired(self):
        prov = FakeWebProvider(search_results=[{"title": "X", "url": "http://x"}])
        reg, exe = _make_executor_with_builtins(web_provider=prov)
        result = exe.execute("web.search", {"query": "q"})
        assert result.success is True
        assert "X" in result.output


# ===================================================================
# Integration: executor + filesystem end-to-end
# ===================================================================

class TestExecutorFilesystemIntegration:
    def test_write_then_read(self, tmp_path):
        reg, exe = _make_executor_with_builtins()
        target = str(tmp_path / "roundtrip.txt")
        w = exe.execute("fs.write_file", {"path": target, "content": "hello"})
        assert w.success is True
        r = exe.execute("fs.read_file", {"path": target})
        assert r.success is True
        assert r.output == "hello"

    def test_write_list_delete(self, tmp_path):
        reg, exe = _make_executor_with_builtins()
        f = str(tmp_path / "temp.txt")
        exe.execute("fs.write_file", {"path": f, "content": "x"})
        ls = exe.execute("fs.list_dir", {"path": str(tmp_path)})
        assert "temp.txt" in ls.output
        exe.execute("fs.delete_path", {"path": f})
        ls2 = exe.execute("fs.list_dir", {"path": str(tmp_path)})
        assert "temp.txt" not in ls2.output
