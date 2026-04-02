"""LLM backend factory for the runtime."""

from __future__ import annotations

import os

from core.dual_process.generator import LLMBackend, MockLLMBackend


def create_llm_backend() -> LLMBackend:
    """Create an LLM backend based on environment.

    Returns a real MiniMax client if MINIMAX_API_KEY is set,
    otherwise a MockLLMBackend (for testing / offline use).
    """
    if os.environ.get("MINIMAX_API_KEY"):
        from core.llm_client import LLMClientFast
        return LLMClientFast()
    return MockLLMBackend()
