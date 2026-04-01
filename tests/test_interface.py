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
    with TestClient(app) as c:
        yield c


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


class TestV2DebugFields:
    """Verify v2 debug fields are present in chat and debug endpoints."""

    def test_chat_has_anticipation(self, client):
        debug = client.post("/chat", json={"message": "Hello"}).json()["debug"]
        assert "anticipation" in debug
        ant = debug["anticipation"]
        assert ant is not None
        assert "predicted_topics" in ant
        assert "confidence" in ant
        assert "basis" in ant

    def test_chat_has_dialogue_trace(self, client):
        debug = client.post("/chat", json={"message": "Hello"}).json()["debug"]
        assert "dialogue_trace" in debug
        trace = debug["dialogue_trace"]
        assert trace is not None
        assert "rounds" in trace
        # Calm messages may have 0 rounds (inner dialogue skipped)
        assert isinstance(trace["rounds"], list)
        assert "tension_level" in trace
        assert "dominant_path" in trace
        assert "total_llm_calls" in trace

    def test_chat_has_defense_field(self, client):
        debug = client.post("/chat", json={"message": "Hello"}).json()["debug"]
        assert "defense_activation" in debug
        # Calm message — defense should be None
        assert debug["defense_activation"] is None

    def test_chat_has_unresolved_count(self, client):
        debug = client.post("/chat", json={"message": "Hello"}).json()["debug"]
        assert "unresolved_count" in debug
        assert isinstance(debug["unresolved_count"], int)

    def test_chat_has_unresolved_items(self, client):
        debug = client.post("/chat", json={"message": "Hello"}).json()["debug"]
        assert "unresolved_items" in debug
        assert isinstance(debug["unresolved_items"], list)

    def test_chat_has_resolution_in_snapshot(self, client):
        debug = client.post("/chat", json={"message": "Hello"}).json()["debug"]
        assert "resolution" in debug["modulator_snapshot"]

    def test_debug_endpoint_has_resolution(self, client):
        resp = client.get("/debug").json()
        assert "resolution" in resp["modulator_snapshot"]
        assert "unresolved_count" in resp
        assert "unresolved_items" in resp

    def test_spike_creates_unresolved_in_debug(self, client):
        debug = client.post(
            "/chat",
            json={"message": "You betrayed and deceived me completely!"},
        ).json()["debug"]
        if debug["is_spike"]:
            assert debug["unresolved_count"] > 0
            assert len(debug["unresolved_items"]) > 0
            item = debug["unresolved_items"][0]
            assert "source" in item
            assert "intensity" in item
            assert "decay_rate" in item


class TestIndexPage:
    def test_serves_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Project Nūr" in resp.text
        assert "<!DOCTYPE html>" in resp.text

    def test_has_v2_sections(self, client):
        html = client.get("/").text
        assert "Anticipation" in html
        assert "Inner Dialogue" in html
        assert "Defense" in html
        assert "Unresolved Items" in html
        assert "Resolution" in html
