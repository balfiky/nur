"""Explicit conversation-triggered learning intake.

This module turns direct requests like "learn from this URL" into durable
Life History experiences. It deliberately does not run for ordinary web
questions or casual discussion; identity-level mutation needs explicit intent.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

from nur_tools.builtin.web_provider import RequestsWebProvider
from runtime.config import RuntimeConfig
from runtime.life_history import LifeHistoryStore
from runtime.llm.backend import create_llm_backend


MAX_LEARNING_URLS = 3
MAX_LEARNING_TEXT_CHARS = 200_000
MIN_LEARNING_TEXT_CHARS = 120

_URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+", re.I)
_LEARNING_INTENT_PATTERNS = (
    re.compile(r"\blearn\b.{0,60}\bfrom\b", re.I),
    re.compile(r"\blearn\s+(?:this|that)\b", re.I),
    re.compile(r"\blearn\s*:", re.I),
    re.compile(r"\bingest\b", re.I),
    re.compile(
        r"\bstudy\b.{0,60}\b(?:this|that|from|repo|repository|project|book|article|url)\b",
        re.I,
    ),
    re.compile(r"\b(?:absorb|absort|internalize|digest)\b", re.I),
    re.compile(r"\bmake\s+this\s+part\s+of\s+you\b", re.I),
    re.compile(r"\blet\s+this\s+shape\s+you\b", re.I),
    re.compile(r"\btake\s+this\s+as\s+formative\b", re.I),
    re.compile(r"\bfill\s+(?:it|life\s+history|life\s+learning|memory)\s+with\s+this\b", re.I),
    re.compile(
        r"\b(?:add|save|record|store|put)\b.{0,60}\b"
        r"(?:life\s+history|life\s+learning|life\s+ledger|formative\s+memory)\b",
        re.I,
    ),
    re.compile(r"\bseed\s+(?:life\s+history|life\s+learning|memory)\b", re.I),
)


class LearningIntakeError(RuntimeError):
    """Raised when an explicit learning request cannot be completed."""


@dataclass(frozen=True)
class LearningRequest:
    urls: tuple[str, ...]
    inline_text: str


@dataclass(frozen=True)
class PendingLearningIntake:
    """A short-lived cross-turn request for formative material."""

    created_turn_index: int
    expires_after_turns: int
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if self.created_at <= 0:
            object.__setattr__(self, "created_at", time.time())

    def expired(self, current_turn_index: int) -> bool:
        return current_turn_index - self.created_turn_index > self.expires_after_turns

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_turn_index": self.created_turn_index,
            "expires_after_turns": self.expires_after_turns,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PendingLearningIntake | None:
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                created_turn_index=int(data.get("created_turn_index", 0)),
                expires_after_turns=int(data.get("expires_after_turns", 0)),
                created_at=float(data.get("created_at", 0.0)),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class LearningIntakeResult:
    title: str
    source_ref: str
    experience_id: int
    source_type: str
    evolution_counts: dict[str, int]
    rejection_counts: dict[str, int] | None = None

    @property
    def total_evolution_events(self) -> int:
        return sum(self.evolution_counts.values())

    def confirmation_text(self) -> str:
        parts = [
            f"{count} {domain.replace('_', ' ')}"
            for domain, count in sorted(self.evolution_counts.items())
            if count
        ]
        if not parts:
            change_text = "no durable belief or drive change"
        else:
            change_text = ", ".join(parts)
        rejection_parts = [
            f"{count} {reason.replace('_', ' ')}"
            for reason, count in sorted((self.rejection_counts or {}).items())
            if count
        ]
        rejection_text = ""
        if rejection_parts:
            rejection_text = f" {sum((self.rejection_counts or {}).values())} item(s) did not move me ({', '.join(rejection_parts)})."
        return (
            f"Learned into Life History: {self.title}. "
            f"Recorded {change_text}.{rejection_text} Inspect it in Admin > Life."
        )


def detect_learning_request(message: str) -> LearningRequest | None:
    """Return an explicit learning request, or ``None`` for ordinary chat."""
    text = (message or "").strip()
    if not text or not _has_learning_intent(text):
        return None

    urls = tuple(_extract_urls(text)[:MAX_LEARNING_URLS])
    inline_text = _extract_inline_text(text) if not urls else ""
    if not urls and len(inline_text) < MIN_LEARNING_TEXT_CHARS:
        raise LearningIntakeError(
            "I need a URL or a longer pasted text block before I can learn from it."
        )
    return LearningRequest(urls=urls, inline_text=inline_text)


def _has_learning_intent(text: str) -> bool:
    return any(pattern.search(text) for pattern in _LEARNING_INTENT_PATTERNS)


def ingest_learning_from_message(
    config: RuntimeConfig,
    message: str,
    *,
    actor: str,
    pending_inline_text: bool = False,
    web_provider: RequestsWebProvider | None = None,
    llm_client_factory: Callable[[RuntimeConfig], Any] = create_llm_backend,
) -> LearningIntakeResult | None:
    """Detect, fetch, digest, and persist explicit learning material."""
    request = (
        LearningRequest(urls=(), inline_text=(message or "").strip())
        if pending_inline_text
        else detect_learning_request(message)
    )
    if request is None:
        return None

    provider = web_provider or RequestsWebProvider()
    owns_provider = web_provider is None
    llm_client = None
    try:
        title, source_ref, source_type, material, metadata = _collect_material(provider, request)
        if len(material.strip()) < MIN_LEARNING_TEXT_CHARS:
            raise LearningIntakeError(
                "The source did not contain enough readable text to learn from."
            )

        try:
            llm_client = llm_client_factory(config)
        except Exception:
            llm_client = None

        with LifeHistoryStore(config) as store:
            result = store.ingest_external_text(
                title=title,
                text=material[:MAX_LEARNING_TEXT_CHARS],
                source_type=source_type,
                source_ref=source_ref,
                participants=[actor, "Nūr"],
                llm_client=llm_client,
                metadata=metadata,
            )
        return _to_learning_result(result)
    finally:
        close = getattr(llm_client, "close", None)
        if callable(close):
            close()
        if owns_provider:
            provider.close()


def _collect_material(
    provider: RequestsWebProvider,
    request: LearningRequest,
) -> tuple[str, str, str, str, dict[str, Any]]:
    if request.urls:
        sources = [_read_url(provider, url) for url in request.urls]
        if len(sources) == 1:
            source = sources[0]
            return (
                source["title"],
                source["url"],
                "conversation_learning_url",
                source["text"],
                {
                    "input_mode": "conversation_learning",
                    "url": source["url"],
                    "reader": source.get("reader", "web_extract"),
                },
            )
        title = f"Learning intake from {len(sources)} sources"
        material = "\n\n---\n\n".join(
            f"Source: {source['title']}\nURL: {source['url']}\n\n{source['text']}"
            for source in sources
        )
        return (
            title,
            ", ".join(source["url"] for source in sources),
            "conversation_learning_url",
            material,
            {
                "input_mode": "conversation_learning",
                "urls": [source["url"] for source in sources],
                "source_count": len(sources),
            },
        )

    return (
        "Conversation learning note",
        "conversation",
        "conversation_learning_text",
        request.inline_text,
        {"input_mode": "conversation_learning", "source": "inline_text"},
    )


def _read_url(provider: RequestsWebProvider, url: str) -> dict[str, str]:
    github = _read_github_repo_readme(provider, url)
    if github is not None:
        return github

    try:
        text = provider.extract_text(url)
    except Exception as exc:
        raise LearningIntakeError(f"Could not read {url}: {exc}") from exc
    return {
        "title": _title_from_url(url),
        "url": url,
        "text": text,
        "reader": "web_extract",
    }


def _read_github_repo_readme(provider: RequestsWebProvider, url: str) -> dict[str, str] | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if len(parts) > 2 and parts[2] not in {"tree", ""}:
        return None

    api_root = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        repo_data = json.loads(provider.fetch(api_root))
        readme_data = json.loads(provider.fetch(f"{api_root}/readme"))
        download_url = str(readme_data.get("download_url") or "")
        if not download_url:
            return None
        readme = provider.fetch(download_url)
    except Exception:
        return None

    full_name = str(repo_data.get("full_name") or f"{owner}/{repo}")
    description = str(repo_data.get("description") or "").strip()
    topics = repo_data.get("topics") if isinstance(repo_data.get("topics"), list) else []
    title = full_name if len(full_name) <= 180 else full_name[:180]
    header = [
        f"GitHub repository: {full_name}",
        f"URL: {url}",
    ]
    if description:
        header.append(f"Description: {description}")
    if topics:
        header.append(f"Topics: {', '.join(str(topic) for topic in topics[:20])}")
    material = "\n".join(header) + "\n\nREADME:\n" + readme
    return {
        "title": title,
        "url": url,
        "text": material,
        "reader": "github_readme",
    }


def _to_learning_result(result: dict[str, Any]) -> LearningIntakeResult:
    experience = result["experience"]
    counts: dict[str, int] = {}
    for event in result.get("evolution_events", []):
        domain = str(event.get("domain") or "unknown")
        counts[domain] = counts.get(domain, 0) + 1
    rejection_counts: dict[str, int] = {}
    rejections = result.get("rejection_trace")
    if not rejections:
        rejections = result.get("policy", {}).get("rejections", [])
    for rejection in rejections or []:
        reason = str(rejection.get("reason") or "unknown")
        count = int(rejection.get("count") or 1)
        rejection_counts[reason] = rejection_counts.get(reason, 0) + max(1, count)
    return LearningIntakeResult(
        title=str(experience["source_title"]),
        source_ref=str(experience["source_ref"]),
        experience_id=int(experience["id"]),
        source_type=str(experience["source_type"]),
        evolution_counts=counts,
        rejection_counts=rejection_counts,
    )


def _extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?)]}")
        if url not in urls:
            urls.append(url)
    return urls


def _extract_inline_text(text: str) -> str:
    match = re.search(
        r"^\s*fill\s+(?:it|life\s+history|life\s+learning|memory)\s+with\s+this\s*:?\s*(.+)",
        text,
        re.I | re.S,
    )
    if match:
        return match.group(1).strip()
    match = re.search(
        r"^\s*(?:add|save|record|store|put)\b.{0,60}\b"
        r"(?:life\s+history|life\s+learning|life\s+ledger|formative\s+memory)\s*:?\s*(.+)",
        text,
        re.I | re.S,
    )
    if match:
        return match.group(1).strip()
    match = re.search(
        r"(?:learn|study|absorb|absort|internalize|digest|ingest)[^:]*:\s*(.+)",
        text,
        re.I | re.S,
    )
    if match:
        return match.group(1).strip()
    return text.strip()


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace("www.", "")
    path = parsed.path.strip("/")
    if path:
        last = path.split("/")[-1].replace("-", " ").replace("_", " ")
        title = f"{host}: {last}" if last else host
    else:
        title = host or "Web source"
    return title[:180] or "Web source"
