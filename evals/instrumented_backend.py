"""Backend counting wrapper for accurate per-scenario LLM-call telemetry.

The eval runner used to count LLM calls by ``dialogue_trace.total_llm_calls
+ 1`` per turn, assuming the generator fires exactly once. That assumption
is wrong: if the self-check fails, the pipeline regenerates, and that
second call was invisible to the counter. The published ``llm_calls``
numbers were undercounted whenever a self-check retry fired.

This module fixes the count by wrapping every backend the runner builds
in an ``InstrumentedBackend`` that forwards ``.generate()`` calls and
increments a shared per-scenario ``BackendCounter``. A single scenario
can build multiple backends (one per unique user_id via the runner's
``get_pipeline``); the shared counter aggregates across all of them.

What this wrapper does NOT capture:

- Internal retries inside ``LLMClient`` (the client retries once on
  connection error before re-raising). Those happen below the wrapper.
- Provider-reported token usage (``prompt_tokens`` / ``completion_tokens``).
  The clients currently discard the ``usage`` block.

Capturing those requires modifying the clients themselves. That is a
deliberate later pass — see ``RunProvenance`` field comments.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.dual_process.generator import LLMBackend


@dataclass
class BackendCounter:
    """Aggregates generate-call counts across all backends in one scenario."""
    generate_calls: int = 0


class InstrumentedBackend:
    """Wraps an ``LLMBackend`` and counts every ``generate()`` invocation.

    Forwards ``close()`` (and any other transparent attribute access via
    ``__getattr__``) to the inner backend so HTTP sessions still close
    properly when the pipeline shuts down.
    """

    def __init__(self, inner: LLMBackend, counter: BackendCounter) -> None:
        self._inner = inner
        self._counter = counter

    def generate(self, system_prompt: str, user_message: str) -> str:
        self._counter.generate_calls += 1
        return self._inner.generate(system_prompt, user_message)

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()

    def __getattr__(self, name: str):
        # Forward attribute access to the inner backend (e.g., MockLLMBackend
        # exposes ``last_system_prompt`` for tests). __getattr__ is only
        # called when normal lookup fails, so this won't shadow our methods.
        return getattr(self._inner, name)
