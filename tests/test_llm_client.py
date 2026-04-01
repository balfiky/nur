"""Tests for the LLM client — MiniMax API."""

import json
import pytest
from unittest.mock import patch, MagicMock

from core.llm_client import LLMClient


class TestLLMClient:
    def _mock_response(self, content: str = "Hello!") -> MagicMock:
        """Create a mock requests.Response with OpenAI-compatible format."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return mock_resp

    @patch("core.llm_client.requests.post")
    def test_generate_success(self, mock_post):
        mock_post.return_value = self._mock_response("Test response")
        client = LLMClient(api_key="test-key")
        result = client.generate("System prompt", "User message")
        assert result == "Test response"

    @patch("core.llm_client.requests.post")
    def test_sends_correct_payload(self, mock_post):
        mock_post.return_value = self._mock_response()
        client = LLMClient(api_key="test-key", model="MiniMax-M2.1")
        client.generate("You are Jarvis.", "Hello")

        call_args = mock_post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["model"] == "MiniMax-M2.1"
        assert payload["messages"][0] == {"role": "system", "content": "You are Jarvis."}
        assert payload["messages"][1] == {"role": "user", "content": "Hello"}

    @patch("core.llm_client.requests.post")
    def test_sends_auth_header(self, mock_post):
        mock_post.return_value = self._mock_response()
        client = LLMClient(api_key="my-secret-key")
        client.generate("sys", "msg")

        call_args = mock_post.call_args
        headers = call_args.kwargs.get("headers") or call_args[1].get("headers")
        assert headers["Authorization"] == "Bearer my-secret-key"
        assert headers["Content-Type"] == "application/json"

    @patch("core.llm_client.requests.post")
    def test_correct_url(self, mock_post):
        mock_post.return_value = self._mock_response()
        client = LLMClient(api_key="k", base_url="https://api.minimax.io/v1")
        client.generate("sys", "msg")

        url = mock_post.call_args[0][0]
        assert url == "https://api.minimax.io/v1/chat/completions"

    @patch("core.llm_client.requests.post")
    def test_http_error_raises(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = Exception("Internal Server Error")
        mock_post.return_value = mock_resp

        client = LLMClient(api_key="k")
        with pytest.raises(Exception, match="Internal Server Error"):
            client.generate("sys", "msg")

    @patch.dict("os.environ", {"MINIMAX_API_KEY": "env-key"})
    @patch("core.llm_client.requests.post")
    def test_api_key_from_env(self, mock_post):
        mock_post.return_value = self._mock_response()
        client = LLMClient()  # no explicit key
        client.generate("sys", "msg")

        headers = mock_post.call_args.kwargs.get("headers") or mock_post.call_args[1].get("headers")
        assert headers["Authorization"] == "Bearer env-key"

    @patch("core.llm_client.requests.post")
    def test_custom_base_url(self, mock_post):
        mock_post.return_value = self._mock_response()
        client = LLMClient(api_key="k", base_url="http://localhost:8080/v1")
        client.generate("sys", "msg")

        url = mock_post.call_args[0][0]
        assert url == "http://localhost:8080/v1/chat/completions"

    @patch("core.llm_client.requests.post")
    def test_strips_think_tags(self, mock_post):
        mock_post.return_value = self._mock_response(
            "<think>\nLet me reason about this...\nThe user wants X.\n</think>\nHere is my answer."
        )
        client = LLMClient(api_key="k")
        result = client.generate("sys", "msg")
        assert result == "Here is my answer."
        assert "<think>" not in result

    @patch("core.llm_client.requests.post")
    def test_strips_multiple_think_tags(self, mock_post):
        mock_post.return_value = self._mock_response(
            "<think>first</think> Hello <think>second</think> world"
        )
        client = LLMClient(api_key="k")
        result = client.generate("sys", "msg")
        assert result == "Hello  world"
        assert "<think>" not in result

    @patch("core.llm_client.requests.post")
    def test_no_think_tags_unchanged(self, mock_post):
        mock_post.return_value = self._mock_response("Just a normal response.")
        client = LLMClient(api_key="k")
        result = client.generate("sys", "msg")
        assert result == "Just a normal response."

    @patch("core.llm_client.requests.post")
    def test_conforms_to_llm_backend_protocol(self, mock_post):
        """LLMClient must work wherever LLMBackend is expected."""
        mock_post.return_value = self._mock_response("response")
        from core.dual_process.generator import ResponseGenerator
        from core.types import PipelineContext

        client = LLMClient(api_key="k")
        gen = ResponseGenerator(backend=client)
        result = gen.generate(PipelineContext(), "Hello")
        assert result.response == "response"
