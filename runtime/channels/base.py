"""Channel protocol — interface all channels implement."""

from __future__ import annotations

from typing import Protocol

from runtime.sessions.manager import SessionManager


class Channel(Protocol):
    """A channel receives messages from an external source and routes them
    through the session manager."""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
