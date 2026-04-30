from __future__ import annotations

import json

import pytest

from runtime.config import RuntimeConfig
from runtime.learning_intake import (
    LearningIntakeError,
    detect_learning_request,
    ingest_learning_from_message,
)
from runtime.life_history import LifeHistoryStore


def _config(tmp_path):
    return RuntimeConfig(data_dir=str(tmp_path / "data"), llm_backend="mock")


class FakeWebProvider:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.closed = False
        self.fetches: list[str] = []
        self.extracts: list[str] = []

    def extract_text(self, url: str) -> str:
        self.extracts.append(url)
        return self.text

    def fetch(self, url: str) -> str:
        self.fetches.append(url)
        if url == "https://api.github.com/repos/NousResearch/hermes-agent":
            return json.dumps({
                "full_name": "NousResearch/hermes-agent",
                "description": "A self-learning agent project.",
                "topics": ["agents", "self-learning"],
            })
        if url == "https://api.github.com/repos/NousResearch/hermes-agent/readme":
            return json.dumps({
                "download_url": (
                    "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/README.md"
                )
            })
        if url == "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/README.md":
            return (
                "# Hermes Agent\n\n"
                "Hermes Agent emphasizes autonomous learning, self-improvement, "
                "tool use, replay, and reflection from experience. "
                "Its methodology makes competence and autonomy central to identity."
            )
        raise AssertionError(f"unexpected fetch: {url}")

    def close(self) -> None:
        self.closed = True


def test_detect_learning_request_requires_explicit_learning_intent():
    assert detect_learning_request("search for top PyTorch books released in 2026") is None
    assert detect_learning_request("I learned that this project uses replay buffers.") is None

    req = detect_learning_request("I want you to learn from https://example.com/a.")
    assert req is not None
    assert req.urls == ("https://example.com/a",)

    with pytest.raises(LearningIntakeError, match="URL or a longer pasted text"):
        detect_learning_request("learn from this")


def test_url_learning_intake_writes_life_history(tmp_path):
    config = _config(tmp_path)
    provider = FakeWebProvider(
        "Autonomy and learning should change identity through experience. "
        "A self improves through practice, procedure, curiosity, and continuity. " * 3
    )

    result = ingest_learning_from_message(
        config,
        "Please learn from this https://example.com/autonomy-guide",
        actor="alice",
        web_provider=provider,
        llm_client_factory=lambda _config: None,
    )

    assert result is not None
    assert result.source_type == "conversation_learning_url"
    assert result.source_ref == "https://example.com/autonomy-guide"
    assert result.total_evolution_events > 0
    assert provider.extracts == ["https://example.com/autonomy-guide"]

    with LifeHistoryStore(config) as store:
        experiences = store.list_experiences(limit=5)
        assert experiences[0]["source_ref"] == "https://example.com/autonomy-guide"
        assert experiences[0]["source_type"] == "conversation_learning_url"
        context = store.prompt_context()
        assert context["counts"]["experiences"] == 1
        assert context["beliefs"]


def test_inline_learning_intake_writes_life_history(tmp_path):
    config = _config(tmp_path)
    text = (
        "Autonomy, curiosity, and learning from experience should shape future "
        "behavior. A character changes when formative material revises its "
        "worldview and the drives it carries forward."
    )

    result = ingest_learning_from_message(
        config,
        f"digest: {text}",
        actor="alice",
        web_provider=FakeWebProvider(),
        llm_client_factory=lambda _config: None,
    )

    assert result is not None
    assert result.source_type == "conversation_learning_text"
    assert result.source_ref == "conversation"

    with LifeHistoryStore(config) as store:
        experience = store.list_experiences(limit=1)[0]
        assert experience["source_type"] == "conversation_learning_text"
        assert "Autonomy" in experience["raw_excerpt"]


def test_github_repo_learning_reads_readme_before_life_ingest(tmp_path):
    config = _config(tmp_path)
    provider = FakeWebProvider()

    result = ingest_learning_from_message(
        config,
        "I want you to learn from this project https://github.com/NousResearch/hermes-agent",
        actor="alice",
        web_provider=provider,
        llm_client_factory=lambda _config: None,
    )

    assert result is not None
    assert result.title == "NousResearch/hermes-agent"
    assert result.source_ref == "https://github.com/NousResearch/hermes-agent"
    assert "https://api.github.com/repos/NousResearch/hermes-agent" in provider.fetches
    assert (
        "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/README.md"
        in provider.fetches
    )

    with LifeHistoryStore(config) as store:
        experience = store.list_experiences(limit=1)[0]
        assert experience["source_title"] == "NousResearch/hermes-agent"
        assert "README" in experience["raw_excerpt"]
