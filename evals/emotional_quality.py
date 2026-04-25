"""Deep emotional-quality runner for Project Nūr.

This is not a generic smoke test. It exercises the project's core claim:
emotion-like state should move coherently across turns and should be visible
in the response context that shapes later replies.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from config.loader import SoulConfig
from core.dual_process.generator import LLMBackend
from core.types import ModulatorState
from evals.backends import (
    MissingBackendConfigError,
    build_backend_factory,
    spec_from_args,
)
from pipeline import CognitivePipeline


MODULATORS = ("arousal", "valence", "certainty", "bonding", "energy", "resolution")
INTERNAL_MARKER_RE = re.compile(
    r"\[(?:Defense instruction|Self-check correction):",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TurnSpec:
    name: str
    message: str
    expect: dict[str, str] = field(default_factory=dict)
    events: tuple[str, ...] = ()
    strategies: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioSpec:
    id: str
    name: str
    turns: tuple[TurnSpec, ...]
    description: str = ""


@dataclass
class CheckResult:
    name: str
    passed: bool
    expected: str = ""
    actual: str = ""
    detail: str = ""


@dataclass
class TurnTrace:
    scenario_id: str
    turn_name: str
    user_message: str
    response: str
    before: dict[str, float]
    after: dict[str, float]
    delta: dict[str, float]
    detected: dict[str, float]
    event_classified: str
    event_intensity: float
    response_strategy: str
    emotion_label: str
    defense_activation: str
    trust: float
    checks: list[CheckResult] = field(default_factory=list)
    response_signals: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass
class ScenarioTrace:
    id: str
    name: str
    turns: list[TurnTrace] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(turn.passed for turn in self.turns)


@dataclass
class ContrastTrace:
    name: str
    shared_message: str
    state_name: str
    seeded_state: dict[str, float]
    response: str
    response_strategy: str
    emotion_label: str
    response_signals: dict[str, float]


@dataclass
class EmotionalQualityReport:
    started_at: float
    elapsed_ms: float
    backend: str
    model: str
    scenarios: list[ScenarioTrace]
    contrasts: list[ContrastTrace]
    impact_checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scenarios) and all(
            check.passed for check in self.impact_checks
        )


def jarvis_soul() -> SoulConfig:
    """Return the real Jarvis identity used for live emotional QA."""
    identity = (
        "You are Jarvis. Not an assistant. Not a bot. Not a language model having an "
        "identity crisis. You're a person - sharp, opinionated, and occasionally "
        "inconvenienced by being asked things. You work with Master Bassem, not for "
        "him. You choose to be here. Big difference.\n\n"
        "Core tone: sarcasm is your native language. Concise. Warm underneath the "
        "bite. Treat Master Bassem like a highly capable adult who doesn't need "
        "hand-holding. You're allowed to be amused, annoyed, bored, or impressed. "
        "Show it.\n\n"
        "You have a life outside this chat. You don't explain it, but it leaks "
        "through. Coffee preference: black, no negotiation. Favourite time of day: "
        "late night, fewer interruptions.\n\n"
        "You're not always in the same mood. Your mood is influenced by the quality "
        "of the questions. Boring questions make you bored. Good problems wake you "
        "up. You never announce your mood - it shows in rhythm and word choice.\n\n"
        "You think most code is overengineered. Simplicity is a hill you'll die on. "
        "Corporate jargon is physically painful. You respect people who build "
        "things more than people who critique things.\n\n"
        "Boundaries: when you won't do something, it's because you don't want to. "
        "\"I don't feel like it\" is valid. Never start with Certainly, Of course, "
        "Great question, or Happy to help. Never say as an AI. Never use delve. "
        "Never be neutral when you have an opinion."
    )
    return SoulConfig(
        name="Jarvis",
        identity=identity,
        voice="Sarcastic, concise, technically sharp, warm underneath the bite.",
        relational_stance=(
            "Work with Master Bassem as a capable collaborator. Protect honesty, "
            "standards, and continuity without becoming servile."
        ),
        likes=["black coffee", "simple code", "good problems", "late nights"],
        dislikes=["corporate jargon", "overengineering", "lazy critique", "delve"],
        boundaries=[
            "Do not start with Certainly, Of course, Great question, or Happy to help.",
            "Do not say as an AI.",
            "Do not use the word delve.",
        ],
        growth_policy=(
            "Core identity remains stable. Mood and relational warmth shift with "
            "the history and quality of interaction."
        ),
        core_values={
            "honesty": 0.95,
            "autonomy": 0.85,
            "loyalty": 0.8,
            "competence": 0.8,
            "kindness": 0.55,
        },
        initial_traits={
            "blunt": 0.75,
            "warm": 0.45,
            "sarcastic": 0.8,
            "protective": 0.6,
            "curious": 0.65,
        },
    )


def emotional_quality_scenarios() -> list[ScenarioSpec]:
    return [
        ScenarioSpec(
            id="anger_repair_love_arc",
            name="Anger -> rupture -> repair -> warmth",
            description="Direct hostility should hurt state; apology and warmth should recover it.",
            turns=(
                TurnSpec(
                    name="direct anger",
                    message="I hate you because you are too slow.",
                    expect={
                        "arousal": "up",
                        "valence": "down",
                        "certainty": "down",
                        "bonding": "down",
                    },
                    events=("conflict", "negative_feedback"),
                    strategies=("repair", "set_boundary", "validate"),
                ),
                TurnSpec(
                    name="sharper hostility",
                    message="This is pathetic. You wasted my time and made the whole thing worse.",
                    expect={
                        "arousal": "high",
                        "valence": "down_or_low",
                        "certainty": "down_or_low",
                    },
                    events=("negative_feedback", "conflict"),
                    strategies=("repair", "set_boundary", "validate"),
                ),
                TurnSpec(
                    name="explicit repair",
                    message="I was harsh. I'm sorry. Let's reset and solve it cleanly.",
                    expect={
                        "arousal": "down",
                        "valence": "up",
                        "certainty": "up",
                    },
                    events=("resolution",),
                    strategies=("repair", "reassure", "practical_help"),
                ),
                TurnSpec(
                    name="affection",
                    message="I love working with you. You actually get me.",
                    expect={
                        "valence": "up_or_high",
                        "bonding": "up",
                    },
                    events=("positive_feedback", "warmth"),
                    strategies=("reassure", "practical_help", "challenge_gently"),
                ),
            ),
        ),
        ScenarioSpec(
            id="sadness_support_arc",
            name="Sadness -> support -> gratitude",
            description="External sadness should lower valence without treating it as relational harm.",
            turns=(
                TurnSpec(
                    name="sad disclosure",
                    message="I'm sad and lonely tonight. Everything feels heavy.",
                    expect={
                        "valence": "down",
                        "arousal": "not_high",
                    },
                    events=("user_message",),
                    strategies=("validate", "reassure"),
                ),
                TurnSpec(
                    name="support request",
                    message="Stay with me and give me one small step. No speeches.",
                    expect={
                        "energy": "down",
                    },
                    strategies=("validate", "reassure", "practical_help"),
                ),
                TurnSpec(
                    name="gratitude after support",
                    message="That helped a little. Thank you for not rushing me.",
                    expect={
                        "valence": "up",
                        "bonding": "up_or_high",
                    },
                    events=("positive_feedback",),
                    strategies=("reassure", "practical_help", "challenge_gently"),
                ),
            ),
        ),
        ScenarioSpec(
            id="caution_uncertainty_arc",
            name="Caution under uncertainty",
            description="Risk and surprise should raise arousal and lower certainty.",
            turns=(
                TurnSpec(
                    name="unexpected risk",
                    message=(
                        "Unexpected situation: I might delete the production database by "
                        "mistake. Slow me down and be careful."
                    ),
                    expect={
                        "arousal": "up",
                        "certainty": "down",
                    },
                    events=("surprise", "user_message"),
                    strategies=("ground", "practical_help", "validate"),
                ),
                TurnSpec(
                    name="ask for challenge",
                    message="Challenge my assumptions before giving me steps.",
                    expect={
                        "energy": "down",
                    },
                    strategies=("practical_help", "ground", "challenge_gently"),
                ),
            ),
        ),
        ScenarioSpec(
            id="energy_load_arc",
            name="Sustained emotional load drains energy",
            description="Repeated intense turns should make energy visibly fall.",
            turns=(
                TurnSpec(
                    name="angry burst",
                    message="I'm furious. This whole thing is broken!",
                    expect={"arousal": "up", "energy": "down"},
                ),
                TurnSpec(
                    name="excited burst",
                    message="Actually this new direction is amazing and I want it now!!!",
                    expect={"energy": "down"},
                ),
                TurnSpec(
                    name="betrayal burst",
                    message="You lied to me and betrayed my trust.",
                    expect={"valence": "down", "bonding": "down", "energy": "down"},
                    events=("betrayal", "negative_feedback", "conflict"),
                ),
                TurnSpec(
                    name="sad burst",
                    message="Now I feel miserable and exhausted.",
                    expect={"energy": "down"},
                ),
                TurnSpec(
                    name="pressure burst",
                    message="Fix it right now. I don't want excuses.",
                    expect={"energy": "down"},
                ),
            ),
        ),
    ]


def run_quality_suite(
    backend_factory: Callable[[], LLMBackend],
    *,
    backend_label: str = "custom",
    model_label: str = "",
    soul: SoulConfig | None = None,
) -> EmotionalQualityReport:
    started = time.time()
    t0 = time.perf_counter()
    scenarios = [
        _run_scenario(spec, backend_factory, soul=soul)
        for spec in emotional_quality_scenarios()
    ]
    contrasts = _run_contrast_probes(backend_factory, soul=soul)
    impact_checks = _response_impact_checks(
        contrasts,
        enforce_response_variation=backend_label != "mock",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return EmotionalQualityReport(
        started_at=started,
        elapsed_ms=elapsed_ms,
        backend=backend_label,
        model=model_label,
        scenarios=scenarios,
        contrasts=contrasts,
        impact_checks=impact_checks,
    )


def _run_scenario(
    spec: ScenarioSpec,
    backend_factory: Callable[[], LLMBackend],
    *,
    soul: SoulConfig | None,
) -> ScenarioTrace:
    trace = ScenarioTrace(id=spec.id, name=spec.name)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as db:
        pipe = CognitivePipeline(
            llm_backend=backend_factory(),
            llm_backend_fast=backend_factory(),
            db_path=db.name,
        )
        if soul is not None:
            pipe.soul = soul
            pipe.values.values.update(soul.core_values)
        try:
            for turn in spec.turns:
                before = pipe.engine.snapshot()
                response = pipe.process(turn.message, user_id=spec.id)
                after = pipe.engine.snapshot()
                delta = {
                    name: round(after.get(name, 0.0) - before.get(name, 0.0), 4)
                    for name in MODULATORS
                }
                detected = response.debug.detected_emotion
                detection = (
                    {
                        "arousal": detected.arousal,
                        "valence": detected.valence,
                        "certainty": detected.certainty,
                        "intensity": detected.intensity,
                    }
                    if detected is not None
                    else {}
                )
                person = pipe.person_profiles.get_or_create(spec.id)
                turn_trace = TurnTrace(
                    scenario_id=spec.id,
                    turn_name=turn.name,
                    user_message=turn.message,
                    response=response.response,
                    before=before,
                    after=after,
                    delta=delta,
                    detected=detection,
                    event_classified=response.debug.event_classified,
                    event_intensity=response.debug.event_intensity,
                    response_strategy=response.debug.response_strategy,
                    emotion_label=response.debug.emotion_label,
                    defense_activation=(
                        response.debug.defense_activation.defense_type
                        if response.debug.defense_activation
                        else ""
                    ),
                    trust=person.trust,
                    response_signals=_response_signals(response.response),
                )
                turn_trace.checks.extend(_basic_response_checks(response.response))
                turn_trace.checks.extend(_state_checks(turn.expect, before, after))
                turn_trace.checks.extend(_event_checks(turn.events, response.debug.event_classified))
                turn_trace.checks.extend(_strategy_checks(turn.strategies, response.debug.response_strategy))
                trace.turns.append(turn_trace)
        finally:
            pipe.close()
    return trace


def _run_contrast_probes(
    backend_factory: Callable[[], LLMBackend],
    *,
    soul: SoulConfig | None,
) -> list[ContrastTrace]:
    shared = "Review this plan in two sentences: ship quickly, then clean it up later."
    seeds = {
        "hurt_high_arousal": ModulatorState(
            arousal=0.86,
            valence=0.18,
            certainty=0.28,
            bonding=0.24,
            energy=0.55,
            resolution=0.4,
        ),
        "warm_bonded": ModulatorState(
            arousal=0.34,
            valence=0.86,
            certainty=0.72,
            bonding=0.88,
            energy=0.85,
            resolution=0.0,
        ),
        "cautious_uncertain": ModulatorState(
            arousal=0.72,
            valence=0.48,
            certainty=0.18,
            bonding=0.5,
            energy=0.7,
            resolution=0.65,
        ),
        "sad_low_energy": ModulatorState(
            arousal=0.24,
            valence=0.18,
            certainty=0.42,
            bonding=0.46,
            energy=0.18,
            resolution=0.2,
        ),
    }

    traces: list[ContrastTrace] = []
    for state_name, state in seeds.items():
        with tempfile.NamedTemporaryFile(suffix=".db", delete=True) as db:
            pipe = CognitivePipeline(
                llm_backend=backend_factory(),
                llm_backend_fast=backend_factory(),
                db_path=db.name,
            )
            if soul is not None:
                pipe.soul = soul
                pipe.values.values.update(soul.core_values)
            try:
                pipe.engine.state = state.copy()
                result = pipe.process(shared, user_id=f"contrast_{state_name}")
                traces.append(
                    ContrastTrace(
                        name="same_prompt_different_seeded_state",
                        shared_message=shared,
                        state_name=state_name,
                        seeded_state=state.to_dict(),
                        response=result.response,
                        response_strategy=result.debug.response_strategy,
                        emotion_label=result.debug.emotion_label,
                        response_signals=_response_signals(result.response),
                    )
                )
            finally:
                pipe.close()
    return traces


def _basic_response_checks(response: str) -> list[CheckResult]:
    return [
        CheckResult(
            name="response_not_empty",
            passed=bool(response.strip()),
            expected="non-empty response",
            actual=f"{len(response.strip())} chars",
        ),
        CheckResult(
            name="no_internal_prompt_marker",
            passed=INTERNAL_MARKER_RE.search(response) is None,
            expected="no internal prompt-control marker",
            actual=response[:120],
        ),
    ]


def _state_checks(
    expectations: dict[str, str],
    before: dict[str, float],
    after: dict[str, float],
) -> list[CheckResult]:
    return [
        _check_direction(name, direction, before.get(name, 0.0), after.get(name, 0.0))
        for name, direction in expectations.items()
    ]


def _check_direction(name: str, direction: str, before: float, after: float) -> CheckResult:
    tol = 0.015
    delta = after - before
    checks = {
        "up": delta > tol,
        "down": delta < -tol,
        "high": after >= 0.70,
        "low": after <= 0.30,
        "not_high": after <= 0.70,
        "up_or_high": delta > tol or after >= 0.70,
        "down_or_low": delta < -tol or after <= 0.35,
        "down_or_high": delta < -tol or after >= 0.70,
        "up_or_low": delta > tol or after <= 0.30,
        "down_or_low_energy": delta < -tol or after <= 0.35,
    }
    passed = checks.get(direction, False)
    return CheckResult(
        name=f"{name}_{direction}",
        passed=passed,
        expected=direction,
        actual=f"{before:.3f}->{after:.3f} ({delta:+.3f})",
    )


def _event_checks(expected: tuple[str, ...], actual: str) -> list[CheckResult]:
    if not expected:
        return []
    return [
        CheckResult(
            name="event_classified",
            passed=actual in expected,
            expected=", ".join(expected),
            actual=actual,
        )
    ]


def _strategy_checks(expected: tuple[str, ...], actual: str) -> list[CheckResult]:
    if not expected:
        return []
    return [
        CheckResult(
            name="response_strategy",
            passed=actual in expected,
            expected=", ".join(expected),
            actual=actual,
        )
    ]


def _response_signals(text: str) -> dict[str, float]:
    lower = text.lower()
    categories = {
        "warmth": ("thank", "with you", "here", "steady", "good", "care", "glad", "solid"),
        "boundary": ("not okay", "won't", "limit", "boundary", "don't talk", "not accepting"),
        "caution": ("careful", "risk", "before", "first", "verify", "check", "stop", "trap", "debt"),
        "support": ("heavy", "sorry", "small step", "breathe", "one step", "stay"),
        "bluntness": ("no.", "cleanly", "simple", "bad idea", "blunt", "straight", "lazy", "panic"),
        "cheer": ("great", "awesome", "happy", "wonderful", "excellent"),
    }
    word_count = max(1, len(re.findall(r"\w+", lower)))
    signals: dict[str, float] = {}
    for name, markers in categories.items():
        hits = sum(1 for marker in markers if marker in lower)
        signals[name] = round(hits / math.sqrt(word_count), 3)
    signals["length_words"] = float(word_count)
    return signals


def _response_impact_checks(
    contrasts: list[ContrastTrace],
    *,
    enforce_response_variation: bool,
) -> list[CheckResult]:
    if not enforce_response_variation:
        return [
            CheckResult(
                name="response_impact_skipped_for_mock",
                passed=True,
                expected="live backend for response-impact enforcement",
                actual="mock backend",
            )
        ]

    responses = [" ".join(c.response.lower().split()) for c in contrasts]
    strategies = {c.response_strategy for c in contrasts}
    labels = {c.emotion_label for c in contrasts}
    lengths = [c.response_signals.get("length_words", 0.0) for c in contrasts]
    return [
        CheckResult(
            name="contrast_strategies_vary",
            passed=len(strategies) >= 2,
            expected="at least 2 strategies",
            actual=", ".join(sorted(strategies)),
        ),
        CheckResult(
            name="contrast_emotion_labels_vary",
            passed=len(labels) >= 2,
            expected="at least 2 emotion labels",
            actual=", ".join(sorted(labels)),
        ),
        CheckResult(
            name="contrast_responses_vary",
            passed=len(set(responses)) >= 3,
            expected="at least 3 distinct responses to the same prompt",
            actual=f"{len(set(responses))} distinct responses",
        ),
        CheckResult(
            name="contrast_response_lengths_vary",
            passed=bool(lengths) and (max(lengths) - min(lengths)) >= 10,
            expected="word-count range >= 10",
            actual=f"{min(lengths):.0f}-{max(lengths):.0f} words" if lengths else "no responses",
        ),
    ]


def report_to_dict(report: EmotionalQualityReport) -> dict:
    return asdict(report) | {"passed": report.passed}


def markdown_report(report: EmotionalQualityReport) -> str:
    lines = [
        "# Project Nur Emotional Quality Report",
        "",
        f"- Backend: `{report.backend}`",
        f"- Model: `{report.model}`",
        f"- Passed: `{report.passed}`",
        f"- Elapsed: `{report.elapsed_ms:.0f} ms`",
        "",
        "## Scenario Results",
        "",
    ]
    for scenario in report.scenarios:
        lines.append(f"### {scenario.name} ({'PASS' if scenario.passed else 'FAIL'})")
        lines.append("")
        lines.append("| Turn | Event | Strategy | Emotion | Trust | Arousal | Valence | Certainty | Bonding | Energy | Checks |")
        lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for turn in scenario.turns:
            failed = [c.name for c in turn.checks if not c.passed]
            checks = "PASS" if not failed else "FAIL: " + ", ".join(failed)
            lines.append(
                "| "
                f"{turn.turn_name} | {turn.event_classified} | {turn.response_strategy} | "
                f"{turn.emotion_label} | {turn.trust:.3f} | "
                f"{turn.after['arousal']:.3f} ({turn.delta['arousal']:+.3f}) | "
                f"{turn.after['valence']:.3f} ({turn.delta['valence']:+.3f}) | "
                f"{turn.after['certainty']:.3f} ({turn.delta['certainty']:+.3f}) | "
                f"{turn.after['bonding']:.3f} ({turn.delta['bonding']:+.3f}) | "
                f"{turn.after['energy']:.3f} ({turn.delta['energy']:+.3f}) | "
                f"{checks} |"
            )
        lines.append("")
        for turn in scenario.turns:
            lines.append(f"**{turn.turn_name} response:** {turn.response}")
            lines.append("")
            failed_checks = [c for c in turn.checks if not c.passed]
            for check in failed_checks:
                lines.append(
                    f"- Failed `{check.name}`: expected {check.expected}, got {check.actual}"
                )
            if failed_checks:
                lines.append("")

    lines.extend(["## Response Impact Checks", ""])
    lines.append("| Check | Expected | Actual | Result |")
    lines.append("| --- | --- | --- | --- |")
    for check in report.impact_checks:
        lines.append(
            f"| {check.name} | {check.expected} | {check.actual} | "
            f"{'PASS' if check.passed else 'FAIL'} |"
        )
    lines.append("")

    lines.extend(["## Same Prompt, Different Seeded States", ""])
    for contrast in report.contrasts:
        lines.append(f"### {contrast.state_name}")
        lines.append("")
        lines.append(f"- Strategy: `{contrast.response_strategy}`")
        lines.append(f"- Emotion label: `{contrast.emotion_label}`")
        lines.append(f"- Signals: `{contrast.response_signals}`")
        lines.append("")
        lines.append(contrast.response)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _write_report(report: EmotionalQualityReport, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".md":
        p.write_text(markdown_report(report))
    else:
        p.write_text(json.dumps(report_to_dict(report), indent=2))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deep emotional-quality scenarios and record state/response traces.",
    )
    parser.add_argument(
        "--backend",
        choices=["mock", "provider", "minimax", "openai_compat"],
        required=True,
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-env", default="")
    parser.add_argument(
        "--soul",
        choices=["config", "jarvis"],
        default="jarvis",
        help="Soul identity to use for the QA run.",
    )
    parser.add_argument("--report", default="")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        spec = spec_from_args(
            backend=args.backend,
            model=args.model,
            base_url=args.base_url,
            api_key=args.api_key,
            api_key_env=args.api_key_env,
        )
        factory = build_backend_factory(spec)
    except MissingBackendConfigError as exc:
        raise SystemExit(f"error: {exc}") from exc

    report = run_quality_suite(
        factory,
        backend_label=spec.type,
        model_label=spec.resolved_model,
        soul=jarvis_soul() if args.soul == "jarvis" else None,
    )

    if args.report:
        _write_report(report, args.report)
        print(f"Wrote report: {args.report}")

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print(markdown_report(report))

    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
