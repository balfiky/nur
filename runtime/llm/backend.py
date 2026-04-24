"""LLM backends for the runtime."""

from __future__ import annotations

import os
from typing import Any

import requests

from core.provider_client import FastChatCompletionsClient
from core.dual_process.generator import LLMBackend, MockLLMBackend


class OpenAICompatibleLLMBackend:
    """Sync OpenAI-compatible chat-completions backend.

    Intended for local/self-hosted runtimes and hosted providers that speak
    the standard ``/chat/completions`` API and do not rely on MiniMax-
    specific request fields such as ``thinking``.
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
        self._base_url = base_url.rstrip("/").removesuffix("/chat/completions")
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
        "mock"               → always MockLLMBackend
        "provider"           → generic hosted-provider / gateway backend
        "openai_compatible"  → local/self-hosted compatible backend
        "minimax"            → legacy MiniMax-specific backend
        "auto"               → generic endpoint if base_url + model configured,
                               else legacy MiniMax if a MiniMax key is
                               available, else Mock
    """
    backend_type = "auto"
    generic_key = os.environ.get("LLM_API_KEY", "")
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    base_url = ""
    model = ""

    if config is not None:
        backend_type = getattr(config, "llm_backend", "auto")
        base_url = getattr(config, "llm_base_url", "")
        model = getattr(config, "llm_model", "")
        configured_generic_key = getattr(config, "llm_api_key", "")
        configured_minimax_key = getattr(config, "minimax_api_key", "")
        generic_key = configured_generic_key or generic_key
        minimax_key = configured_minimax_key or minimax_key

    effective_key = generic_key or minimax_key

    if backend_type == "mock":
        return MockLLMBackend()

    if backend_type in {"provider", "openai_compatible"}:
        return OpenAICompatibleLLMBackend(
            base_url=base_url,
            model=model,
            api_key=effective_key,
        )

    if backend_type == "minimax":
        return FastChatCompletionsClient(
            api_key=effective_key or None,
            base_url=base_url or "https://api.minimax.io/v1",
            model=model or "MiniMax-M2.7-highspeed",
        )

    if backend_type == "auto" and base_url and model:
        return OpenAICompatibleLLMBackend(
            base_url=base_url,
            model=model,
            api_key=effective_key,
        )

    if backend_type == "auto" and minimax_key:
        return FastChatCompletionsClient(api_key=minimax_key or None)

    return MockLLMBackend()
