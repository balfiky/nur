"""LLM backends for the runtime."""

from __future__ import annotations

import os
from typing import Any

import requests

from core.llm_client import LLMClientFast
from core.dual_process.generator import LLMBackend, MockLLMBackend


class OpenAICompatibleLLMBackend:
    """Sync OpenAI-compatible chat-completions backend.

    Intended for local vLLM / llama.cpp / proxy endpoints that speak the
    standard ``/chat/completions`` API but do not understand MiniMax-specific
    request fields such as ``thinking``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 60.0,
    ) -> None:
        if not base_url:
            raise ValueError("OpenAI-compatible backend requires llm_base_url")
        if not model:
            raise ValueError("OpenAI-compatible backend requires llm_model")
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._session = requests.Session()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._session.headers.update(headers)

    def generate(self, system_prompt: str, user_message: str) -> str:
        url = f"{self._base_url}/chat/completions"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        response = self._session.post(url, json=payload, timeout=self._timeout)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data["choices"][0]["message"]["content"].strip()


def create_llm_backend(config=None) -> LLMBackend:
    """Create an LLM backend based on runtime config and environment.

    Backend selection (``config.llm_backend``):
        "mock"    → always MockLLMBackend
        "minimax" → always LLMClientFast (config key or env key)
        "openai_compatible" → configurable sync OpenAI-compatible backend
        "auto"    → OpenAI-compatible if base_url + model configured,
                    else MiniMax if an API key is available, else Mock
    """
    backend_type = "auto"
    api_key = os.environ.get("LLM_API_KEY", "") or os.environ.get("MINIMAX_API_KEY", "")
    base_url = ""
    model = ""

    if config is not None:
        backend_type = getattr(config, "llm_backend", "auto")
        base_url = getattr(config, "llm_base_url", "")
        model = getattr(config, "llm_model", "")
        generic_key = getattr(config, "llm_api_key", "")
        config_key = getattr(config, "minimax_api_key", "")
        api_key = generic_key or config_key or api_key

    if backend_type == "mock":
        return MockLLMBackend()

    if backend_type == "openai_compatible":
        return OpenAICompatibleLLMBackend(
            base_url=base_url,
            model=model,
            api_key=api_key,
        )

    if backend_type == "minimax":
        return LLMClientFast(
            api_key=api_key or None,
            base_url=base_url or "https://api.minimax.io/v1",
            model=model or "MiniMax-M2.7-highspeed",
        )

    if backend_type == "auto" and base_url and model:
        return OpenAICompatibleLLMBackend(
            base_url=base_url,
            model=model,
            api_key=api_key,
        )

    if backend_type == "auto" and api_key:
        return LLMClientFast(api_key=api_key or None)

    return MockLLMBackend()
