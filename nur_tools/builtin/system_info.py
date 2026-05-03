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
import subprocess
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
    ToolCapability(
        name="system.memory_usage",
        description="Return total, used, free, and available RAM for the machine running Nūr",
        category=ToolCategory.READ_ONLY,
        arg_schema={},
    ),
    ToolCapability(
        name="system.installed_packages",
        description=(
            "List installed operating-system packages from the host package "
            "database. Use for apt/dpkg package inventory questions such as "
            "packages starting with a requested prefix."
        ),
        category=ToolCategory.READ_ONLY,
        arg_schema={
            "prefix": {
                "type": "string",
                "required": False,
                "default": "",
                "description": "Optional package-name prefix",
            },
            "limit": {
                "type": "integer",
                "required": False,
                "default": 200,
                "description": "Maximum rows to return, capped at 1000",
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


def _memory_usage(_: dict[str, Any]) -> ToolResult:
    usage = _read_memory_usage()
    if usage is None:
        return ToolResult(
            tool_name="system.memory_usage",
            success=False,
            output="",
            error="Memory usage is unavailable on this platform",
            metadata={},
        )

    total = usage["total_bytes"]
    available = usage["available_bytes"]
    free = usage["free_bytes"]
    used = max(0, total - available)
    used_percent = (used / total * 100.0) if total else 0.0
    output = "\n".join(
        [
            "Memory Total Used Free Available Use%",
            (
                f"RAM {_human_bytes(total)} {_human_bytes(used)} "
                f"{_human_bytes(free)} {_human_bytes(available)} "
                f"{used_percent:.0f}%"
            ),
        ]
    )
    return ToolResult(
        tool_name="system.memory_usage",
        success=True,
        output=output,
        metadata={
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "available_bytes": available,
            "used_percent": round(used_percent, 2),
        },
        side_effect_summary="none",
    )


def _read_memory_usage() -> dict[str, int] | None:
    proc = _read_proc_meminfo()
    if proc is not None:
        return proc

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None

    total = int(page_size) * int(total_pages)
    available = int(page_size) * int(available_pages)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "free_bytes": available,
    }


def _read_proc_meminfo() -> dict[str, int] | None:
    path = "/proc/meminfo"
    if not os.path.exists(path):
        return None

    values: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if not rest:
                    continue
                parts = rest.strip().split()
                if not parts:
                    continue
                try:
                    kib = int(parts[0])
                except ValueError:
                    continue
                values[key] = kib * 1024
    except OSError:
        return None

    total = values.get("MemTotal")
    if not total:
        return None
    available = values.get("MemAvailable", values.get("MemFree", 0))
    free = values.get("MemFree", available)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "free_bytes": free,
    }


def _installed_packages(args: dict[str, Any]) -> ToolResult:
    prefix = str(args.get("prefix") or "").strip().lower()
    limit = _coerce_limit(args.get("limit"), default=200, maximum=1000)

    if shutil.which("dpkg-query"):
        return _installed_dpkg_packages(prefix=prefix, limit=limit)
    if shutil.which("rpm"):
        return _installed_rpm_packages(prefix=prefix, limit=limit)
    return ToolResult(
        tool_name="system.installed_packages",
        success=False,
        output="",
        error="No supported package database found (dpkg-query or rpm)",
        metadata={"prefix": prefix, "limit": limit},
    )


def _installed_dpkg_packages(*, prefix: str, limit: int) -> ToolResult:
    try:
        proc = subprocess.run(
            [
                "dpkg-query",
                "-W",
                "-f=${Package}\t${Version}\t${Architecture}\t${Status}\n",
            ],
            shell=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolResult(
            tool_name="system.installed_packages",
            success=False,
            output="",
            error=f"{type(exc).__name__}: {exc}",
            metadata={"manager": "dpkg", "prefix": prefix, "limit": limit},
        )

    if proc.returncode != 0:
        return ToolResult(
            tool_name="system.installed_packages",
            success=False,
            output=proc.stderr,
            error=f"Exit code {proc.returncode}",
            metadata={"manager": "dpkg", "prefix": prefix, "limit": limit},
        )

    rows: list[tuple[str, str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, version, arch, status = parts[0], parts[1], parts[2], parts[3]
        if "install ok installed" not in status:
            continue
        if prefix and not name.lower().startswith(prefix):
            continue
        rows.append((name, version, arch))

    return _package_result(rows, manager="dpkg", prefix=prefix, limit=limit)


def _installed_rpm_packages(*, prefix: str, limit: int) -> ToolResult:
    try:
        proc = subprocess.run(
            ["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolResult(
            tool_name="system.installed_packages",
            success=False,
            output="",
            error=f"{type(exc).__name__}: {exc}",
            metadata={"manager": "rpm", "prefix": prefix, "limit": limit},
        )

    if proc.returncode != 0:
        return ToolResult(
            tool_name="system.installed_packages",
            success=False,
            output=proc.stderr,
            error=f"Exit code {proc.returncode}",
            metadata={"manager": "rpm", "prefix": prefix, "limit": limit},
        )

    rows: list[tuple[str, str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, version, arch = parts[0], parts[1], parts[2]
        if prefix and not name.lower().startswith(prefix):
            continue
        rows.append((name, version, arch))

    return _package_result(rows, manager="rpm", prefix=prefix, limit=limit)


def _package_result(
    rows: list[tuple[str, str, str]],
    *,
    manager: str,
    prefix: str,
    limit: int,
) -> ToolResult:
    sorted_rows = sorted(rows, key=lambda row: row[0])
    displayed = sorted_rows[:limit]
    truncated = len(sorted_rows) > len(displayed)
    lines = ["Name Version Arch"]
    lines.extend(f"{name} {version} {arch}" for name, version, arch in displayed)
    if truncated:
        lines.append(f"... truncated, {len(sorted_rows) - len(displayed)} more")
    return ToolResult(
        tool_name="system.installed_packages",
        success=True,
        output="\n".join(lines) if displayed else "(no installed packages matched)",
        metadata={
            "manager": manager,
            "prefix": prefix,
            "count": len(sorted_rows),
            "returned": len(displayed),
            "truncated": truncated,
            "limit": limit,
        },
        side_effect_summary="none",
    )


def _coerce_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(maximum, limit))


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
    "system.memory_usage": _memory_usage,
    "system.installed_packages": _installed_packages,
}
