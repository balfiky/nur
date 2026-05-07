"""Tests for Agentic Tools Phase 6: richer tools.

Covers:
  1. Browser tool — provider protocol, capabilities, handlers, truncation
  2. Calendar tool — provider protocol, capabilities, handlers, CRUD
  3. Web extract_text — new capability, truncation
  4. Registry coexistence — all tools in one registry
  5. Intent detection patterns — new heuristic patterns
  6. Cognitive compatibility — appraisal works with new tools
  7. Exception safety — NullProviders produce proper ToolResult via executor
"""

from __future__ import annotations

from typing import Any

import pytest

from core.types import (
    ToolCategory,
    ToolResult,
)
from core.tool_appraisal import appraise_tool_result
from core.dual_process.tool_loop import detect_tool_intent
from nur_tools.registry import ToolRegistry
from nur_tools.executor import ToolExecutor
from nur_tools import register_builtins
from nur_tools.builtin.browser import (
    CAPABILITIES as BROWSER_CAPS,
    create_handlers as create_browser_handlers,
    _MAX_PAGE_TEXT,
)
from nur_tools.builtin.calendar import (
    CalendarEvent,
    CAPABILITIES as CALENDAR_CAPS,
    create_handlers as create_calendar_handlers,
)
from nur_tools.builtin.web_search import (
    CAPABILITIES as WEB_CAPS,
    create_handlers as create_web_handlers,
    _MAX_EXTRACT_TEXT,
)


# ===================================================================
# Fake providers
# ===================================================================

class FakeBrowserProvider:
    """Browser provider that returns canned results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def open_url(self, url: str) -> str:
        self.calls.append(("open_url", url))
        return f"Page Title for {url}"

    def get_page_text(self, url: str) -> str:
        self.calls.append(("get_page_text", url))
        return f"Visible text content of {url}"

    def click(self, selector: str) -> str:
        self.calls.append(("click", selector))
        return f"Clicked {selector}"

    def fill(self, selector: str, text: str) -> str:
        self.calls.append(("fill", (selector, text)))
        return f"Filled {selector} with {text}"

    def screenshot(self, url: str | None = None) -> str:
        self.calls.append(("screenshot", url))
        return "/tmp/screenshot.png"


class FakeCalendarProvider:
    """Calendar provider with in-memory event store."""

    def __init__(self) -> None:
        self._events: dict[str, CalendarEvent] = {}
        self._next_id = 1

    def list_events(self, date: str, limit: int) -> list[CalendarEvent]:
        events = [e for e in self._events.values() if e.start.startswith(date)]
        return events[:limit]

    def create_event(
        self, title: str, start: str, end: str,
        location: str = "", description: str = "",
    ) -> CalendarEvent:
        eid = f"evt_{self._next_id}"
        self._next_id += 1
        event = CalendarEvent(
            id=eid, title=title, start=start, end=end,
            location=location, description=description,
        )
        self._events[eid] = event
        return event

    def delete_event(self, event_id: str) -> bool:
        if event_id in self._events:
            del self._events[event_id]
            return True
        return False


class FakeWebProvider:
    """Web provider that returns canned results."""

    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        return [{"title": f"Result for {query}", "url": f"https://example.com/{query}"}]

    def fetch(self, url: str) -> str:
        return f"Raw HTML content of {url}"

    def extract_text(self, url: str) -> str:
        return f"Clean readable text from {url}"


# ===================================================================
# 1. Browser tool
# ===================================================================

class TestBrowserCapabilities:
    def test_five_capabilities(self):
        assert len(BROWSER_CAPS) == 5

    def test_capability_names(self):
        names = {c.name for c in BROWSER_CAPS}
        assert names == {
            "browser.open_url", "browser.get_page_text",
            "browser.click", "browser.fill", "browser.screenshot",
        }

    def test_categories(self):
        by_name = {c.name: c for c in BROWSER_CAPS}
        assert by_name["browser.open_url"].category == ToolCategory.READ_ONLY
        assert by_name["browser.get_page_text"].category == ToolCategory.READ_ONLY
        assert by_name["browser.click"].category == ToolCategory.EXTERNAL_ACTION
        assert by_name["browser.fill"].category == ToolCategory.WRITE
        assert by_name["browser.screenshot"].category == ToolCategory.READ_ONLY

    def test_all_require_network(self):
        for cap in BROWSER_CAPS:
            assert cap.requires_network is True


class TestBrowserHandlers:
    def test_open_url(self):
        prov = FakeBrowserProvider()
        handlers = create_browser_handlers(prov)
        result = handlers["browser.open_url"]({"url": "https://example.com"})
        assert result.success is True
        assert "Page Title" in result.output
        assert result.metadata["url"] == "https://example.com"

    def test_get_page_text(self):
        prov = FakeBrowserProvider()
        handlers = create_browser_handlers(prov)
        result = handlers["browser.get_page_text"]({"url": "https://example.com"})
        assert result.success is True
        assert "Visible text" in result.output

    def test_get_page_text_truncation(self):
        class LongPageProvider(FakeBrowserProvider):
            def get_page_text(self, url: str) -> str:
                return "x" * (_MAX_PAGE_TEXT + 1000)

        handlers = create_browser_handlers(LongPageProvider())
        result = handlers["browser.get_page_text"]({"url": "https://example.com"})
        assert result.success is True
        assert "truncated" in result.output
        assert result.metadata["truncated"] is True

    def test_click(self):
        prov = FakeBrowserProvider()
        handlers = create_browser_handlers(prov)
        result = handlers["browser.click"]({"selector": "#submit"})
        assert result.success is True
        assert "Clicked" in result.output

    def test_fill(self):
        prov = FakeBrowserProvider()
        handlers = create_browser_handlers(prov)
        result = handlers["browser.fill"]({"selector": "#name", "text": "Jarvis"})
        assert result.success is True
        assert "Filled" in result.output

    def test_screenshot(self):
        prov = FakeBrowserProvider()
        handlers = create_browser_handlers(prov)
        result = handlers["browser.screenshot"]({"url": "https://example.com"})
        assert result.success is True
        assert result.output.endswith(".png")

    def test_screenshot_no_url(self):
        prov = FakeBrowserProvider()
        handlers = create_browser_handlers(prov)
        result = handlers["browser.screenshot"]({})
        assert result.success is True


class TestNullBrowserProvider:
    def test_null_provider_errors(self):
        handlers = create_browser_handlers(None)
        # Executor catches the exception — but direct handler call raises
        with pytest.raises(RuntimeError, match="No browser provider"):
            handlers["browser.open_url"]({"url": "https://example.com"})


# ===================================================================
# 2. Calendar tool
# ===================================================================

class TestCalendarCapabilities:
    def test_three_capabilities(self):
        assert len(CALENDAR_CAPS) == 3

    def test_capability_names(self):
        names = {c.name for c in CALENDAR_CAPS}
        assert names == {
            "calendar.list_events", "calendar.create_event", "calendar.delete_event",
        }

    def test_categories(self):
        by_name = {c.name: c for c in CALENDAR_CAPS}
        assert by_name["calendar.list_events"].category == ToolCategory.READ_ONLY
        assert by_name["calendar.create_event"].category == ToolCategory.WRITE
        assert by_name["calendar.delete_event"].category == ToolCategory.DESTRUCTIVE


class TestCalendarHandlers:
    def test_list_events_empty(self):
        prov = FakeCalendarProvider()
        handlers = create_calendar_handlers(prov)
        result = handlers["calendar.list_events"]({"date": "2026-04-02"})
        assert result.success is True
        assert "No events" in result.output

    def test_create_and_list(self):
        prov = FakeCalendarProvider()
        handlers = create_calendar_handlers(prov)
        # Create
        result = handlers["calendar.create_event"]({
            "title": "Team standup",
            "start": "2026-04-02T09:00",
            "end": "2026-04-02T09:30",
            "location": "Room A",
        })
        assert result.success is True
        assert "Created" in result.output
        assert "Team standup" in result.output

        # List
        result = handlers["calendar.list_events"]({"date": "2026-04-02"})
        assert result.success is True
        assert "Team standup" in result.output
        assert "Room A" in result.output

    def test_delete_event(self):
        prov = FakeCalendarProvider()
        handlers = create_calendar_handlers(prov)
        # Create first
        handlers["calendar.create_event"]({
            "title": "Test", "start": "2026-04-02T10:00", "end": "2026-04-02T11:00",
        })
        # Delete
        result = handlers["calendar.delete_event"]({"event_id": "evt_1"})
        assert result.success is True
        assert "Deleted" in result.output

    def test_delete_nonexistent(self):
        prov = FakeCalendarProvider()
        handlers = create_calendar_handlers(prov)
        result = handlers["calendar.delete_event"]({"event_id": "no_such_id"})
        assert result.success is False
        assert "not found" in result.error

    def test_create_event_metadata(self):
        prov = FakeCalendarProvider()
        handlers = create_calendar_handlers(prov)
        result = handlers["calendar.create_event"]({
            "title": "Meeting", "start": "2026-04-02T14:00", "end": "2026-04-02T15:00",
        })
        assert result.metadata["event_id"] == "evt_1"
        assert result.metadata["title"] == "Meeting"


class TestNullCalendarProvider:
    def test_null_provider_errors(self):
        handlers = create_calendar_handlers(None)
        with pytest.raises(RuntimeError, match="No calendar provider"):
            handlers["calendar.list_events"]({"date": "2026-04-02"})


# ===================================================================
# 3. Web extract_text
# ===================================================================

class TestWebExtractText:
    def test_capability_exists(self):
        names = {c.name for c in WEB_CAPS}
        assert "web.extract_text" in names

    def test_extract_text_handler(self):
        prov = FakeWebProvider()
        handlers = create_web_handlers(prov)
        result = handlers["web.extract_text"]({"url": "https://example.com"})
        assert result.success is True
        assert "Clean readable text" in result.output

    def test_extract_text_truncation(self):
        class LongTextProvider(FakeWebProvider):
            def extract_text(self, url: str) -> str:
                return "x" * (_MAX_EXTRACT_TEXT + 1000)

        handlers = create_web_handlers(LongTextProvider())
        result = handlers["web.extract_text"]({"url": "https://example.com"})
        assert result.success is True
        assert "truncated" in result.output
        assert result.metadata["truncated"] is True
        assert len(result.output) < _MAX_EXTRACT_TEXT + 100  # truncated + suffix

    def test_extract_text_metadata(self):
        prov = FakeWebProvider()
        handlers = create_web_handlers(prov)
        result = handlers["web.extract_text"]({"url": "https://example.com/page"})
        assert result.metadata["url"] == "https://example.com/page"
        assert isinstance(result.metadata["length"], int)

    def test_null_provider_errors(self):
        handlers = create_web_handlers(None)
        with pytest.raises(RuntimeError, match="No web provider"):
            handlers["web.extract_text"]({"url": "https://example.com"})


# ===================================================================
# 4. Registry coexistence
# ===================================================================

class TestRegistryCoexistence:
    def test_all_tools_registered(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)
        names = reg.names()
        # System: 5, Filesystem: 6, Shell: 1, Web: 3, Browser: 5,
        # Calendar: 3, Skills: 7 = 30
        assert len(names) == 30

    def test_expected_tool_names(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)
        names = set(reg.names())
        # Spot check all categories
        assert "system.hostname" in names
        assert "system.disk_usage" in names
        assert "system.memory_usage" in names
        assert "system.installed_packages" in names
        assert "skills.create_from_request" in names
        assert "skills.audit" in names
        assert "skills.enable" in names
        assert "fs.read_file" in names
        assert "shell.run_command" in names
        assert "web.search" in names
        assert "web.extract_text" in names
        assert "browser.open_url" in names
        assert "browser.screenshot" in names
        assert "calendar.list_events" in names
        assert "calendar.delete_event" in names

    def test_custom_providers_used(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        prov = FakeBrowserProvider()
        register_builtins(reg, exe, browser_provider=prov)
        result = exe.execute("browser.open_url", {"url": "https://test.com"})
        assert result.success is True
        assert prov.calls[-1] == ("open_url", "https://test.com")

    def test_category_filter(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)
        destructive = reg.list_tools(category=ToolCategory.DESTRUCTIVE)
        names = {t.name for t in destructive}
        assert "fs.delete_path" in names
        assert "shell.run_command" in names
        assert "calendar.delete_event" in names


# ===================================================================
# 5. Intent detection patterns
# ===================================================================

class TestIntentDetection:
    def _all_tools(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)
        return set(reg.names())

    def test_extract_text_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("extract text from https://example.com/article", available)
        assert intent is not None
        assert intent.tool_name == "web.extract_text"
        assert intent.arguments["url"] == "https://example.com/article"

    def test_get_readable_text_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("get the readable text of https://example.com", available)
        assert intent is not None
        assert intent.tool_name == "web.extract_text"

    def test_browse_to_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("browse to https://example.com", available)
        assert intent is not None
        assert intent.tool_name == "browser.open_url"

    def test_open_in_browser_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("open https://example.com/page in the browser", available)
        assert intent is not None
        assert intent.tool_name == "browser.open_url"

    def test_screenshot_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("screenshot of https://example.com", available)
        assert intent is not None
        assert intent.tool_name == "browser.screenshot"

    def test_take_screenshot_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("take a screenshot", available)
        assert intent is not None
        assert intent.tool_name == "browser.screenshot"

    def test_list_events_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("show my events for 2026-04-02", available)
        assert intent is not None
        assert intent.tool_name == "calendar.list_events"
        assert intent.arguments["date"] == "2026-04-02"

    def test_check_calendar_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("check my calendar for 2026-04-03", available)
        assert intent is not None
        assert intent.tool_name == "calendar.list_events"

    def test_whats_on_calendar_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("what are my events on 2026-04-02", available)
        assert intent is not None
        assert intent.tool_name == "calendar.list_events"

    def test_create_event_pattern(self):
        available = self._all_tools()
        intent = detect_tool_intent("create event 'Team standup' on 2026-04-02T09:00", available)
        assert intent is not None
        assert intent.tool_name == "calendar.create_event"
        assert intent.arguments["title"] == "Team standup"

    def test_conversational_no_match(self):
        available = self._all_tools()
        assert detect_tool_intent("How are you today?", available) is None

    def test_existing_patterns_still_work(self):
        available = self._all_tools()
        # Filesystem
        intent = detect_tool_intent("read file /tmp/test.txt", available)
        assert intent is not None
        assert intent.tool_name == "fs.read_file"
        # Web search
        intent = detect_tool_intent("search the web for 'python tutorial'", available)
        assert intent is not None
        assert intent.tool_name == "web.search"


# ===================================================================
# 6. Cognitive compatibility
# ===================================================================

class TestCognitiveCompatibility:
    def test_browser_success_appraisal(self):
        result = ToolResult(
            tool_name="browser.open_url", success=True, output="Page loaded",
        )
        obs = appraise_tool_result(result, ToolCategory.READ_ONLY)
        assert obs.certainty_delta > 0

    def test_browser_failure_appraisal(self):
        result = ToolResult(
            tool_name="browser.open_url", success=False, output="", error="Timeout",
        )
        obs = appraise_tool_result(result, ToolCategory.READ_ONLY)
        assert obs.certainty_delta == 0.0
        assert obs.emotional_delta == {}
        assert "operational issue" in obs.summary

    def test_calendar_write_appraisal(self):
        result = ToolResult(
            tool_name="calendar.create_event", success=True,
            output="Created: Meeting",
        )
        obs = appraise_tool_result(result, ToolCategory.WRITE)
        assert obs.certainty_delta > 0

    def test_calendar_destructive_appraisal(self):
        result = ToolResult(
            tool_name="calendar.delete_event", success=True,
            output="Deleted event",
        )
        obs = appraise_tool_result(result, ToolCategory.DESTRUCTIVE)
        # Destructive success has specific emotional signature
        assert obs.emotional_delta.get("arousal", 0) > 0

    def test_web_extract_appraisal(self):
        result = ToolResult(
            tool_name="web.extract_text", success=True, output="Article text...",
        )
        obs = appraise_tool_result(result, ToolCategory.READ_ONLY)
        assert obs.certainty_delta > 0


# ===================================================================
# 7. Exception safety through executor
# ===================================================================

class TestExceptionSafety:
    def test_null_browser_through_executor(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)  # NullBrowserProvider
        result = exe.execute("browser.open_url", {"url": "https://example.com"})
        assert result.success is False
        assert "No browser provider" in result.error

    def test_null_calendar_through_executor(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)  # NullCalendarProvider
        result = exe.execute("calendar.list_events", {"date": "2026-04-02"})
        assert result.success is False
        assert "No calendar provider" in result.error

    def test_null_web_extract_through_executor(self):
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)  # NullWebProvider
        result = exe.execute("web.extract_text", {"url": "https://example.com"})
        assert result.success is False
        assert "No web provider" in result.error

    def test_all_null_handlers_produce_structured_errors(self):
        """All null-provider tools fail gracefully through the executor."""
        reg = ToolRegistry()
        exe = ToolExecutor(reg)
        register_builtins(reg, exe)
        null_tools = [
            ("browser.open_url", {"url": "http://x"}),
            ("browser.get_page_text", {"url": "http://x"}),
            ("browser.click", {"selector": "#x"}),
            ("browser.fill", {"selector": "#x", "text": "x"}),
            ("browser.screenshot", {}),
            ("calendar.list_events", {"date": "2026-01-01"}),
            ("calendar.create_event", {"title": "x", "start": "x", "end": "x"}),
            ("calendar.delete_event", {"event_id": "x"}),
            ("web.extract_text", {"url": "http://x"}),
        ]
        for tool_name, args in null_tools:
            result = exe.execute(tool_name, args)
            assert result.success is False, f"{tool_name} should fail with null provider"
            assert isinstance(result.error, str)
            assert result.latency_ms > 0
