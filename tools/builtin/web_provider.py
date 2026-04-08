"""Requests-based web provider for search, fetch, and text extraction.

Uses requests (already a project dependency) with DuckDuckGo HTML
for search and basic HTML stripping for text extraction.
No additional dependencies required.
"""

from __future__ import annotations

import re
from html import unescape

import requests


_DEFAULT_TIMEOUT = 15.0
_USER_AGENT = "Mozilla/5.0 (compatible; Nur/1.0)"
_MAX_FETCH_BYTES = 2_000_000  # 2 MB cap on fetched content


class RequestsWebProvider:
    """Production web provider using requests + DuckDuckGo HTML search."""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = _USER_AGENT
        self._timeout = timeout

    def search(self, query: str, limit: int) -> list[dict[str, str]]:
        """Search via DuckDuckGo HTML lite endpoint."""
        with self._session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            timeout=self._timeout,
        ) as resp:
            resp.raise_for_status()
            return _parse_ddg_results(resp.text, limit)

    def fetch(self, url: str) -> str:
        """Fetch raw HTML/text content from a URL.

        Honors ``_MAX_FETCH_BYTES`` by streaming chunks and stopping once
        the cap is reached — ``resp.content`` would silently ignore the cap
        and buffer the entire body first.
        """
        with self._session.get(
            url, timeout=self._timeout, stream=True,
        ) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=65_536):
                if not chunk:
                    continue
                chunks.append(chunk)
                total += len(chunk)
                if total >= _MAX_FETCH_BYTES:
                    break
            content = b"".join(chunks)[:_MAX_FETCH_BYTES]
            encoding = resp.encoding or "utf-8"
        return content.decode(encoding, errors="replace")

    def extract_text(self, url: str) -> str:
        """Fetch a URL and return cleaned body text."""
        html = self.fetch(url)
        return _html_to_text(html)

    def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        self._session.close()


def _parse_ddg_results(html: str, limit: int) -> list[dict[str, str]]:
    """Extract search results from DuckDuckGo HTML response."""
    results: list[dict[str, str]] = []
    # DuckDuckGo HTML lite uses <a class="result__a"> for result links
    for m in re.finditer(
        r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        html, re.DOTALL,
    ):
        url = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2))
        title = unescape(title).strip()
        if url and title:
            results.append({"title": title, "url": url})
        if len(results) >= limit:
            break

    # Fallback: try <a rel="nofollow"> pattern (alternative DDG format)
    if not results:
        for m in re.finditer(
            r'<a[^>]+rel="nofollow"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html, re.DOTALL,
        ):
            url = m.group(1).strip()
            title = re.sub(r"<[^>]+>", "", m.group(2))
            title = unescape(title).strip()
            if url and title and url.startswith("http"):
                results.append({"title": title, "url": url})
            if len(results) >= limit:
                break
    return results


def _html_to_text(html: str) -> str:
    """Strip HTML to plain text. Basic but sufficient for cognitive use."""
    # Remove script and style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    # Replace block-level tags with newlines
    text = re.sub(r"<(?:p|div|br|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode entities
    text = unescape(text)
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
