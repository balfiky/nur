"""Minimal Python client for the Project Nūr v1 integration API.

This is a thin wrapper over ``requests`` — no extra dependencies, no
abstractions. It exists so integrators can do::

    from interface.client import NurClient

    nur = NurClient("http://localhost:8000", api_key="…")
    reply = nur.chat("how are you?", user_id="alice")
    print(reply["response"], reply["emotion_label"])

…without having to remember endpoint paths or auth header formats.

The client is synchronous. For async integrations, call it from a thread
pool or adapt this module to ``httpx.AsyncClient`` — the shapes returned
from the server are stable.
"""

from __future__ import annotations

from typing import Any

import requests


class NurAPIError(RuntimeError):
    """Raised when the server returns a non-2xx response."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Nur API error {status_code}: {body[:500]}")
        self.status_code = status_code
        self.body = body


class NurClient:
    """Thin v1 API client. Reuses a :class:`requests.Session` for keep-alive."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        api_key: str | None = None,
        timeout: float = 60.0,
        user_id: str = "default",
        chat_id: str = "default",
        platform: str = "web",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._default_user = user_id
        self._default_chat = chat_id
        self._default_platform = platform
        self._session = requests.Session()
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

    # ------------------------------------------------------------------
    # Core HTTP helper
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}"
        resp = self._session.request(method, url, timeout=self._timeout, **kwargs)
        if resp.status_code >= 400:
            raise NurAPIError(resp.status_code, resp.text)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def close(self) -> None:
        """Release the keep-alive pool. Safe to call multiple times."""
        self._session.close()

    # Context-manager sugar so ``with NurClient(...) as nur:`` works.
    def __enter__(self) -> "NurClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Health and readiness (no auth)
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def ready(self) -> dict[str, Any]:
        return self._request("GET", "/v1/ready")

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def chat(
        self,
        message: str,
        *,
        user_id: str | None = None,
        chat_id: str | None = None,
        platform: str | None = None,
        include_debug: bool = False,
    ) -> dict[str, Any]:
        body = {
            "message": message,
            "user_id": user_id or self._default_user,
            "chat_id": chat_id or self._default_chat,
            "platform": platform or self._default_platform,
            "include_debug": include_debug,
        }
        return self._request("POST", "/v1/chat", json=body)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def list_sessions(self) -> dict[str, Any]:
        return self._request("GET", "/v1/sessions")

    def get_session(self, session_key: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/sessions/{session_key}")

    def reset_session(self, session_key: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/sessions/{session_key}/reset")

    def delete_user(
        self,
        user_id: str,
        *,
        platform: str | None = None,
    ) -> dict[str, Any]:
        """Delete all persisted data for ``platform:user_id``.

        Evicts live sessions, wipes the per-user DB and session JSONs,
        and leaves the shared self-model DB untouched.
        """
        plat = platform or self._default_platform
        return self._request("DELETE", f"/v1/users/{plat}/{user_id}")

    def end_session(
        self,
        *,
        user_id: str | None = None,
        chat_id: str | None = None,
        platform: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "user_id": user_id or self._default_user,
            "chat_id": chat_id or self._default_chat,
            "platform": platform or self._default_platform,
        }
        return self._request("POST", "/v1/sessions/end", json=body)

    def rest_session(
        self,
        hours: float = 1.0,
        *,
        user_id: str | None = None,
        chat_id: str | None = None,
        platform: str | None = None,
    ) -> dict[str, Any]:
        body = {
            "hours": hours,
            "user_id": user_id or self._default_user,
            "chat_id": chat_id or self._default_chat,
            "platform": platform or self._default_platform,
        }
        return self._request("POST", "/v1/sessions/rest", json=body)

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def self_profile(
        self,
        *,
        user_id: str | None = None,
        chat_id: str | None = None,
        platform: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "user_id": user_id or self._default_user,
            "chat_id": chat_id or self._default_chat,
            "platform": platform or self._default_platform,
        }
        return self._request("GET", "/v1/profiles/self", params=params)

    def person_profile(
        self,
        user_id: str,
        *,
        chat_id: str | None = None,
        platform: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "user_id": user_id,
            "chat_id": chat_id or self._default_chat,
            "platform": platform or self._default_platform,
        }
        return self._request("GET", "/v1/profiles/person", params=params)

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------

    def long_term_memory(
        self,
        user_id: str,
        *,
        limit: int = 20,
        chat_id: str | None = None,
        platform: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "user_id": user_id,
            "chat_id": chat_id or self._default_chat,
            "platform": platform or self._default_platform,
            "limit": limit,
        }
        return self._request("GET", "/v1/memory/long_term", params=params)

    def relationship_memory(
        self,
        user_id: str,
        *,
        topic: str = "",
        event_limit: int = 10,
        loop_limit: int = 10,
        chat_id: str | None = None,
        platform: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "user_id": user_id,
            "chat_id": chat_id or self._default_chat,
            "platform": platform or self._default_platform,
            "topic": topic,
            "event_limit": event_limit,
            "loop_limit": loop_limit,
        }
        return self._request("GET", "/v1/memory/relationship", params=params)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def list_tools(
        self,
        *,
        user_id: str | None = None,
        chat_id: str | None = None,
        platform: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "user_id": user_id or self._default_user,
            "chat_id": chat_id or self._default_chat,
            "platform": platform or self._default_platform,
        }
        return self._request("GET", "/v1/tools", params=params)

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def get_config(self) -> dict[str, Any]:
        return self._request("GET", "/v1/config")
