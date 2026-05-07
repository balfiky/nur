"""Shared classification for tool failures.

Operational failures are runtime/configuration problems, not emotional or
relational tension. They should be visible in debug surfaces without becoming
resolution pressure in the emotional engine.
"""

from __future__ import annotations

import re
from typing import Any

from core.types import ToolResult, UnresolvedItem


_OPERATIONAL_ERROR_RE = re.compile(
    r"("
    r"no\s+(?:web|browser|calendar)\s+provider\s+configured|"
    r"no\s+handler\s+registered|unknown\s+tool|"
    r"not\s+configured|missing\s+(?:api\s+)?key|api\s+key\s+is\s+not\s+configured|"
    r"connecttimeout|readtimeout|connectiontimeout|connectionerror|"
    r"network\s+is\s+unreachable|temporary\s+failure\s+in\s+name\s+resolution|"
    r"name\s+or\s+service\s+not\s+known|nodename\s+nor\s+servname|"
    r"connection\s+refused|connection\s+reset|dns|proxy"
    r")",
    re.IGNORECASE,
)

_EXTERNAL_TOOL_PREFIXES = ("web.", "browser.", "calendar.")


def is_operational_tool_failure(result: ToolResult) -> bool:
    """Return true when a failed tool result is an environment/config issue."""
    if result.success:
        return False
    error = str(result.error or "").strip()
    if not error:
        return False
    tool_name = str(result.tool_name or "")
    if _OPERATIONAL_ERROR_RE.search(error):
        return True
    if tool_name.startswith(_EXTERNAL_TOOL_PREFIXES) and _looks_network_error(error):
        return True
    return False


def operational_issue_summary(result: ToolResult) -> str:
    """Compact human-readable operational issue for debug/status surfaces."""
    error = " ".join(str(result.error or "unknown error").split())
    if len(error) > 160:
        error = error[:157] + "..."
    return f"{result.tool_name}: {error}"


def is_operational_unresolved_item(item: UnresolvedItem | Any) -> bool:
    """Return true for legacy unresolved items that should not affect emotion."""
    source = str(getattr(item, "source", "") or "")
    if source not in {
        "tool_failure",
        "blocked_action",
        "incomplete_task",
        "task_incomplete",
        "task_blocked",
    }:
        return False
    text = " ".join(
        [
            str(getattr(item, "description", "") or ""),
            str(getattr(item, "id", "") or ""),
        ]
    )
    return bool(_OPERATIONAL_ERROR_RE.search(text))


def _looks_network_error(error: str) -> bool:
    lowered = error.lower()
    return any(
        marker in lowered
        for marker in (
            "timeout",
            "timed out",
            "connection",
            "network",
            "dns",
            "proxy",
            "ssl",
            "certificate",
        )
    )
