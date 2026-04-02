"""Builtin web search/fetch tool — pluggable provider for testability.

Designed behind a WebProvider protocol so tests can inject a fake
without network access. The default provider is a no-op that returns
an error asking for configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from core.types import ToolCapability, ToolCategory, ToolResult
from tools.executor import ToolHandler

# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


class WebProvider(Protocol):
    """Abstract interface for web search/fetch backends."""

    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        """Return a list of results, each with at least 'title' and 'url'."""
        ...

    def fetch(self, url: str) -> str:
        """Fetch a URL and return its text content."""
        ...


# ---------------------------------------------------------------------------
# Default (unconfigured) provider
# ---------------------------------------------------------------------------


class NullWebProvider:
    """Placeholder provider — returns an error for all calls."""

    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        raise RuntimeError("No web provider configured")

    def fetch(self, url: str) -> str:
        raise RuntimeError("No web provider configured")


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

CAPABILITIES: list[ToolCapability] = [
    ToolCapability(
        name="web.search",
        description="Search the web for a query",
        category=ToolCategory.READ_ONLY,
        arg_schema={
            "query": {"type": "string", "required": True},
            "limit": {"type": "number", "required": False, "default": 5},
        },
        requires_network=True,
    ),
    ToolCapability(
        name="web.fetch",
        description="Fetch the text content of a URL",
        category=ToolCategory.READ_ONLY,
        arg_schema={
            "url": {"type": "string", "required": True},
        },
        requires_network=True,
    ),
]

# ---------------------------------------------------------------------------
# Handlers (closed over a provider instance)
# ---------------------------------------------------------------------------


def create_handlers(provider: WebProvider | None = None) -> dict[str, ToolHandler]:
    """Create handler functions bound to the given provider.

    If no provider is given, uses NullWebProvider which errors on all calls.
    The executor normalizes those errors into structured ToolResult values.
    """
    prov = provider or NullWebProvider()

    def _search(args: dict[str, Any]) -> ToolResult:
        query = args["query"]
        limit = args.get("limit", 5)
        results = prov.search(query, limit)
        lines = [f"- {r.get('title', '?')}: {r.get('url', '?')}" for r in results]
        output = "\n".join(lines) if lines else "(no results)"
        return ToolResult(
            tool_name="web.search",
            success=True,
            output=output,
            metadata={"query": query, "result_count": len(results)},
            side_effect_summary="none",
        )

    def _fetch(args: dict[str, Any]) -> ToolResult:
        url = args["url"]
        text = prov.fetch(url)
        return ToolResult(
            tool_name="web.fetch",
            success=True,
            output=text,
            metadata={"url": url, "length": len(text)},
            side_effect_summary="none",
        )

    return {
        "web.search": _search,
        "web.fetch": _fetch,
    }


# Default handlers (NullWebProvider — will error until configured)
HANDLERS: dict[str, ToolHandler] = create_handlers()
