"""Provenance resolution for eval runs.

Collects everything needed to interpret or reproduce a set of eval
numbers: code state (git sha, dirty-worktree flag), host, and SHA256
fingerprints of the config/prompt files that can change behavior
without touching code.

The resolver is deliberately strict about the *kinds* of things it
records but lenient about missing data — e.g. if the repo is not a
git checkout, `git_sha` is "" rather than raising. The point is that
every field in `RunProvenance` either contains real data or is
explicitly empty; nothing is silently defaulted to a fictional value.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import socket
import subprocess
from pathlib import Path

from evals.types import RunProvenance


# ---------------------------------------------------------------------------
# Files whose contents can change behavior without a code edit.
# Keep this list narrow; expanding it bloats provenance reports.
# ---------------------------------------------------------------------------
FINGERPRINT_FILES: list[str] = [
    "config/modulators.yaml",
    "config/attachment.yaml",
    "config/profiles_schema.yaml",
    "config/values_seed.yaml",
    "config/soul.yaml",
    "config/semantic_memory.yaml",
    "config/prompts/generator.md",
    "config/prompts/self_check.md",
    "config/prompts/digestion.md",
    "config/prompts/fast_path.md",
    "config/prompts/slow_path.md",
    "config/prompts/arbiter.md",
    "config/prompts/classify_event.md",
    "config/prompts/contagion.md",
    "config/prompts/detect_topics.md",
    "config/prompts/fast_path_revision.md",
]


def _git(*args: str) -> str:
    """Run a git command at the repo root and return stripped stdout.

    Returns an empty string on failure (not a git checkout, git missing,
    etc.) rather than raising — provenance should degrade gracefully.
    """
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=_repo_root(),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def _repo_root(start: Path | None = None) -> str:
    """Best-effort repo root for git checkouts and source archives."""
    here = (start or Path(__file__)).resolve()
    candidates = (here, *here.parents)
    for parent in candidates:
        if (parent / ".git").exists():
            return str(parent)
        pyproject = parent / "pyproject.toml"
        if pyproject.is_file():
            try:
                if 'name = "project-nur"' in pyproject.read_text(encoding="utf-8"):
                    return str(parent)
            except OSError:
                pass
        if (parent / "config" / "soul.yaml").is_file() and (parent / "pipeline.py").is_file():
            return str(parent)
    return str(here.parent if here.is_file() else here)


def collect_git_info() -> tuple[str, str, bool]:
    """Return (sha, branch, dirty_worktree)."""
    sha = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    status = _git("status", "--porcelain")
    dirty = bool(status)
    return sha, branch, dirty


def fingerprint_files(paths: list[str]) -> dict[str, str]:
    """SHA256 hex of each file, relative to repo root.

    Missing files are omitted from the result (not recorded as empty
    strings) so that upgrades that remove a prompt don't look like a
    fingerprint change.
    """
    root = Path(_repo_root())
    out: dict[str, str] = {}
    for rel in paths:
        path = root / rel
        if not path.is_file():
            continue
        h = hashlib.sha256()
        h.update(path.read_bytes())
        out[rel] = h.hexdigest()
    return out


def build_provenance(
    *,
    backend_type: str,
    requested_model: str,
    resolved_model: str,
    base_url: str,
    scenario_set: str,
    scenario_ids: list[str],
) -> RunProvenance:
    """Assemble a provenance record at the start of a run.

    Execution counters (llm_calls, failures, etc.) are filled in by the
    runner after scenarios complete. This function only sets the fields
    that can be known *before* the run starts.

    ``RunProvenance`` exposes fields like ``temperature``, ``max_tokens``,
    ``prompt_tokens``, ``completion_tokens``, ``retries``, and
    ``estimated_cost_usd`` that remain ``None`` until the clients are
    instrumented to produce real values. ``None`` serializes to JSON
    ``null`` — an honest "not measured" rather than a fake zero.
    """
    sha, branch, dirty = collect_git_info()
    started = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    return RunProvenance(
        started_at=started,
        host=socket.gethostname(),
        git_sha=sha,
        git_branch=branch,
        dirty_worktree=dirty,
        backend_type=backend_type,
        requested_model=requested_model,
        resolved_model=resolved_model,
        base_url=base_url,
        config_fingerprints=fingerprint_files(FINGERPRINT_FILES),
        scenario_set=scenario_set,
        scenario_count=len(scenario_ids),
        scenario_ids=list(scenario_ids),
    )
