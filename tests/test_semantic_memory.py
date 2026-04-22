"""Tests for explicit semantic memory."""

from config.loader import SemanticMemoryConfig
from core.memory.semantic import SQLiteSemanticMemory, derive_semantic_entries


class TestSemanticMemory:
    def test_store_and_retrieve_preference(self):
        memory = SQLiteSemanticMemory(":memory:")
        try:
            entries = derive_semantic_entries(
                config=SemanticMemoryConfig(),
                user_id="alice",
                user_message="I prefer concise replies.",
                assistant_response="Understood.",
                topic="style",
                event_intensity=0.4,
            )
            for entry in entries:
                memory.store(entry)

            results = memory.retrieve(
                "What kind of replies do I prefer?",
                source_person="alice",
                topic="style",
                limit=5,
            )

            assert any(item.kind == "preference" for item in results)
            assert any("concise replies" in item.summary for item in results)
        finally:
            memory.close()

    def test_turn_write_always_creates_episode(self):
        entries = derive_semantic_entries(
            config=SemanticMemoryConfig(),
            user_id="alice",
            user_message="We should keep onboarding simple.",
            assistant_response="Agreed.",
            topic="onboarding",
            event_intensity=0.3,
        )

        assert any(entry.kind == "episode" for entry in entries)
