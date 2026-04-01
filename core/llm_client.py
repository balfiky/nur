"""LLM client for MiniMax API (OpenAI-compatible chat completion format).

Default model: MiniMax-M2.7-highspeed (Plus-Highspeed token plan).
Uses the requests library directly — no SDK dependency.
"""

from __future__ import annotations

import os
import re

import requests


class LLMClient:
    """MiniMax API client conforming to the LLMBackend protocol."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.minimax.io/v1",
        model: str = "MiniMax-M2.7-highspeed",
    ) -> None:
        self._api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        self._base_url = base_url.rstrip("/")
        self._model = model

    def generate(self, system_prompt: str, user_message: str) -> str:
        """Send a chat completion request and return the response text."""
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        # Strip MiniMax M2.1 reasoning tags
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        return content.strip()
