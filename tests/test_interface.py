"""Tests for the web interface — Phase 7."""

import pytest
from fastapi.testclient import TestClient

from interface.api import app, set_pipeline
from pipeline import CognitivePipeline
from core.dual_process.generator import MockLLMBackend


@pytest.fixture(autouse=True)
def reset_pipeline():
    """Reset the global pipeline before each test."""
    backend = MockLLMBackend(response="Test response.")
    pipe = CognitivePipeline(llm_backend=backend)
    set_pipeline(pipe)
    yield
    set_pipeline(None)


@pytest.fixture
def client():
    return TestClient(app)


class TestChatEndpoint:
    def test_chat_returns_response(self, client):
        resp = client.post("/chat", json={"message": "Hello", "user_id": "alice"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["response"] == "Test response."
        assert "debug" in data

    def test_chat_debug_has_modulators(self, client):
        resp = client.post("/chat", json={"message": "Hello"})
        data = resp.json()
        debug = data["debug"]
        assert "modulator_snapshot" in debug
        assert "arousal" in debug["modulator_snapshot"]
        assert "energy_after" in debug

    def test_chat_debug_has_event(self, client):
        resp = client.post("/chat", json={"message": "I'm so angry!"})
        data = resp.json()
        debug = data["debug"]
        assert debug["event_classified"] != ""
        assert debug["event_intensity"] > 0

    def test_chat_debug_has_profiles(self, client):
        resp = client.post("/chat", json={"message": "Hello", "user_id": "bob"})
        debug = resp.json()["debug"]
        assert debug["person_profile"] is not None
        assert debug["person_profile"]["person_id"] == "bob"
        assert debug["self_profile"] is not None

    def test_chat_default_user_id(self, client):
        resp = client.post("/chat", json={"message": "Hi"})
        debug = resp.json()["debug"]
        assert debug["user_id"] == "default"


class TestDebugEndpoint:
    def test_debug_returns_state(self, client):
        resp = client.get("/debug")
        assert resp.status_code == 200
        data = resp.json()
        assert "modulator_snapshot" in data
        assert "emotion_label" in data
        assert "energy" in data

    def test_debug_reflects_chat(self, client):
        client.post("/chat", json={"message": "Hello"})
        resp = client.get("/debug")
        data = resp.json()
        assert data["short_term_count"] > 0


class TestSessionEndpoint:
    def test_end_session(self, client):
        client.post("/chat", json={"message": "Hello"})
        client.post("/chat", json={"message": "Thanks!"})
        resp = client.post("/session/end", json={"user_id": "default"})
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "trust_delta" in data
        assert "memories_written" in data

    def test_end_session_clears_short_term(self, client):
        client.post("/chat", json={"message": "Hello"})
        client.post("/session/end", json={})
        debug = client.get("/debug").json()
        assert debug["short_term_count"] == 0


class TestRestEndpoint:
    def test_rest_recovers_energy(self, client):
        # Drain some energy
        for i in range(10):
            client.post("/chat", json={"message": f"Message {i}"})
        debug_before = client.get("/debug").json()
        energy_before = debug_before["energy"]

        resp = client.post("/rest", json={"hours": 2.0})
        assert resp.status_code == 200
        data = resp.json()
        assert data["energy_after"] > energy_before


class TestIndexPage:
    def test_serves_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Project Nūr" in resp.text
        assert "<!DOCTYPE html>" in resp.text
