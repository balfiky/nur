"""LLM backends for the runtime."""

from __future__ import annotations

import os
import re
from typing import Any

import requests

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

from core.provider_client import FastChatCompletionsClient
from core.dual_process.generator import LLMBackend, MockLLMBackend
from runtime.security import validate_http_url


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
        timeout: float = 120.0,
    ) -> None:
        if not base_url:
            raise ValueError("OpenAI-compatible backend requires llm_base_url")
        if not model:
            raise ValueError("OpenAI-compatible backend requires llm_model")
        safe_base_url = validate_http_url(
            base_url,
            allow_loopback=True,
            allow_private_env="NUR_ALLOW_PRIVATE_LLM_URLS",
        )
        self._base_url = safe_base_url.rstrip("/").removesuffix("/chat/completions")
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
            "max_tokens": 2048,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        response = self._session.post(url, json=payload, timeout=self._timeout)
        if _should_retry_without_chat_template_kwargs(response):
            retry_payload = {
                key: value
                for key, value in payload.items()
                if key != "chat_template_kwargs"
            }
            response = self._session.post(url, json=retry_payload, timeout=self._timeout)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        content = data["choices"][0]["message"]["content"] or ""
        content = _THINK_RE.sub("", content).strip()
        return content

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._session.close()


def _should_retry_without_chat_template_kwargs(response: requests.Response) -> bool:
    """Retry strict OpenAI-compatible providers that reject vLLM extras."""
    if response.status_code not in (400, 422):
        return False
    text = response.text.lower()
    return (
        "chat_template_kwargs" in text
        or "extra" in text
        or "unknown" in text
        or "unrecognized" in text
        or "forbidden" in text
    )


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
