"""General generated-artifact detection and drafting.

This module handles requests like "write me a Python script and save it in
tools/" without hardcoding the artifact topic. The LLM supplies the file
content; the runtime writes it through the normal filesystem tool.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


_ARTIFACT_INTENT_RE = re.compile(
    r"\b(?:write|create|make|build|generate|draft)\b"
    r".{0,120}\b(?:code|script|program|app|game|tool|file|document|markdown|html|page)\b"
    r".{0,160}\b(?:save|write|put|place|store)\b",
    re.IGNORECASE | re.DOTALL,
)
_SAVE_DIR_RE = re.compile(
    r"\b(?:in|inside|under|into)\s+(?:the\s+)?"
    r"([A-Za-z0-9_./~ -]{1,120}?)\s+"
    r"(?:dir|directory|folder)\b",
    re.IGNORECASE,
)
_SAVE_FILE_RE = re.compile(
    r"\b(?:as|to|into|at)\s+`?([A-Za-z0-9_./~-]+\.[A-Za-z0-9_+-]{1,12})`?",
    re.IGNORECASE,
)
_LANGUAGE_EXTENSIONS = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "typescript": ".ts",
    "html": ".html",
    "css": ".css",
    "markdown": ".md",
    "bash": ".sh",
    "shell": ".sh",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yml",
    "text": ".txt",
}
_GENERIC_FILENAME_WORDS = {
    "a",
    "an",
    "app",
    "application",
    "code",
    "file",
    "game",
    "program",
    "script",
    "simple",
    "small",
    "the",
    "tool",
}


@dataclass(frozen=True)
class ArtifactWriteRequest:
    """A user request to generate content and save it to one file."""

    path: str
    language: str
    subject: str
    original_request: str


@dataclass(frozen=True)
class ArtifactDraft:
    """Generated file payload."""

    path: str
    content: str
    summary: str = ""


def detect_artifact_write_request(message: str) -> ArtifactWriteRequest | None:
    """Return a file-generation request, or None for ordinary tool requests."""
    text = (message or "").strip()
    if not text or not _ARTIFACT_INTENT_RE.search(text):
        return None

    explicit_path = _extract_explicit_file_path(text)
    language = _infer_language(text, explicit_path)
    subject = _infer_subject(text)
    path = explicit_path or _inferred_path(text, subject, language)
    if not path:
        return None
    return ArtifactWriteRequest(
        path=path,
        language=language,
        subject=subject,
        original_request=text,
    )


def generate_artifact_draft(
    backend: Any,
    request: ArtifactWriteRequest,
) -> ArtifactDraft:
    """Ask the LLM for one complete file payload as strict JSON."""
    system_prompt = (
        "Generate one complete file for the user's request. "
        "Return JSON only, with keys: path, content, summary. "
        "The content value must contain the complete file contents. "
        "Do not use Markdown fences. Do not omit code. "
        f"Use this target path unless the user explicitly specified another: {request.path!r}. "
        f"Language: {request.language or 'infer from request'}. "
        f"Subject: {request.subject or 'requested artifact'}."
    )
    raw = backend.generate(system_prompt, request.original_request)
    payload = _parse_artifact_json(raw)
    path = str(payload.get("path") or request.path).strip() or request.path
    content = str(payload.get("content") or "").strip()
    if not content:
        content = _extract_fenced_or_plain_content(raw)
    if not content:
        raise ValueError("Generated artifact response did not include file content.")
    return ArtifactDraft(
        path=path,
        content=content.rstrip() + "\n",
        summary=str(payload.get("summary") or "").strip(),
    )


def _extract_explicit_file_path(text: str) -> str:
    match = _SAVE_FILE_RE.search(text)
    if not match:
        return ""
    return _clean_path(match.group(1))


def _infer_language(text: str, explicit_path: str = "") -> str:
    lower = text.lower()
    for language in _LANGUAGE_EXTENSIONS:
        if re.search(rf"\b{re.escape(language)}\b", lower):
            return language
    suffix = "." + explicit_path.rsplit(".", 1)[-1].lower() if "." in explicit_path else ""
    for language, ext in _LANGUAGE_EXTENSIONS.items():
        if suffix == ext:
            return language
    return "text"


def _infer_subject(text: str) -> str:
    match = re.search(
        r"\b(?:for|of)\s+(?:a\s+|an\s+|the\s+)?(.{3,120}?)(?:\s+and\s+|\s+to\s+|\s+in\s+|\s+inside\s+|\s+under\s+|\s+save\b|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    subject = match.group(1) if match else text
    subject = re.sub(r"\b(?:python|javascript|typescript|html|css|markdown|bash|shell)\b", " ", subject, flags=re.I)
    subject = re.sub(r"\b(?:code|script|program|app|application|file|document)\b", " ", subject, flags=re.I)
    return " ".join(subject.split()).strip(" .,:;")[:120] or "artifact"


def _inferred_path(text: str, subject: str, language: str) -> str:
    directory = ""
    dir_match = _SAVE_DIR_RE.search(text)
    if dir_match:
        directory = _clean_directory(dir_match.group(1))
    ext = _LANGUAGE_EXTENSIONS.get(language, ".txt")
    filename = _slug_filename(subject, ext)
    return f"{directory.rstrip('/')}/{filename}" if directory else filename


def _clean_directory(value: str) -> str:
    cleaned = _clean_path(value)
    cleaned = re.sub(r"\b(?:you\s+create|that\s+you\s+create|you\s+make|that\s+you\s+make)\b", "", cleaned, flags=re.I)
    cleaned = cleaned.strip(" ./") or "."
    return cleaned


def _clean_path(value: str) -> str:
    cleaned = " ".join(str(value or "").strip(" `\"'").split())
    return cleaned.replace(" ", "-").strip("-")


def _slug_filename(subject: str, extension: str) -> str:
    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z0-9]+", subject or "")
        if word.lower() not in _GENERIC_FILENAME_WORDS
    ]
    stem = "-".join(words[:5]).strip("-") or "artifact"
    return f"{stem}{extension}"


def _parse_artifact_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_fenced_or_plain_content(raw: str) -> str:
    text = (raw or "").strip()
    match = re.search(r"```(?:[A-Za-z0-9_+-]+)?\s*(.*?)```", text, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return text
