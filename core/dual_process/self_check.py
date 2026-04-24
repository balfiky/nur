"""Self-check — lightweight validation of generated response.

Checks: tone fit, profile contradictions, overconfidence, bluntness.
Returns pass/fail with correction notes. If fail, the pipeline
regenerates with the correction note injected.

Uses LLM when available, falls back to rule-based checker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config.loader import get_config
from core.types import PipelineContext

if TYPE_CHECKING:
    from core.dual_process.generator import LLMBackend


# ---------------------------------------------------------------------------
# Self-check result
# ---------------------------------------------------------------------------

@dataclass
class SelfCheckResult:
    """Result of the self-check pass."""
    passed: bool = True
    issues: list[str] = field(default_factory=list)
    correction_note: str = ""

    @property
    def failed(self) -> bool:
        return not self.passed


# ---------------------------------------------------------------------------
# Self-checker (rule-based + optional LLM)
# ---------------------------------------------------------------------------

class SelfChecker:
    """Validates a response against the current emotional/profile context.

    When llm_client is provided, uses the LLM for richer checking.
    Falls back to rule-based checks when no LLM is available.
    """

    def __init__(self, llm_client: LLMBackend | None = None) -> None:
        self._llm_client = llm_client

    def check(
        self,
        response: str,
        ctx: PipelineContext,
    ) -> SelfCheckResult:
        """Run all checks against the generated response."""
        # Always run rule-based checks (fast, reliable)
        issues: list[str] = []
        issues.extend(self._check_tone_fit(response, ctx))
        issues.extend(self._check_overconfidence(response, ctx))
        issues.extend(self._check_bluntness(response, ctx))
        issues.extend(self._check_contradictions(response, ctx))
        issues.extend(self._check_energy_fit(response, ctx))

        # If LLM is available, also run LLM-based check for additional issues
        llm_correction = ""
        if self._llm_client is not None:
            llm_issues, llm_correction = self._llm_check(response, ctx)
            for issue in llm_issues:
                if issue not in issues:
                    issues.append(issue)

        if issues:
            # Prefer the LLM's targeted correction over generic synthesis
            if llm_correction:
                correction = llm_correction
            else:
                correction = "Self-check issues found. Please adjust:\n"
                correction += "\n".join(f"- {issue}" for issue in issues)
            return SelfCheckResult(
                passed=False,
                issues=issues,
                correction_note=correction,
            )

        return SelfCheckResult(passed=True)

    # ------------------------------------------------------------------
    # LLM-based check
    # ------------------------------------------------------------------

    def _llm_check(self, response: str, ctx: PipelineContext) -> tuple[list[str], str]:
        """Use LLM to check the response against context.

        Returns (issues, correction_note). correction_note is the LLM's
        targeted guidance; empty string if the LLM didn't provide one.
        """
        template = get_config().self_check_prompt
        if not template:
            return []

        snap = ctx.modulator_snapshot or {}
        self_profile_str = ""
        if ctx.self_profile:
            sp = ctx.self_profile
            parts = []
            if sp.strengths:
                parts.append(f"Strengths: {', '.join(sp.strengths)}")
            if sp.flaws:
                parts.append(f"Flaws: {', '.join(sp.flaws)}")
            self_profile_str = "; ".join(parts) if parts else "No profile data"

        prompt = template
        agent_name = ctx.soul_profile.name if ctx.soul_profile else get_config().soul.name
        prompt = prompt.replace("{agent_name}", agent_name)
        prompt = prompt.replace("{response}", response)
        prompt = prompt.replace("{modulator_snapshot}", json.dumps(snap, indent=2))
        prompt = prompt.replace("{self_profile}", self_profile_str)
        prompt = prompt.replace("{contradiction_flags}", "\n".join(ctx.contradiction_flags) if ctx.contradiction_flags else "None")

        try:
            result_text = self._llm_client.generate(prompt, response)
            # Try to parse JSON from the response
            result_text = result_text.strip()
            # Extract JSON if wrapped in markdown code block
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            data = json.loads(result_text)
            if not data.get("passed", True):
                return data.get("issues", []), data.get("correction_note", "")
        except (json.JSONDecodeError, KeyError, IndexError):
            pass

        return [], ""

    # ------------------------------------------------------------------
    # Rule-based checks
    # ------------------------------------------------------------------

    def _check_tone_fit(self, response: str, ctx: PipelineContext) -> list[str]:
        """Check if response tone matches modulator state."""
        issues = []
        snap = ctx.modulator_snapshot or {}
        valence = snap.get("valence", 0.5)

        # Positive markers in response when mood is very negative
        positive_markers = ["!", "great", "wonderful", "fantastic", "amazing", "love"]
        negative_markers = ["unfortunately", "sorry", "sadly", "regret", "afraid"]

        lower = response.lower()

        if valence < 0.25:
            positive_count = sum(1 for m in positive_markers if m in lower)
            if positive_count >= 2:
                issues.append(
                    f"Tone too positive for current negative mood (valence={valence:.2f}). "
                    "Dampen enthusiasm."
                )

        if valence > 0.75:
            negative_count = sum(1 for m in negative_markers if m in lower)
            if negative_count >= 2:
                issues.append(
                    f"Tone too negative for current positive mood (valence={valence:.2f}). "
                    "Let warmth show."
                )

        return issues

    def _check_overconfidence(self, response: str, ctx: PipelineContext) -> list[str]:
        """Flag overconfident language when certainty is low."""
        issues = []
        snap = ctx.modulator_snapshot or {}
        certainty = snap.get("certainty", 0.5)

        if certainty < 0.3:
            confident_phrases = [
                "definitely", "certainly", "absolutely", "without a doubt",
                "clearly", "obviously", "of course", "no question",
            ]
            lower = response.lower()
            found = [p for p in confident_phrases if p in lower]
            if found:
                issues.append(
                    f"Overconfident language ({', '.join(found)}) when certainty is low "
                    f"({certainty:.2f}). Hedge more."
                )

        return issues

    def _check_bluntness(self, response: str, ctx: PipelineContext) -> list[str]:
        """Flag bluntness when self-profile lists it as a flaw."""
        issues = []
        if not ctx.self_profile:
            return issues

        if "blunt" not in ctx.self_profile.flaws:
            return issues

        snap = ctx.modulator_snapshot or {}
        certainty = snap.get("certainty", 0.5)

        # When certainty is high AND bluntness is a known flaw, check for it
        if certainty > 0.7:
            blunt_markers = [
                "you should", "you need to", "just do", "simply",
                "that's wrong", "no.", "stop",
            ]
            lower = response.lower()
            found = [m for m in blunt_markers if m in lower]
            if found:
                issues.append(
                    f"Blunt language detected ({', '.join(found[:3])}) — "
                    f"known flaw 'blunt' + high certainty ({certainty:.2f}). Soften."
                )

        return issues

    def _check_contradictions(self, response: str, ctx: PipelineContext) -> list[str]:
        """Flag if response ignores active contradiction flags."""
        issues = []
        if not ctx.contradiction_flags:
            return issues

        # If there are active contradictions but response doesn't acknowledge them
        lower = response.lower()
        acknowledgment_markers = [
            "notice", "seem", "different", "unusual", "unlike",
            "change", "shift", "not like you", "something",
        ]
        has_acknowledgment = any(m in lower for m in acknowledgment_markers)

        if len(ctx.contradiction_flags) >= 2 and not has_acknowledgment:
            issues.append(
                f"Multiple contradictions detected ({len(ctx.contradiction_flags)}) "
                "but response doesn't address them. Consider acknowledging the shift."
            )

        return issues

    def _check_energy_fit(self, response: str, ctx: PipelineContext) -> list[str]:
        """Flag verbose responses when energy is very low."""
        issues = []
        snap = ctx.modulator_snapshot or {}
        energy = snap.get("energy", 1.0)

        if energy < 0.2 and len(response) > 500:
            issues.append(
                f"Response is {len(response)} chars but energy is very low "
                f"({energy:.2f}). Should be terser."
            )

        return issues
