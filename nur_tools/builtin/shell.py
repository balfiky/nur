"""Builtin shell tool — command execution with structured output.

Captures stdout, stderr, and exit code. Normalizes timeouts and
execution errors into ToolResult values.
"""

from __future__ import annotations

import subprocess
from typing import Any

from core.types import ToolCapability, ToolCategory, ToolResult
from nur_tools.executor import ToolHandler

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

CAPABILITIES: list[ToolCapability] = [
    ToolCapability(
        name="shell.run_command",
        description=(
            "Run a shell command through the local system shell and capture "
            "stdout, stderr, and exit code. Supports normal shell syntax such "
            "as pipes, redirects, environment expansion, and &&."
        ),
        category=ToolCategory.DESTRUCTIVE,
        arg_schema={
            "cmd": {"type": "string", "required": True},
            "cwd": {"type": "string", "required": False},
            "timeout_seconds": {"type": "number", "required": False, "default": 30},
        },
    ),
]

# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = 30


def _run_command(args: dict[str, Any]) -> ToolResult:
    cmd = str(args["cmd"])
    cwd = args.get("cwd") or None
    timeout = args.get("timeout_seconds", _DEFAULT_TIMEOUT)
    if not cmd.strip():
        return ToolResult(
            tool_name="shell.run_command",
            success=False,
            output="",
            error="Command is empty",
            metadata={"cmd": cmd},
        )

    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            tool_name="shell.run_command",
            success=False,
            output="",
            error=f"Command timed out after {timeout}s",
            metadata={"cmd": cmd, "timeout_seconds": timeout},
            side_effect_summary="process killed after timeout",
        )
    except OSError as exc:
        return ToolResult(
            tool_name="shell.run_command",
            success=False,
            output="",
            error=f"OSError: {exc}",
            metadata={"cmd": cmd},
        )

    output_parts: list[str] = []
    if proc.stdout:
        output_parts.append(proc.stdout)
    if proc.stderr:
        output_parts.append(f"[stderr]\n{proc.stderr}")
    output = "\n".join(output_parts)

    success = proc.returncode == 0
    return ToolResult(
        tool_name="shell.run_command",
        success=success,
        output=output,
        error=None if success else f"Exit code {proc.returncode}",
        metadata={
            "cmd": cmd,
            "exit_code": proc.returncode,
        },
        side_effect_summary="command executed" if success else f"command failed (exit {proc.returncode})",
    )


# ---------------------------------------------------------------------------
# Handler map
# ---------------------------------------------------------------------------

HANDLERS: dict[str, ToolHandler] = {
    "shell.run_command": _run_command,
}
