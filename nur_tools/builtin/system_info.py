"""Builtin system inspection tools.

These tools expose common host facts as read-only capabilities so routine
machine-inspection requests do not have to route through arbitrary shell
execution.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
from typing import Any

from core.types import ToolCapability, ToolCategory, ToolResult
from nur_tools.executor import ToolHandler


CAPABILITIES: list[ToolCapability] = [
    ToolCapability(
        name="system.hostname",
        description="Return the hostname of the machine running Nūr",
        category=ToolCategory.READ_ONLY,
        arg_schema={},
    ),
    ToolCapability(
        name="system.uname",
        description="Return operating system and kernel information for the machine running Nūr",
        category=ToolCategory.READ_ONLY,
        arg_schema={},
    ),
    ToolCapability(
        name="system.disk_usage",
        description="Return disk usage for a filesystem path on the machine running Nūr",
        category=ToolCategory.READ_ONLY,
        arg_schema={
            "path": {
                "type": "string",
                "required": False,
                "default": "/",
                "description": "Filesystem path to inspect, defaults to /",
            },
        },
    ),
]


def _hostname(_: dict[str, Any]) -> ToolResult:
    hostname = socket.gethostname()
    return ToolResult(
        tool_name="system.hostname",
        success=True,
        output=hostname,
        metadata={"hostname": hostname},
        side_effect_summary="none",
    )


def _uname(_: dict[str, Any]) -> ToolResult:
    uname = platform.uname()
    output = " ".join(
        part for part in (
            uname.system,
            uname.node,
            uname.release,
            uname.version,
            uname.machine,
            uname.processor,
        )
        if part
    )
    return ToolResult(
        tool_name="system.uname",
        success=True,
        output=output,
        metadata={
            "system": uname.system,
            "node": uname.node,
            "release": uname.release,
            "version": uname.version,
            "machine": uname.machine,
            "processor": uname.processor,
        },
        side_effect_summary="none",
    )


def _disk_usage(args: dict[str, Any]) -> ToolResult:
    path = str(args.get("path") or "/")
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return ToolResult(
            tool_name="system.disk_usage",
            success=False,
            output="",
            error=f"OSError: {exc}",
            metadata={"path": path},
        )

    used_percent = (usage.used / usage.total * 100.0) if usage.total else 0.0
    output = "\n".join(
        [
            "Path Size Used Avail Use%",
            (
                f"{os.path.abspath(path)} {_human_bytes(usage.total)} "
                f"{_human_bytes(usage.used)} {_human_bytes(usage.free)} "
                f"{used_percent:.0f}%"
            ),
        ]
    )
    return ToolResult(
        tool_name="system.disk_usage",
        success=True,
        output=output,
        metadata={
            "path": path,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "used_percent": round(used_percent, 2),
        },
        side_effect_summary="none",
    )


def _human_bytes(value: int) -> str:
    units = ["B", "K", "M", "G", "T", "P"]
    size = float(value)
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)}{unit}"
            if size >= 10:
                return f"{size:.0f}{unit}"
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{value}B"


HANDLERS: dict[str, ToolHandler] = {
    "system.hostname": _hostname,
    "system.uname": _uname,
    "system.disk_usage": _disk_usage,
}
