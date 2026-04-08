"""Tests for the LLM client — MiniMax API."""

import json
import pytest
from unittest.mock import patch, MagicMock

from core.llm_client import LLMClient, LLMClientFast


class TestLLMClient:
    def _mock_response(self, content: str = "Hello!") -> MagicMock:
        """Create a mock requests.Response with OpenAI-compatible format."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return mock_resp

    @patch("requests.Session.post")
    def test_generate_success(self, mock_post):
        mock_post.return_value = self._mock_response("Test response")
        client = LLMClient(api_key="test-key")
        result = client.generate("System prompt", "User message")
        assert result == "Test response"

    @patch("requests.Session.post")
    def test_sends_correct_payload(self, mock_post):
        mock_post.return_value = self._mock_response()
        client = LLMClient(api_key="test-key", model="MiniMax-M2.1")
        client.generate("You are Jarvis.", "Hello")

        call_args = mock_post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["model"] == "MiniMax-M2.1"
        assert payload["messages"][0] == {"role": "system", "content": "You are Jarvis."}
        assert payload["messages"][1] == {"role": "user", "content": "Hello"}

    def test_sends_auth_header(self):
        client = LLMClient(api_key="my-secret-key")
        assert client._session.headers["Authorization"] == "Bearer my-secret-key"
        assert client._session.headers["Content-Type"] == "application/json"

    @patch("requests.Session.post")
    def test_correct_url(self, mock_post):
        mock_post.return_value = self._mock_response()
        client = LLMClient(api_key="k", base_url="https://api.minimax.io/v1")
        client.generate("sys", "msg")

        url = mock_post.call_args[0][0]
        assert url == "https://api.minimax.io/v1/chat/completions"

    @patch("requests.Session.post")
    def test_http_error_raises(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = Exception("Internal Server Error")
        mock_post.return_value = mock_resp

        client = LLMClient(api_key="k")
        with pytest.raises(Exception, match="Internal Server Error"):
            client.generate("sys", "msg")

    @patch.dict("os.environ", {"MINIMAX_API_KEY": "env-key"})
    def test_api_key_from_env(self):
        client = LLMClient()  # no explicit key
        assert client._session.headers["Authorization"] == "Bearer env-key"

    @patch("requests.Session.post")
    def test_custom_base_url(self, mock_post):
        mock_post.return_value = self._mock_response()
        client = LLMClient(api_key="k", base_url="http://localhost:8080/v1")
        client.generate("sys", "msg")

        url = mock_post.call_args[0][0]
        assert url == "http://localhost:8080/v1/chat/completions"

    @patch("requests.Session.post")
    def test_strips_think_tags(self, mock_post):
        mock_post.return_value = self._mock_response(
            "<think>\nLet me reason about this...\nThe user wants X.\n</think>\nHere is my answer."
        )
        client = LLMClient(api_key="k")
        result = client.generate("sys", "msg")
        assert result == "Here is my answer."
        assert "<think>" not in result

    @patch("requests.Session.post")
    def test_strips_multiple_think_tags(self, mock_post):
        mock_post.return_value = self._mock_response(
            "<think>first</think> Hello <think>second</think> world"
        )
        client = LLMClient(api_key="k")
        result = client.generate("sys", "msg")
        assert result == "Hello  world"
        assert "<think>" not in result

    @patch("requests.Session.post")
    def test_no_think_tags_unchanged(self, mock_post):
        mock_post.return_value = self._mock_response("Just a normal response.")
        client = LLMClient(api_key="k")
        result = client.generate("sys", "msg")
        assert result == "Just a normal response."

    @patch("requests.Session.post")
    def test_conforms_to_llm_backend_protocol(self, mock_post):
        """LLMClient must work wherever LLMBackend is expected."""
        mock_post.return_value = self._mock_response("response")
        from core.dual_process.generator import ResponseGenerator
        from core.types import PipelineContext

        client = LLMClient(api_key="k")
        gen = ResponseGenerator(backend=client)
        result = gen.generate(PipelineContext(), "Hello")
        assert result.response == "response"


class TestLLMClientFast:
    def _mock_response(self, content: str = "Hello!") -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return mock_resp

    def test_thinking_disabled(self):
        client = LLMClientFast(api_key="k")
        assert client._thinking is False

    @patch("requests.Session.post")
    def test_sends_thinking_disabled(self, mock_post):
        mock_post.return_value = self._mock_response("Fast response")
        client = LLMClientFast(api_key="k")
        client.generate("sys", "msg")

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["thinking"] == {"type": "disabled"}

    @patch("requests.Session.post")
    def test_thinking_enabled_by_default(self, mock_post):
        """Regular LLMClient does NOT send thinking=disabled."""
        mock_post.return_value = self._mock_response("Full response")
        client = LLMClient(api_key="k")
        client.generate("sys", "msg")

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        assert "thinking" not in payload

    def test_close_releases_session(self):
        """LLMClient.close() must release the underlying requests.Session
        so session evictions do not leak HTTP connection pools."""
        client = LLMClient(api_key="k")
        with patch.object(client._session, "close") as mock_close:
            client.close()
            mock_close.assert_called_once()
