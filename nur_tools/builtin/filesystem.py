"""Builtin filesystem tool — bounded, explicit file operations.

Each operation is a standalone function that returns a ToolResult.
No autonomy here — just execution primitives.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
from typing import Any

from core.types import ToolCapability, ToolCategory, ToolResult
from nur_tools.executor import ToolHandler

# ---------------------------------------------------------------------------
# Capabilities (static descriptions for the registry)
# ---------------------------------------------------------------------------

CAPABILITIES: list[ToolCapability] = [
    ToolCapability(
        name="fs.read_file",
        description="Read the contents of a file",
        category=ToolCategory.READ_ONLY,
        arg_schema={"path": {"type": "string", "required": True}},
    ),
    ToolCapability(
        name="fs.list_dir",
        description="List entries in a directory",
        category=ToolCategory.READ_ONLY,
        arg_schema={"path": {"type": "string", "required": True}},
    ),
    ToolCapability(
        name="fs.search_text",
        description="Search for a regex pattern in files under a directory",
        category=ToolCategory.READ_ONLY,
        arg_schema={
            "path": {"type": "string", "required": True},
            "pattern": {"type": "string", "required": True},
        },
    ),
    ToolCapability(
        name="fs.glob_paths",
        description="Find files matching a glob pattern",
        category=ToolCategory.READ_ONLY,
        arg_schema={
            "path": {"type": "string", "required": True},
            "pattern": {"type": "string", "required": True},
        },
    ),
    ToolCapability(
        name="fs.write_file",
        description="Write content to a file (creates or overwrites)",
        category=ToolCategory.WRITE,
        arg_schema={
            "path": {"type": "string", "required": True},
            "content": {"type": "string", "required": True},
        },
    ),
    ToolCapability(
        name="fs.delete_path",
        description="Delete a file or directory",
        category=ToolCategory.DESTRUCTIVE,
        arg_schema={"path": {"type": "string", "required": True}},
    ),
]

# ---------------------------------------------------------------------------
# Max output size to avoid unbounded memory
# ---------------------------------------------------------------------------

_MAX_READ_BYTES = 1_000_000  # 1 MB
_MAX_SEARCH_MATCHES = 100


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _read_file(args: dict[str, Any]) -> ToolResult:
    path = args["path"]
    if not os.path.isfile(path):
        return ToolResult(
            tool_name="fs.read_file", success=False, output="",
            error=f"Not a file: {path}",
        )
    size = os.path.getsize(path)
    if size > _MAX_READ_BYTES:
        return ToolResult(
            tool_name="fs.read_file", success=False, output="",
            error=f"File too large: {size} bytes (max {_MAX_READ_BYTES})",
        )
    with open(path, "r", errors="replace") as f:
        content = f.read()
    return ToolResult(
        tool_name="fs.read_file", success=True, output=content,
        metadata={"size_bytes": size},
        side_effect_summary="none",
    )


def _list_dir(args: dict[str, Any]) -> ToolResult:
    path = args["path"]
    if not os.path.isdir(path):
        return ToolResult(
            tool_name="fs.list_dir", success=False, output="",
            error=f"Not a directory: {path}",
        )
    entries = sorted(os.listdir(path))
    output = "\n".join(entries)
    return ToolResult(
        tool_name="fs.list_dir", success=True, output=output,
        metadata={"count": len(entries)},
        side_effect_summary="none",
    )


def _search_text(args: dict[str, Any]) -> ToolResult:
    path = args["path"]
    pattern = args["pattern"]
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return ToolResult(
            tool_name="fs.search_text", success=False, output="",
            error=f"Invalid regex: {e}",
        )

    matches: list[str] = []
    if os.path.isfile(path):
        _search_file(path, regex, matches)
    elif os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for fname in sorted(files):
                if len(matches) >= _MAX_SEARCH_MATCHES:
                    break
                _search_file(os.path.join(root, fname), regex, matches)
    else:
        return ToolResult(
            tool_name="fs.search_text", success=False, output="",
            error=f"Path not found: {path}",
        )

    output = "\n".join(matches)
    return ToolResult(
        tool_name="fs.search_text", success=True, output=output,
        metadata={"match_count": len(matches)},
        side_effect_summary="none",
    )


def _search_file(filepath: str, regex: re.Pattern, matches: list[str]) -> None:
    """Search a single file, appending formatted matches."""
    try:
        with open(filepath, "r", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                if len(matches) >= _MAX_SEARCH_MATCHES:
                    return
                if regex.search(line):
                    matches.append(f"{filepath}:{lineno}: {line.rstrip()}")
    except (OSError, UnicodeDecodeError):
        pass


def _glob_paths(args: dict[str, Any]) -> ToolResult:
    path = args["path"]
    pattern = args["pattern"]
    if not os.path.isdir(path):
        return ToolResult(
            tool_name="fs.glob_paths", success=False, output="",
            error=f"Not a directory: {path}",
        )

    found: list[str] = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(dirs + files):
            if fnmatch.fnmatch(name, pattern):
                found.append(os.path.join(root, name))

    output = "\n".join(found)
    return ToolResult(
        tool_name="fs.glob_paths", success=True, output=output,
        metadata={"match_count": len(found)},
        side_effect_summary="none",
    )


def _write_file(args: dict[str, Any]) -> ToolResult:
    path = args["path"]
    content = args["content"]
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return ToolResult(
        tool_name="fs.write_file", success=True,
        output=f"Wrote {len(content)} bytes to {path}",
        metadata={"bytes_written": len(content)},
        side_effect_summary=f"created/overwritten: {path}",
    )


def _delete_path(args: dict[str, Any]) -> ToolResult:
    path = args["path"]
    if not os.path.exists(path):
        return ToolResult(
            tool_name="fs.delete_path", success=False, output="",
            error=f"Path not found: {path}",
        )
    if os.path.isdir(path):
        shutil.rmtree(path)
        kind = "directory"
    else:
        os.remove(path)
        kind = "file"
    return ToolResult(
        tool_name="fs.delete_path", success=True,
        output=f"Deleted {kind}: {path}",
        side_effect_summary=f"deleted {kind}: {path}",
    )


# ---------------------------------------------------------------------------
# Handler map for executor registration
# ---------------------------------------------------------------------------

HANDLERS: dict[str, ToolHandler] = {
    "fs.read_file": _read_file,
    "fs.list_dir": _list_dir,
    "fs.search_text": _search_text,
    "fs.glob_paths": _glob_paths,
    "fs.write_file": _write_file,
    "fs.delete_path": _delete_path,
}


# ---------------------------------------------------------------------------
# Sandboxed handlers
# ---------------------------------------------------------------------------

def _path_inside(root: str, target: str) -> bool:
    """True iff ``target`` resolves inside ``root`` (both realpaths)."""
    root_real = os.path.realpath(root)
    target_real = os.path.realpath(target)
    return target_real == root_real or target_real.startswith(root_real + os.sep)


def _refuse(tool_name: str, path: str) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=False,
        output="",
        error=f"Path outside sandboxed workspace: {path}",
        metadata={"path": path},
    )


def create_handlers(workspace: str | None = None) -> dict[str, ToolHandler]:
    """Return filesystem handlers.

    When ``workspace`` is ``None`` the raw handlers are returned (same as the
    ``HANDLERS`` export — kept for back-compat with direct test wiring). When
    ``workspace`` is a directory path, every handler validates that its
    ``path`` argument resolves inside that root before doing any I/O. The
    workspace directory is created if missing.
    """
    if workspace is None:
        return dict(HANDLERS)

    root = os.path.realpath(workspace)
    os.makedirs(root, exist_ok=True)

    def _guarded(name: str, inner: ToolHandler) -> ToolHandler:
        def handler(args: dict[str, Any]) -> ToolResult:
            target = args.get("path", "")
            if not isinstance(target, str) or not target:
                return ToolResult(
                    tool_name=name, success=False, output="",
                    error="path argument required",
                )
            # Resolve relative paths against the workspace root so callers
            # can use either absolute or workspace-relative paths.
            if not os.path.isabs(target):
                target = os.path.join(root, target)
            if not _path_inside(root, target):
                return _refuse(name, args.get("path", ""))
            guarded_args = dict(args)
            guarded_args["path"] = target
            return inner(guarded_args)
        return handler

    return {name: _guarded(name, fn) for name, fn in HANDLERS.items()}
