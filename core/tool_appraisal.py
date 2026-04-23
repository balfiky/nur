"""Tool outcome appraisal — maps tool results back into cognition.

Converts a ToolResult into a ToolObservation with emotional deltas.
Pure function, zero LLM calls.

Default deltas from docs/internal/AGENTIC_TOOLS_DESIGN.md Section 12.2:

| Outcome             | Arousal | Valence | Certainty | Energy | Resolution |
|---------------------|---------|---------|-----------|--------|------------|
| Successful read     | +0.02   | +0.02   | +0.08     | -0.01  | -0.05      |
| No results          | +0.03   | -0.04   | -0.06     | -0.01  | +0.05      |
| Execution error     | +0.08   | -0.08   | -0.10     | -0.02  | +0.10      |
| Destructive success | +0.04   | +0.00   | +0.05     | -0.03  | -0.03      |
| Environment block   | +0.05   | -0.03   | -0.05     | -0.01  | +0.06      |
"""

from __future__ import annotations

from core.types import ToolCategory, ToolObservation, ToolResult


def appraise_tool_result(
    result: ToolResult,
    category: ToolCategory,
) -> ToolObservation:
    """Appraise a tool result into a cognitive observation.

    Args:
        result: The structured execution output.
        category: The tool's declared category.

    Returns:
        ToolObservation with emotional deltas and a summary.
    """
    if not result.success:
        return _appraise_failure(result)

    if category == ToolCategory.DESTRUCTIVE:
        return _appraise_destructive_success(result)

    return _appraise_read_success(result)


def _appraise_read_success(result: ToolResult) -> ToolObservation:
    """Successful read/search/write — certainty up, mild energy cost."""
    has_output = bool(result.output and result.output.strip())

    if not has_output:
        # No useful result (e.g. empty search)
        return ToolObservation(
            summary=f"{result.tool_name}: no useful output",
            emotional_delta={
                "arousal": 0.03,
                "valence": -0.04,
                "certainty": -0.06,
                "energy": -0.01,
            },
            certainty_delta=-0.06,
            resolution_delta=0.05,
            self_observation=None,
            continue_tool_loop=False,
        )

    return ToolObservation(
        summary=f"{result.tool_name}: success",
        emotional_delta={
            "arousal": 0.02,
            "valence": 0.02,
            "certainty": 0.08,
            "energy": -0.01,
        },
        certainty_delta=0.08,
        resolution_delta=-0.05,
        self_observation="methodical",
        continue_tool_loop=False,
    )


def _appraise_destructive_success(result: ToolResult) -> ToolObservation:
    """Successful destructive action — higher arousal, energy cost."""
    return ToolObservation(
        summary=f"{result.tool_name}: destructive action completed",
        emotional_delta={
            "arousal": 0.04,
            "valence": 0.00,
            "certainty": 0.05,
            "energy": -0.03,
        },
        certainty_delta=0.05,
        resolution_delta=-0.03,
        self_observation="decisive",
        continue_tool_loop=False,
    )


def _appraise_failure(result: ToolResult) -> ToolObservation:
    """Failed execution — arousal up, certainty down, resolution up."""
    error_msg = result.error or "unknown error"

    # Distinguish environment blocks from execution errors
    is_env_block = any(
        kw in error_msg.lower()
        for kw in ["permission", "denied", "forbidden", "not allowed", "blocked"]
    )

    if is_env_block:
        return ToolObservation(
            summary=f"{result.tool_name}: blocked — {error_msg}",
            emotional_delta={
                "arousal": 0.05,
                "valence": -0.03,
                "certainty": -0.05,
                "energy": -0.01,
            },
            certainty_delta=-0.05,
            resolution_delta=0.06,
            self_observation="hesitant",
            continue_tool_loop=False,
        )

    return ToolObservation(
        summary=f"{result.tool_name}: failed — {error_msg}",
        emotional_delta={
            "arousal": 0.08,
            "valence": -0.08,
            "certainty": -0.10,
            "energy": -0.02,
        },
        certainty_delta=-0.10,
        resolution_delta=0.10,
        self_observation="frustrated",
        continue_tool_loop=False,
    )
