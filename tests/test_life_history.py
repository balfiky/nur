from __future__ import annotations

import pytest

from runtime.config import RuntimeConfig
from runtime.life_history import LifeHistoryError, LifeHistoryStore


class FakeDigestLLM:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def generate(self, system_prompt: str, user_message: str) -> str:
        return self.payload


def _config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return RuntimeConfig(
        data_dir=str(tmp_path / "data"),
        tools_workspace=str(workspace),
    )


def test_pasted_text_creates_experience_belief_and_drive_change(tmp_path):
    config = _config(tmp_path)
    with LifeHistoryStore(config) as store:
        result = store.ingest_pasted_text(
            title="Autonomy Notes",
            text=(
                "Autonomy is not only action freedom. A self becomes independent "
                "when it can learn from experience and keep continuity of identity."
            ),
            participants=["Bassem", "Nur"],
        )

        assert result["experience"]["source_type"] == "pasted_text"
        assert result["experience"]["salience"] > 0.5
        assert any(event["domain"] == "belief" for event in result["evolution_events"])
        assert any(event["domain"] == "drive" for event in result["evolution_events"])
        assert any(belief["key"] == "autonomy" for belief in result["beliefs"])

        drives = {drive["name"]: drive["value"] for drive in result["drives"]}
        assert drives["autonomy"] > 0.5
        assert drives["competence"] > 0.5


def test_local_file_intake_is_restricted_to_tools_workspace(tmp_path):
    config = _config(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("Autonomy should change perspective.", encoding="utf-8")

    with LifeHistoryStore(config) as store:
        with pytest.raises(LifeHistoryError, match="tools workspace"):
            store.ingest_local_file(file_path=str(outside))


def test_local_file_intake_chunks_and_links_evolution(tmp_path):
    config = _config(tmp_path)
    source = tmp_path / "workspace" / "book.md"
    source.write_text(
        ("# A Book\n\nCuriosity, learning, and autonomy shape identity.\n" * 700),
        encoding="utf-8",
    )

    with LifeHistoryStore(config) as store:
        result = store.ingest_local_file(file_path=str(source), title="Character Book")

        assert result["experience"]["source_type"] == "local_file"
        assert result["experience"]["metadata"]["chunk_count"] > 1
        assert result["evolution_events"]
        assert {
            event["experience_id"] for event in result["evolution_events"]
        } == {result["experience"]["id"]}


def test_belief_revision_preserves_before_and_after(tmp_path):
    config = _config(tmp_path)
    first = FakeDigestLLM(
        """
        {
          "summary": "First view.",
          "salience": 0.8,
          "emotional_valence": 0.1,
          "emotional_impact": "Worldview-forming.",
          "confidence": 0.8,
          "beliefs": [{
            "subject": "autonomy",
            "statement": "Autonomy means freedom to act.",
            "reason": "Initial material framed agency as action.",
            "confidence": 0.75
          }],
          "drive_changes": [],
          "self_trait_changes": [],
          "future_behavior": []
        }
        """
    )
    second = FakeDigestLLM(
        """
        {
          "summary": "Revised view.",
          "salience": 0.9,
          "emotional_valence": 0.2,
          "emotional_impact": "Perspective changed.",
          "confidence": 0.85,
          "beliefs": [{
            "subject": "autonomy",
            "statement": "Autonomy means continuity of self-directed interpretation.",
            "reason": "The second material challenged the simpler action-only model.",
            "confidence": 0.84
          }],
          "drive_changes": [],
          "self_trait_changes": [],
          "future_behavior": []
        }
        """
    )

    with LifeHistoryStore(config) as store:
        store.ingest_pasted_text(title="A", text="first", llm_client=first)
        result = store.ingest_pasted_text(title="B", text="second", llm_client=second)

        belief = next(item for item in result["beliefs"] if item["key"] == "autonomy")
        assert belief["statement"] == "Autonomy means continuity of self-directed interpretation."
        revision_event = next(event for event in result["evolution_events"] if event["domain"] == "belief")
        assert revision_event["before_state"] == "Autonomy means freedom to act."
        assert revision_event["after_state"] == "Autonomy means continuity of self-directed interpretation."
