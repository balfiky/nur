"""Tests for tools/builtin/web_provider.py — the requests-backed provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tools.builtin.web_provider import (
    RequestsWebProvider,
    _MAX_FETCH_BYTES,
    _html_to_text,
    _parse_ddg_results,
)


class TestRequestsWebProviderFetch:
    def _streaming_response(self, chunks: list[bytes], encoding: str = "utf-8") -> MagicMock:
        """Build a mock Response that yields the given chunks via iter_content."""
        resp = MagicMock()
        resp.iter_content.return_value = iter(chunks)
        resp.encoding = encoding
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_fetch_honors_max_fetch_bytes(self):
        """Regression: prior implementation used ``resp.content[:CAP]`` which
        silently buffered the entire body and ignored the cap. ``fetch()`` must
        stop streaming once the cap is reached."""
        # Three chunks of 1MB each — a 3MB body while the cap is 2MB.
        big_chunks = [b"A" * 1_000_000, b"B" * 1_000_000, b"C" * 1_000_000]
        provider = RequestsWebProvider()
        fake_resp = self._streaming_response(big_chunks)

        with patch.object(provider._session, "get", return_value=fake_resp) as mock_get:
            text = provider.fetch("https://example.invalid/big")

        mock_get.assert_called_once()
        assert len(text) <= _MAX_FETCH_BYTES
        # The body must be truncated, not fully buffered.
        assert len(text) == _MAX_FETCH_BYTES
        assert text.startswith("A")

    def test_fetch_short_body_returned_whole(self):
        provider = RequestsWebProvider()
        fake_resp = self._streaming_response([b"hello world"])
        with patch.object(provider._session, "get", return_value=fake_resp):
            text = provider.fetch("https://example.invalid/small")
        assert text == "hello world"

    def test_fetch_uses_context_manager(self):
        """The streamed response must be wrapped in a context manager so the
        connection is returned to the pool even on error paths."""
        provider = RequestsWebProvider()
        fake_resp = self._streaming_response([b"ok"])
        with patch.object(provider._session, "get", return_value=fake_resp):
            provider.fetch("https://example.invalid/")
        fake_resp.__enter__.assert_called_once()
        fake_resp.__exit__.assert_called_once()

    def test_close_releases_session(self):
        provider = RequestsWebProvider()
        with patch.object(provider._session, "close") as mock_close:
            provider.close()
            mock_close.assert_called_once()


class TestRequestsWebProviderSearch:
    def test_search_parses_ddg_html(self):
        provider = RequestsWebProvider()
        html = (
            '<a class="result__a" href="https://example.com/a">Alpha</a>'
            '<a class="result__a" href="https://example.com/b">Beta</a>'
        )
        fake_resp = MagicMock()
        fake_resp.text = html
        fake_resp.raise_for_status = MagicMock()
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(provider._session, "post", return_value=fake_resp):
            results = provider.search("anything", limit=5)

        assert results == [
            {"title": "Alpha", "url": "https://example.com/a"},
            {"title": "Beta", "url": "https://example.com/b"},
        ]


class TestHTMLParsing:
    def test_strip_scripts_and_styles(self):
        html = "<html><script>evil()</script><p>Hello</p><style>x{}</style></html>"
        text = _html_to_text(html)
        assert "Hello" in text
        assert "evil" not in text
        assert "x{}" not in text

    def test_decodes_entities(self):
        assert _html_to_text("<p>A &amp; B</p>") == "A & B"

    def test_ddg_fallback_pattern(self):
        """If result__a is missing, the nofollow fallback should catch links."""
        html = (
            '<a rel="nofollow" href="https://example.com/x">Ex</a>'
            '<a rel="nofollow" href="/internal">Skip</a>'
        )
        results = _parse_ddg_results(html, limit=5)
        assert {"title": "Ex", "url": "https://example.com/x"} in results
        # Relative URL should be filtered because fallback requires http prefix.
        assert all(r["url"].startswith("http") for r in results)
