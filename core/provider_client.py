"""Provider-neutral chat-completions clients.

Default values still point at the legacy MiniMax path for backward
compatibility, but the module surface is intentionally provider-neutral.
"""

from __future__ import annotations

import os
import re

import requests

from runtime.security import validate_http_url


class ChatCompletionsClient:
    """Sync chat-completions client conforming to the LLMBackend protocol.

    Uses a persistent requests.Session for HTTP connection reuse
    (keep-alive), avoiding repeated TCP + TLS handshakes to the API server.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.minimax.io/v1",
        model: str = "MiniMax-M2.7-highspeed",
        thinking: bool = True,
    ) -> None:
        self._api_key = (
            api_key
            or os.environ.get("LLM_API_KEY", "")
            or os.environ.get("MINIMAX_API_KEY", "")
        )
        safe_base_url = validate_http_url(
            base_url,
            allow_loopback=True,
            allow_private_env="NUR_ALLOW_PRIVATE_LLM_URLS",
        )
        self._base_url = safe_base_url.rstrip("/").removesuffix("/chat/completions")
        self._model = model
        self._thinking = thinking
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        })

    def generate(self, system_prompt: str, user_message: str) -> str:
        """Send a chat completion request and return the response text."""
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        # Disable thinking mode when not needed (faster responses)
        if not self._thinking:
            payload["thinking"] = {"type": "disabled"}

        # Retry once on connection error (server closes idle keep-alive connections)
        try:
            resp = self._session.post(url, json=payload, timeout=60)
        except (requests.ConnectionError, requests.exceptions.ConnectionError):
            resp = self._session.post(url, json=payload, timeout=60)
        if resp.status_code != 200:
            import logging

            logging.getLogger(__name__).error(
                "LLM API error %s: %s", resp.status_code, resp.text[:500]
            )
        resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        return content.strip()

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._session.close()


class FastChatCompletionsClient(ChatCompletionsClient):
    """Chat-completions client with thinking mode disabled."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.minimax.io/v1",
        model: str = "MiniMax-M2.7-highspeed",
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, model=model, thinking=False)
