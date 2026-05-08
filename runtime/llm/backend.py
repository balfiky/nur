"""LLM backends for the runtime."""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import tempfile
from typing import Any

import requests

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

from core.provider_client import FastChatCompletionsClient
from core.dual_process.generator import LLMBackend
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


class CodexCLIBackend:
    """LLM backend that delegates generation to ``codex exec``.

    The Codex CLI is agentic, so this wrapper runs it in read-only,
    ephemeral mode and captures only the final message. By default each
    call uses a temporary working directory so normal chat turns do not give
    Codex a project workspace to edit or inspect. Operators can opt into a
    stable read-only workdir with ``NUR_CODEX_WORKDIR``.
    """

    def __init__(
        self,
        *,
        model: str = "",
        timeout: float | None = None,
        executable: str | None = None,
        workdir: str | None = None,
    ) -> None:
        self._executable = executable or os.environ.get("NUR_CODEX_BIN", "codex")
        if not shutil.which(self._executable):
            raise ValueError("Codex backend requires the codex CLI on PATH")
        self._model = (model or "").strip()
        self._timeout = _codex_timeout(timeout)
        self._workdir = (workdir or os.environ.get("NUR_CODEX_WORKDIR", "")).strip()
        if self._workdir and not os.path.isdir(self._workdir):
            raise ValueError(f"NUR_CODEX_WORKDIR does not exist: {self._workdir}")

    def generate(self, system_prompt: str, user_message: str) -> str:
        with tempfile.TemporaryDirectory(prefix="nur-codex-") as tmpdir:
            workdir = self._workdir or tmpdir
            output_path = os.path.join(tmpdir, "last_message.txt")
            cmd = [
                self._executable,
                "exec",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-rules",
                "--color",
                "never",
                "--output-last-message",
                output_path,
                "-C",
                workdir,
            ]
            if self._model:
                cmd.extend(["--model", self._model])
            cmd.append("-")
            try:
                result = subprocess.run(
                    cmd,
                    input=_codex_prompt(system_prompt, user_message),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self._timeout,
                    cwd=workdir,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Codex backend timed out after {self._timeout:.0f}s") from exc
            if result.returncode != 0:
                detail = _trim_error(result.stderr or result.stdout)
                raise RuntimeError(f"Codex backend failed with exit code {result.returncode}: {detail}")
            content = ""
            try:
                with open(output_path, encoding="utf-8") as handle:
                    content = handle.read()
            except FileNotFoundError:
                content = result.stdout
            content = content.strip()
            if not content:
                raise RuntimeError("Codex backend returned an empty response")
            return content


def codex_cli_available(executable: str | None = None) -> bool:
    """Return whether the configured Codex CLI executable is available."""
    return shutil.which(executable or os.environ.get("NUR_CODEX_BIN", "codex")) is not None


def list_codex_models(
    executable: str | None = None,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """Return the safe Codex model catalog for UI selection."""
    exe = executable or os.environ.get("NUR_CODEX_BIN", "codex")
    if not shutil.which(exe):
        raise ValueError("Codex backend requires the codex CLI on PATH")
    catalog_timeout = timeout if timeout is not None else 20.0
    try:
        result = subprocess.run(
            [exe, "debug", "models"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=catalog_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Codex model catalog timed out after {catalog_timeout:.0f}s") from exc

    if result.returncode == 0:
        return _normalize_codex_model_catalog(result.stdout)

    cached = _read_codex_models_cache()
    if cached:
        return _normalize_codex_model_catalog(cached)
    detail = _trim_error(result.stderr or result.stdout)
    raise RuntimeError(f"Codex model catalog failed with exit code {result.returncode}: {detail}")


def _codex_timeout(timeout: float | None) -> float:
    if timeout is not None:
        return timeout
    raw = os.environ.get("NUR_CODEX_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return 300.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 300.0


def _read_codex_models_cache() -> str:
    cache_path = os.environ.get("NUR_CODEX_MODELS_CACHE", "").strip()
    path = cache_path or os.path.expanduser("~/.codex/models_cache.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _normalize_codex_model_catalog(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("Codex model catalog returned invalid JSON") from exc
    models = data.get("models", []) if isinstance(data, dict) else []
    if not isinstance(models, list):
        raise RuntimeError("Codex model catalog did not contain a models list")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug or slug in seen:
            continue
        visibility = str(item.get("visibility") or "list").strip()
        if visibility and visibility != "list":
            continue
        seen.add(slug)
        normalized.append({
            "slug": slug,
            "display_name": _codex_catalog_text(item.get("display_name")) or slug,
            "description": _codex_catalog_text(item.get("description")),
            "default_reasoning_level": _codex_catalog_text(item.get("default_reasoning_level")),
            "supported_reasoning_levels": _codex_reasoning_levels(item.get("supported_reasoning_levels")),
        })
    return normalized


def _codex_catalog_text(value: Any) -> str:
    return str(value or "").strip()


def _codex_reasoning_levels(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    levels: list[str] = []
    for item in value:
        if isinstance(item, dict):
            effort = _codex_catalog_text(item.get("effort"))
        else:
            effort = _codex_catalog_text(item)
        if effort:
            levels.append(effort)
    return levels


def _codex_prompt(system_prompt: str, user_message: str) -> str:
    return (
        "You are acting as Nūr's response-generation backend.\n"
        "Return only the assistant response that should be shown to the user.\n"
        "Do not edit files, do not run commands, and do not mention this backend wrapper.\n\n"
        "Nūr system prompt:\n"
        "<system_prompt>\n"
        f"{system_prompt}\n"
        "</system_prompt>\n\n"
        "User message:\n"
        "<user_message>\n"
        f"{user_message}\n"
        "</user_message>\n"
    )


def _trim_error(text: str, limit: int = 1200) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


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

    Nūr requires a real LLM backend to operate. There is no mock or
    fallback path — when intelligence is required, the configured model is
    called. The factory raises ``ValueError`` if no usable backend can be
    constructed from the provided config.

    Backend selection (``config.llm_backend``):
        "provider"           → generic hosted-provider / gateway backend
        "openai_compatible"  → local/self-hosted compatible backend
        "codex"              → local Codex CLI backend
        "minimax"            → legacy MiniMax-specific backend using llm_api_key
        "auto"               → openai_compatible if base_url + model configured;
                               raises otherwise
    """
    backend_type = "auto"
    generic_key = os.environ.get("LLM_API_KEY", "")
    base_url = ""
    model = ""

    if config is not None:
        backend_type = getattr(config, "llm_backend", "auto")
        base_url = getattr(config, "llm_base_url", "")
        model = getattr(config, "llm_model", "")
        configured_generic_key = getattr(config, "llm_api_key", "")
        generic_key = configured_generic_key or generic_key

    effective_key = generic_key

    if backend_type == "mock":
        raise ValueError(
            "llm_backend='mock' is no longer supported. Nūr requires a real "
            "LLM backend (provider, openai_compatible, codex, or minimax). "
            "Configure llm_backend in runtime_config.yaml."
        )

    if backend_type == "codex":
        return CodexCLIBackend(model=model)

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

    if backend_type == "auto":
        if base_url and model:
            return OpenAICompatibleLLMBackend(
                base_url=base_url,
                model=model,
                api_key=effective_key,
            )
        raise ValueError(
            "llm_backend='auto' requires llm_base_url and llm_model to be "
            "configured. Set them in runtime_config.yaml or specify an "
            "explicit backend (provider, openai_compatible, codex, minimax)."
        )

    raise ValueError(
        f"Unknown llm_backend value: {backend_type!r}. "
        "Valid options: provider, openai_compatible, codex, minimax, auto."
    )
