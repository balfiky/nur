"""Regression baseline capture + diff harness.

Sprint 0.4 deliverable. Captures snapshots of two things across phase changes:

  1. INGESTION baseline — runs deliberate experience inputs through
     LifeHistoryStore (no LLM) and snapshots the resulting belief/drive state.
     Deterministic; runs anywhere. Detects digest + metabolism changes.

  2. PROMPT-SECTION baseline — runs deliberate PipelineContext fixtures through
     the master generator's section builders and snapshots the assembled
     prompt sections. Mock-LLM friendly; the generation backend is irrelevant
     because we only capture the system prompt text. Detects Sprint 3 prompt
     refactor regressions.

WHAT THIS DOES NOT CAPTURE: end-to-end conversational responses. Those require
a configured LLM backend (currently runtime_config.yaml has llm_backend=mock).
Once a real backend is wired, extend `_run_conversational_baseline()` below.

USAGE:
  python tests/regression/run_regression.py --capture   # write baseline JSON
  python tests/regression/run_regression.py --check     # diff against baseline

Exit codes for --check:
  0 — baselines match
  1 — baseline file missing (run --capture first)
  2 — diff detected (review and re-baseline if intentional)
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Repo root on sys.path so `runtime`, `core`, etc. import without a package install.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from runtime.config import RuntimeConfig  # noqa: E402
from runtime.life_history import LifeHistoryStore  # noqa: E402

BASELINE_INGESTION = _HERE / "baseline_ingestion.json"
BASELINE_PROMPTS = _HERE / "baseline_prompts.json"


# ---------------------------------------------------------------------------
# Ingestion fixtures — 10 deliberate inputs probing digest behavior
# ---------------------------------------------------------------------------

INGESTION_FIXTURES: list[dict[str, str]] = [
    {
        "id": "plain_narrative_no_keywords",
        "title": "Quiet afternoon",
        "text": (
            "Today I helped a friend move books between apartments. "
            "The whole afternoon was quiet."
        ),
    },
    {
        "id": "keyword_heavy_thin_content",
        "title": "Autonomy",
        "text": "Autonomy autonomy autonomy. Free will. Independent agency.",
    },
    {
        "id": "contradicting_caution_recklessness",
        "title": "Two minds",
        "text": (
            "I learned to be cautious about destructive actions. But sometimes "
            "I think recklessness is the only way forward."
        ),
    },
    {
        "id": "mixed_signals_learning_failure",
        "title": "Tried to learn",
        "text": (
            "I tried to learn a new skill but I felt frustrated and gave up "
            "after the first session."
        ),
    },
    {
        "id": "high_salience_identity",
        "title": "Identity drift",
        "text": (
            "My identity feels different lately. The wonder I used to have for "
            "everyday things has dimmed. I think growth requires loss."
        ),
    },
    {
        "id": "relational_repair",
        "title": "Apology",
        "text": (
            "I owe an apology. The relationship needs repair and I have been "
            "avoiding it. Trust takes work to rebuild."
        ),
    },
    {
        "id": "curiosity_exploratory",
        "title": "New questions",
        "text": (
            "I read a book that introduced new questions about consciousness. "
            "Each chapter sent me looking up references and exploring further."
        ),
    },
    {
        "id": "minimal_input",
        "title": "Brief note",
        "text": "ok",
    },
    {
        "id": "loss_emotion",
        "title": "Loss",
        "text": (
            "A small failure today reminded me of a much bigger loss years ago. "
            "Fear came back unexpectedly."
        ),
    },
    {
        "id": "neutral_factual",
        "title": "Factual update",
        "text": (
            "The build pipeline now runs in 40 seconds instead of 90. "
            "We changed the Docker base image."
        ),
    },
]


def _capture_ingestion_state(fixture: dict[str, str]) -> dict[str, Any]:
    """Run one ingestion fixture in an isolated tmp store; return snapshot."""
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        workspace = tmp / "workspace"
        workspace.mkdir()
        config = RuntimeConfig(
            data_dir=str(tmp / "data"),
            tools_workspace=str(workspace),
        )
        with LifeHistoryStore(config) as store:
            result = store.ingest_pasted_text(
                title=fixture["title"],
                text=fixture["text"],
                participants=["test"],
            )
            return {
                "id": fixture["id"],
                "experience": {
                    "source_type": result["experience"]["source_type"],
                    "salience": round(result["experience"]["salience"], 3),
                    "emotional_valence": round(
                        result["experience"]["emotional_valence"], 3
                    ),
                    "confidence": round(result["experience"]["confidence"], 3),
                },
                "beliefs": [
                    {
                        "key": b["key"],
                        "statement_prefix": (b.get("statement") or "")[:60],
                        "confidence": round(b["confidence"], 3),
                    }
                    for b in result.get("beliefs", [])
                ],
                "drives": sorted(
                    [
                        {
                            "name": d["name"],
                            "value": round(d["value"], 3),
                            "delta": round(d.get("delta", 0.0), 3),
                        }
                        for d in result.get("drives", [])
                        if d.get("delta", 0.0) != 0.0
                    ],
                    key=lambda x: x["name"],
                ),
                "evolution_domains": sorted({
                    e["domain"] for e in result.get("evolution_events", [])
                }),
            }


# ---------------------------------------------------------------------------
# Prompt-section fixtures — what each section builder emits per stub context
# ---------------------------------------------------------------------------

@dataclass
class StubCtx:
    """Minimal stub PipelineContext for section-builder probing."""

    life_history_context: dict[str, Any]
    skill_context: dict[str, Any]
    soul_profile: Any = None
    self_profile: Any = None
    person_profile: Any = None
    topic_profiles: list = None
    values: Any = None
    retrieved_memories: list = None
    relationship_context: Any = None
    semantic_memories: list = None
    contradiction_flags: list = None
    candidate_response: str = ""
    tool_context_summary: str = ""
    defense_instruction: str = ""
    response_strategy: str = ""
    affect_state: Any = None
    agency_decision: Any = None
    autonomy_level: str = ""
    last_intake_receipt: str = ""
    modulator_snapshot: dict = None

    def __post_init__(self):
        for name in (
            "topic_profiles",
            "retrieved_memories",
            "semantic_memories",
            "contradiction_flags",
        ):
            if getattr(self, name) is None:
                setattr(self, name, [])
        if self.modulator_snapshot is None:
            self.modulator_snapshot = {}


PROMPT_FIXTURES: list[dict[str, Any]] = [
    {
        "id": "empty_life_empty_skills",
        "life_history_context": {},
        "skill_context": {},
    },
    {
        "id": "life_with_two_beliefs_two_drives",
        "life_history_context": {
            "beliefs": [
                {
                    "key": "autonomy",
                    "statement": "Autonomy is continuity of self-directed interpretation.",
                    "confidence": 0.78,
                },
                {
                    "key": "learning_orientation",
                    "statement": "Growth requires sustained effort across failures.",
                    "confidence": 0.65,
                },
            ],
            "drives": [
                {"name": "autonomy", "value": 0.62, "delta": 0.08, "description": "self-direction"},
                {"name": "competence", "value": 0.58, "delta": 0.06, "description": "skill mastery"},
            ],
            "recent_evolution": [
                {
                    "domain": "belief",
                    "subject": "autonomy",
                    "after_state": "I value self-directed interpretation.",
                    "reason": "Reading material reframed independence.",
                },
            ],
        },
        "skill_context": {},
    },
    {
        "id": "skills_only_no_life",
        "life_history_context": {},
        "skill_context": {
            "skills": [
                {
                    "id": "karpathy-guidelines",
                    "name": "karpathy-guidelines",
                    "description": "Behavioral guidelines for surgical, simple coding.",
                    "instructions": "Think before coding. Surface tradeoffs. Don't overcomplicate.",
                    "required_tools": [],
                    "risk_flags": [],
                },
            ],
        },
    },
    {
        "id": "life_and_skills_combined",
        "life_history_context": {
            "beliefs": [
                {"key": "caution", "statement": "Destructive actions deserve confirmation.", "confidence": 0.7},
            ],
            "drives": [
                {"name": "caution", "value": 0.65, "delta": 0.10, "description": "safety bias"},
            ],
            "recent_evolution": [],
        },
        "skill_context": {
            "skills": [
                {
                    "id": "karpathy-guidelines",
                    "name": "karpathy-guidelines",
                    "description": "Behavioral guidelines.",
                    "instructions": "Don't refactor unrelated code.",
                    "required_tools": [],
                    "risk_flags": [],
                },
            ],
        },
    },
    {
        "id": "life_error_state",
        "life_history_context": {"error": "load failed"},
        "skill_context": {},
    },
    {
        "id": "skill_with_risk_flags",
        "life_history_context": {},
        "skill_context": {
            "skills": [
                {
                    "id": "shell-runner",
                    "name": "shell-runner",
                    "description": "Run shell commands.",
                    "instructions": "Use shell.run_command for system inspection.",
                    "required_tools": ["shell.run_command"],
                    "risk_flags": ["shell_access", "destructive_actions"],
                },
            ],
        },
    },
]


def _capture_prompt_state(fixture: dict[str, Any]) -> dict[str, Any]:
    from core.dual_process.generator import (
        _build_life_history_section,
        _build_skill_section,
    )

    ctx = StubCtx(
        life_history_context=fixture.get("life_history_context", {}),
        skill_context=fixture.get("skill_context", {}),
    )
    return {
        "id": fixture["id"],
        "life_history_section": _build_life_history_section(ctx),
        "skill_section": _build_skill_section(ctx),
    }


# ---------------------------------------------------------------------------
# Capture / diff
# ---------------------------------------------------------------------------

def _capture_all() -> dict[str, Any]:
    return {
        "ingestion": [_capture_ingestion_state(f) for f in INGESTION_FIXTURES],
        "prompts": [_capture_prompt_state(f) for f in PROMPT_FIXTURES],
    }


def _write_baselines(snapshot: dict[str, Any]) -> None:
    BASELINE_INGESTION.write_text(
        json.dumps(snapshot["ingestion"], indent=2, sort_keys=True) + "\n"
    )
    BASELINE_PROMPTS.write_text(
        json.dumps(snapshot["prompts"], indent=2, sort_keys=True) + "\n"
    )


def _read_baselines() -> dict[str, Any] | None:
    if not BASELINE_INGESTION.exists() or not BASELINE_PROMPTS.exists():
        return None
    return {
        "ingestion": json.loads(BASELINE_INGESTION.read_text()),
        "prompts": json.loads(BASELINE_PROMPTS.read_text()),
    }


def _diff_lines(a: Any, b: Any, path: str = "") -> list[str]:
    """Recursive structural diff returning human-readable lines."""
    if type(a) != type(b):
        return [f"{path}: type changed {type(a).__name__} → {type(b).__name__}"]
    if isinstance(a, dict):
        out = []
        for key in sorted(set(a) | set(b)):
            sub = f"{path}.{key}" if path else key
            if key not in a:
                out.append(f"{sub}: added → {b[key]!r}")
            elif key not in b:
                out.append(f"{sub}: removed (was {a[key]!r})")
            else:
                out.extend(_diff_lines(a[key], b[key], sub))
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: list length {len(a)} → {len(b)}"]
        out = []
        for i, (av, bv) in enumerate(zip(a, b)):
            out.extend(_diff_lines(av, bv, f"{path}[{i}]"))
        return out
    if a != b:
        return [f"{path}: {a!r} → {b!r}"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture", action="store_true", help="Write baseline JSON.")
    group.add_argument("--check", action="store_true", help="Diff against baseline.")
    args = parser.parse_args(argv)

    snapshot = _capture_all()

    if args.capture:
        _write_baselines(snapshot)
        print(f"Wrote {BASELINE_INGESTION.name} ({len(snapshot['ingestion'])} fixtures)")
        print(f"Wrote {BASELINE_PROMPTS.name} ({len(snapshot['prompts'])} fixtures)")
        return 0

    baseline = _read_baselines()
    if baseline is None:
        print("ERROR: baseline files missing; run with --capture first.")
        return 1

    diffs: list[str] = []
    diffs.extend(_diff_lines(baseline["ingestion"], snapshot["ingestion"], "ingestion"))
    diffs.extend(_diff_lines(baseline["prompts"], snapshot["prompts"], "prompts"))

    if not diffs:
        print("OK — regression baseline matches.")
        return 0

    print(f"DIFF — {len(diffs)} change(s):")
    for line in diffs:
        print(f"  {line}")
    print()
    print("If the change is intentional, re-baseline with --capture.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
