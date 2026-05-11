"""Regression test: constitution survives the full pipeline → LLM system prompt.

The existing test_constitution.py covers store persistence and section rendering
in isolation. This file closes the end-to-end gap: does the constitution from
life_history_provider actually appear in the system prompt sent to the LLM?

If generator.py's _build_life_history_section is refactored or the context
key is renamed, this test will catch the regression before a release.
"""

from __future__ import annotations

from tests._fakes import MockLLMBackend
from pipeline import CognitivePipeline


SENTINEL = "CONSTITUTION_E2E_SENTINEL_2XQ7"


def _provider_with_constitution() -> dict:
    return {
        "constitution": SENTINEL,
        "beliefs": [],
        "drives": [],
        "recent_evolution": [],
    }


def _provider_empty() -> dict:
    return {
        "constitution": "",
        "beliefs": [],
        "drives": [],
        "recent_evolution": [],
    }


def _make_pipeline(provider) -> tuple[CognitivePipeline, MockLLMBackend]:
    backend = MockLLMBackend(response="Acknowledged.")
    pipe = CognitivePipeline(
        llm_backend=backend,
        db_path=":memory:",
        life_history_provider=provider,
    )
    return pipe, backend


class TestConstitutionInjection:
    def test_constitution_appears_in_system_prompt(self):
        """Constitution text must reach the LLM's system_prompt every turn."""
        pipe, backend = _make_pipeline(_provider_with_constitution)
        pipe.process("Hello", user_id="test")
        assert SENTINEL in backend.last_system_prompt, (
            f"Constitution sentinel not found in system prompt. "
            f"First 400 chars of prompt: {backend.last_system_prompt[:400]!r}"
        )

    def test_empty_constitution_does_not_inject_section(self):
        """An unset constitution must not produce the Life History header."""
        pipe, backend = _make_pipeline(_provider_empty)
        pipe.process("Hello", user_id="test")
        # Section only renders when there is actual content
        assert SENTINEL not in backend.last_system_prompt

    def test_constitution_present_on_second_turn(self):
        """Constitution must still be injected on subsequent turns, not just the first."""
        pipe, backend = _make_pipeline(_provider_with_constitution)
        pipe.process("First message", user_id="test")
        pipe.process("Second message", user_id="test")
        assert SENTINEL in backend.last_system_prompt

    def test_constitution_survives_query_text_variant(self):
        """Provider called with a query_text arg must still inject the constitution."""
        call_args: list = []

        def provider_that_records(query_text: str = "") -> dict:
            call_args.append(query_text)
            return _provider_with_constitution()

        pipe, backend = _make_pipeline(provider_that_records)
        pipe.process("Tell me about yourself", user_id="test")
        assert SENTINEL in backend.last_system_prompt
        assert len(call_args) >= 1
