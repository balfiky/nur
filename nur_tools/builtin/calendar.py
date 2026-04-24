"""Builtin calendar tool — pluggable provider for testability.

Designed behind a CalendarProvider protocol so tests can inject a fake.
The default provider is a no-op that returns an error.

A real provider can connect to Google Calendar, Outlook, or any
calendar API without changing Nūr code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from core.types import ToolCapability, ToolCategory, ToolResult
from nur_tools.executor import ToolHandler


# ---------------------------------------------------------------------------
# Calendar event dataclass
# ---------------------------------------------------------------------------

@dataclass
class CalendarEvent:
    """Normalized calendar event returned by providers."""
    id: str = ""
    title: str = ""
    start: str = ""     # ISO 8601
    end: str = ""       # ISO 8601
    location: str = ""
    description: str = ""


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------


class CalendarProvider(Protocol):
    """Abstract interface for calendar backends."""

    def list_events(self, date: str, limit: int) -> list[CalendarEvent]:
        """List events for a given date (YYYY-MM-DD). Returns up to limit events."""
        ...

    def create_event(
        self, title: str, start: str, end: str,
        location: str = "", description: str = "",
    ) -> CalendarEvent:
        """Create a new event. Returns the created event."""
        ...

    def delete_event(self, event_id: str) -> bool:
        """Delete an event by ID. Returns True if deleted."""
        ...


# ---------------------------------------------------------------------------
# Default (unconfigured) provider
# ---------------------------------------------------------------------------


class NullCalendarProvider:
    """Placeholder provider — returns an error for all calls."""

    def list_events(self, date: str, limit: int) -> list[CalendarEvent]:
        raise RuntimeError("No calendar provider configured")

    def create_event(
        self, title: str, start: str, end: str,
        location: str = "", description: str = "",
    ) -> CalendarEvent:
        raise RuntimeError("No calendar provider configured")

    def delete_event(self, event_id: str) -> bool:
        raise RuntimeError("No calendar provider configured")


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

CAPABILITIES: list[ToolCapability] = [
    ToolCapability(
        name="calendar.list_events",
        description="List calendar events for a date",
        category=ToolCategory.READ_ONLY,
        arg_schema={
            "date": {"type": "string", "required": True, "description": "YYYY-MM-DD"},
            "limit": {"type": "number", "required": False, "default": 10},
        },
        requires_network=True,
    ),
    ToolCapability(
        name="calendar.create_event",
        description="Create a new calendar event",
        category=ToolCategory.WRITE,
        arg_schema={
            "title": {"type": "string", "required": True},
            "start": {"type": "string", "required": True, "description": "ISO 8601"},
            "end": {"type": "string", "required": True, "description": "ISO 8601"},
            "location": {"type": "string", "required": False},
            "description": {"type": "string", "required": False},
        },
        requires_network=True,
    ),
    ToolCapability(
        name="calendar.delete_event",
        description="Delete a calendar event by ID",
        category=ToolCategory.DESTRUCTIVE,
        arg_schema={
            "event_id": {"type": "string", "required": True},
        },
        requires_network=True,
    ),
]

# ---------------------------------------------------------------------------
# Handlers (closed over a provider instance)
# ---------------------------------------------------------------------------


def create_handlers(provider: CalendarProvider | None = None) -> dict[str, ToolHandler]:
    """Create handler functions bound to the given provider."""
    prov = provider or NullCalendarProvider()

    def _list_events(args: dict[str, Any]) -> ToolResult:
        date = args["date"]
        limit = args.get("limit", 10)
        events = prov.list_events(date, limit)
        if not events:
            output = f"No events on {date}"
        else:
            lines = []
            for e in events:
                line = f"- {e.title} ({e.start} - {e.end})"
                if e.location:
                    line += f" @ {e.location}"
                lines.append(line)
            output = "\n".join(lines)
        return ToolResult(
            tool_name="calendar.list_events",
            success=True,
            output=output,
            metadata={"date": date, "event_count": len(events)},
            side_effect_summary="none",
        )

    def _create_event(args: dict[str, Any]) -> ToolResult:
        event = prov.create_event(
            title=args["title"],
            start=args["start"],
            end=args["end"],
            location=args.get("location", ""),
            description=args.get("description", ""),
        )
        return ToolResult(
            tool_name="calendar.create_event",
            success=True,
            output=f"Created: {event.title} ({event.start} - {event.end})",
            metadata={"event_id": event.id, "title": event.title},
            side_effect_summary=f"Created event: {event.title}",
        )

    def _delete_event(args: dict[str, Any]) -> ToolResult:
        event_id = args["event_id"]
        deleted = prov.delete_event(event_id)
        if deleted:
            return ToolResult(
                tool_name="calendar.delete_event",
                success=True,
                output=f"Deleted event {event_id}",
                metadata={"event_id": event_id},
                side_effect_summary=f"Deleted event {event_id}",
            )
        return ToolResult(
            tool_name="calendar.delete_event",
            success=False,
            output="",
            error=f"Event {event_id} not found",
            metadata={"event_id": event_id},
        )

    return {
        "calendar.list_events": _list_events,
        "calendar.create_event": _create_event,
        "calendar.delete_event": _delete_event,
    }
