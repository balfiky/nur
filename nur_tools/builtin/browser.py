"""Builtin browser automation tool — pluggable provider for testability.

Designed behind a BrowserProvider protocol so tests can inject a fake
without a real browser. The default provider is a no-op that returns
an error asking for configuration.

When Playwright or another automation library is available, a concrete
provider can be plugged in without changing any Nūr code.
"""

from __future__ import annotations

from typing import Any, Protocol

from core.types import ToolCapability, ToolCategory, ToolResult
from nur_tools.executor import ToolHandler


# ---------------------------------------------------------------------------
# Browser page result
# ---------------------------------------------------------------------------

_MAX_PAGE_TEXT = 10_000  # cap returned text to prevent memory bloat


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


class BrowserProvider(Protocol):
    """Abstract interface for browser automation backends."""

    def open_url(self, url: str) -> str:
        """Navigate to a URL. Returns the page title or status."""
        ...

    def get_page_text(self, url: str) -> str:
        """Get the visible text content of a page."""
        ...

    def click(self, selector: str) -> str:
        """Click an element matching the CSS selector. Returns status."""
        ...

    def fill(self, selector: str, text: str) -> str:
        """Fill an input element with text. Returns status."""
        ...

    def screenshot(self, url: str | None = None) -> str:
        """Take a screenshot. Returns the file path of the saved image."""
        ...


# ---------------------------------------------------------------------------
# Default (unconfigured) provider
# ---------------------------------------------------------------------------


class NullBrowserProvider:
    """Placeholder provider — returns an error for all calls."""

    def open_url(self, url: str) -> str:
        raise RuntimeError("No browser provider configured")

    def get_page_text(self, url: str) -> str:
        raise RuntimeError("No browser provider configured")

    def click(self, selector: str) -> str:
        raise RuntimeError("No browser provider configured")

    def fill(self, selector: str, text: str) -> str:
        raise RuntimeError("No browser provider configured")

    def screenshot(self, url: str | None = None) -> str:
        raise RuntimeError("No browser provider configured")


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

CAPABILITIES: list[ToolCapability] = [
    ToolCapability(
        name="browser.open_url",
        description="Open a URL in the browser and return the page title",
        category=ToolCategory.READ_ONLY,
        arg_schema={"url": {"type": "string", "required": True}},
        requires_network=True,
    ),
    ToolCapability(
        name="browser.get_page_text",
        description="Get the visible text content of a web page",
        category=ToolCategory.READ_ONLY,
        arg_schema={"url": {"type": "string", "required": True}},
        requires_network=True,
    ),
    ToolCapability(
        name="browser.click",
        description="Click an element on the current page by CSS selector",
        category=ToolCategory.EXTERNAL_ACTION,
        arg_schema={"selector": {"type": "string", "required": True}},
        requires_network=True,
    ),
    ToolCapability(
        name="browser.fill",
        description="Fill an input field on the current page with text",
        category=ToolCategory.WRITE,
        arg_schema={
            "selector": {"type": "string", "required": True},
            "text": {"type": "string", "required": True},
        },
        requires_network=True,
    ),
    ToolCapability(
        name="browser.screenshot",
        description="Take a screenshot of the current page or a URL",
        category=ToolCategory.READ_ONLY,
        arg_schema={"url": {"type": "string", "required": False}},
        requires_network=True,
    ),
]

# ---------------------------------------------------------------------------
# Handlers (closed over a provider instance)
# ---------------------------------------------------------------------------


def create_handlers(provider: BrowserProvider | None = None) -> dict[str, ToolHandler]:
    """Create handler functions bound to the given provider.

    If no provider is given, uses NullBrowserProvider which errors on all calls.
    The executor normalizes those errors into structured ToolResult values.
    """
    prov = provider or NullBrowserProvider()

    def _open_url(args: dict[str, Any]) -> ToolResult:
        url = args["url"]
        title = prov.open_url(url)
        return ToolResult(
            tool_name="browser.open_url",
            success=True,
            output=title,
            metadata={"url": url},
            side_effect_summary=f"Navigated to {url}",
        )

    def _get_page_text(args: dict[str, Any]) -> ToolResult:
        url = args["url"]
        text = prov.get_page_text(url)
        truncated = len(text) > _MAX_PAGE_TEXT
        output = text[:_MAX_PAGE_TEXT]
        if truncated:
            output += f"\n... (truncated, {len(text)} chars total)"
        return ToolResult(
            tool_name="browser.get_page_text",
            success=True,
            output=output,
            metadata={"url": url, "length": len(text), "truncated": truncated},
            side_effect_summary="none",
        )

    def _click(args: dict[str, Any]) -> ToolResult:
        selector = args["selector"]
        status = prov.click(selector)
        return ToolResult(
            tool_name="browser.click",
            success=True,
            output=status,
            metadata={"selector": selector},
            side_effect_summary=f"Clicked {selector}",
        )

    def _fill(args: dict[str, Any]) -> ToolResult:
        selector = args["selector"]
        text = args["text"]
        status = prov.fill(selector, text)
        return ToolResult(
            tool_name="browser.fill",
            success=True,
            output=status,
            metadata={"selector": selector},
            side_effect_summary=f"Filled {selector}",
        )

    def _screenshot(args: dict[str, Any]) -> ToolResult:
        url = args.get("url")
        path = prov.screenshot(url)
        return ToolResult(
            tool_name="browser.screenshot",
            success=True,
            output=path,
            metadata={"url": url, "path": path},
            side_effect_summary="Screenshot saved",
        )

    return {
        "browser.open_url": _open_url,
        "browser.get_page_text": _get_page_text,
        "browser.click": _click,
        "browser.fill": _fill,
        "browser.screenshot": _screenshot,
    }
