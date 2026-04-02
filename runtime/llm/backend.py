"""LLM backend factory for the runtime."""

from __future__ import annotations

import os

from core.dual_process.generator import LLMBackend, MockLLMBackend


def create_llm_backend(config=None) -> LLMBackend:
    """Create an LLM backend based on runtime config and environment.

    Backend selection (``config.llm_backend``):
        "mock"    → always MockLLMBackend
        "minimax" → always LLMClientFast (config key or env key)
        "auto"    → LLMClientFast if an API key is available, else Mock
    """
    backend_type = "auto"
    api_key = os.environ.get("MINIMAX_API_KEY", "")

    if config is not None:
        backend_type = getattr(config, "llm_backend", "auto")
        config_key = getattr(config, "minimax_api_key", "")
        if config_key:
            api_key = config_key

    if backend_type == "mock":
        return MockLLMBackend()

    if backend_type == "minimax" or (backend_type == "auto" and api_key):
        from core.llm_client import LLMClientFast

        return LLMClientFast(api_key=api_key or None)

    return MockLLMBackend()
