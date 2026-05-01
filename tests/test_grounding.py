from __future__ import annotations

from core.grounding import extract_action_claims, verify_response_grounding
from core.types import ToolResult, ToolTrace


def test_no_issue_for_regular_capability_or_future_language():
    response = "I can draft a SKILL.md. If you enable tools, I can run the command later."

    assert extract_action_claims(response) == []
    assert verify_response_grounding(response, tool_trace=None) == []


def test_no_issue_for_negated_correction_language():
    response = (
        "I did not create a file. There is no Tool Execution Result showing "
        "that I read files or ran a command."
    )

    assert verify_response_grounding(response, tool_trace=None) == []


def test_unverified_external_action_claim_is_flagged_without_tool_trace():
    response = "I am reading the repository files and writing skill.md now."

    issues = verify_response_grounding(response, tool_trace=None)

    assert len(issues) == 1
    assert issues[0].code == "unverified_external_action_claim"
    assert set(issues[0].required_categories) == {"read", "write"}
    assert issues[0].evidence_categories == ()


def test_read_claim_is_grounded_by_read_tool_result():
    trace = ToolTrace(
        executed_results=[
            ToolResult(tool_name="fs.read_file", success=True, output="content"),
        ]
    )

    issues = verify_response_grounding(
        "I read the file and found the setting.",
        tool_trace=trace,
    )

    assert issues == []


def test_execute_claim_is_grounded_by_shell_tool_result():
    trace = ToolTrace(
        executed_results=[
            ToolResult(tool_name="shell.run_command", success=False, output="", error="exit 1"),
        ]
    )

    issues = verify_response_grounding(
        "The command failed and returned an error.",
        tool_trace=trace,
    )

    assert issues == []


def test_write_claim_is_not_grounded_by_read_only_web_result():
    trace = ToolTrace(
        executed_results=[
            ToolResult(tool_name="web.fetch", success=True, output="html"),
        ]
    )

    issues = verify_response_grounding(
        "I created the file after checking the page.",
        tool_trace=trace,
    )

    assert len(issues) == 1
    assert "write" in issues[0].required_categories
