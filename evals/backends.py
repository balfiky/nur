"""Backend factory for the eval runner.

Strict counterpart to ``runtime.llm.backend.create_llm_backend`` — the
runtime factory falls back to mock when config is missing so the web
UI still works offline; the eval factory MUST NOT do that, because a
"real" eval run that silently degraded to a canned mock would produce
unusable numbers.

If a caller asks for a live backend and the required config is
missing, raise ``MissingBackendConfigError`` at build time. The CLI
surface turns this into a hard exit before any scenarios run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from core.dual_process.generator import LLMBackend, MockLLMBackend
from core.llm_client import LLMClientFast
from runtime.llm.backend import OpenAICompatibleLLMBackend


# ---------------------------------------------------------------------------
# Defaults (kept in sync with runtime/llm/backend.py; changes here should be
# intentional and recorded in provenance.resolved_model).
# ---------------------------------------------------------------------------
DEFAULT_MINIMAX_MODEL = "MiniMax-M2.7-highspeed"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1"


class MissingBackendConfigError(RuntimeError):
    """Raised when a live backend is requested without the config it needs."""


@dataclass(frozen=True)
class BackendSpec:
    """What the CLI asked for; flows into provenance."""
    type: str                               # "mock" | "minimax" | "openai_compat"
    requested_model: str = ""
    base_url: str = ""
    api_key: str = ""                       # resolved from --api-key or env
    temperature: float | None = None
    max_tokens: int | None = None

    @property
    def resolved_model(self) -> str:
        """Model that will actually be sent to the provider."""
        if self.type == "minimax":
            return self.requested_model or DEFAULT_MINIMAX_MODEL
        if self.type == "openai_compat":
            return self.requested_model
        return ""                           # mock

    @property
    def resolved_base_url(self) -> str:
        if self.type == "minimax":
            return self.base_url or DEFAULT_MINIMAX_BASE_URL
        if self.type == "openai_compat":
            return self.base_url
        return ""


def build_backend_factory(spec: BackendSpec) -> Callable[[], LLMBackend]:
    """Return a zero-arg callable that builds a fresh backend per scenario.

    A fresh instance per scenario matters because:
    - scenarios are independent and shouldn't share cache/session state
    - ``LLMClient`` holds an HTTP session we want to close between runs
    - the mock backend accumulates ``last_system_prompt`` across calls
    """
    if spec.type == "mock":
        return lambda: MockLLMBackend()

    if spec.type == "minimax":
        if not spec.api_key:
            raise MissingBackendConfigError(
                "--backend minimax requires an API key. Pass --api-key or "
                "set MINIMAX_API_KEY / LLM_API_KEY in the environment."
            )
        model = spec.resolved_model
        base_url = spec.resolved_base_url
        api_key = spec.api_key
        return lambda: LLMClientFast(api_key=api_key, base_url=base_url, model=model)

    if spec.type == "openai_compat":
        if not spec.base_url:
            raise MissingBackendConfigError(
                "--backend openai_compat requires --base-url."
            )
        if not spec.requested_model:
            raise MissingBackendConfigError(
                "--backend openai_compat requires --model."
            )
        base_url = spec.base_url
        model = spec.requested_model
        api_key = spec.api_key
        return lambda: OpenAICompatibleLLMBackend(
            base_url=base_url, model=model, api_key=api_key,
        )

    raise MissingBackendConfigError(
        f"Unknown backend type: {spec.type!r}. "
        f"Expected one of: mock, minimax, openai_compat."
    )


def spec_from_args(
    *,
    backend: str,
    model: str,
    base_url: str,
    api_key: str,
    api_key_env: str,
    temperature: float | None,
    max_tokens: int | None,
) -> BackendSpec:
    """Build a BackendSpec from CLI args, resolving the API key from env."""
    resolved_key = api_key or (os.environ.get(api_key_env, "") if api_key_env else "")
    if not resolved_key and backend in {"minimax", "openai_compat"}:
        # Last-resort defaults for common env var names.
        resolved_key = (
            os.environ.get("LLM_API_KEY", "")
            or os.environ.get("MINIMAX_API_KEY", "")
        )
    return BackendSpec(
        type=backend,
        requested_model=model,
        base_url=base_url,
        api_key=resolved_key,
        temperature=temperature,
        max_tokens=max_tokens,
    )
