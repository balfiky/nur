from __future__ import annotations

from core.grounding import (
    extract_action_claims,
    grounding_correction_response,
    verify_response_grounding,
)
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


def test_no_issue_for_conditional_registry_language():
    response = "If you create a skill, import it through Admin > Skills."

    assert extract_action_claims(response) == []
    assert verify_response_grounding(response, tool_trace=None) == []


def test_no_issue_for_negated_registry_language():
    response = "I did not create or enable a skill."

    assert extract_action_claims(response) == []
    assert verify_response_grounding(response, tool_trace=None) == []


def test_no_issue_for_third_party_registry_action():
    response = "The operator added a skill yesterday."

    assert extract_action_claims(response) == []
    assert verify_response_grounding(response, tool_trace=None) == []


def test_unverified_external_action_claim_is_flagged_without_tool_trace():
    response = "I am reading the repository files and writing skill.md now."

    issues = verify_response_grounding(response, tool_trace=None)

    assert len(issues) == 1
    assert issues[0].code == "unverified_external_action_claim"
    assert set(issues[0].required_categories) == {"read", "write"}
    assert issues[0].evidence_categories == ()


def test_unverified_durable_capability_claim_is_flagged_without_tool_trace():
    response = "Added. The requested capability is now part of my durable runtime context."

    issues = verify_response_grounding(response, tool_trace=None)

    assert len(issues) == 1
    assert issues[0].code == "unverified_external_action_claim"
    assert issues[0].required_categories == ("registry_write",)


def test_done_here_is_skill_claim_requires_registry_write():
    response = "Done. Here is the skill to transform reports into action items."

    issues = verify_response_grounding(response, tool_trace=None)

    assert len(issues) == 1
    assert issues[0].required_categories == ("registry_write",)


def test_skill_draft_language_is_not_registry_claim():
    response = "Here is a SKILL.md draft you can import through Admin > Skills."

    assert extract_action_claims(response) == []
    assert verify_response_grounding(response, tool_trace=None) == []


def test_overlapping_registry_claims_are_deduplicated():
    claims = extract_action_claims("I added the capability to my registry.")

    assert len(claims) == 1
    assert claims[0].categories == {"registry_write"}


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


def test_success_claim_requires_successful_shell_result():
    """A failed shell call must not ground a "command succeeded" claim.

    Regression for the youtube-dl transcript where Nūr narrated a successful
    download even though the underlying shell command exited non-zero.
    """
    failed_trace = ToolTrace(
        executed_results=[
            ToolResult(tool_name="shell.run_command", success=False, output="", error="exit 1"),
        ]
    )
    issues = verify_response_grounding(
        "The final command succeeded where the earlier ones failed.",
        tool_trace=failed_trace,
    )
    assert len(issues) == 1
    assert "execute_success" in issues[0].required_categories

    success_trace = ToolTrace(
        executed_results=[
            ToolResult(tool_name="shell.run_command", success=True, output="ok"),
        ]
    )
    issues = verify_response_grounding(
        "The final command succeeded where the earlier ones failed.",
        tool_trace=success_trace,
    )
    assert issues == []


def test_soft_environment_narration_is_caught_without_evidence():
    """Phrases like "I'm forcing the environment initialization" without any
    tool execution are extracted as execute claims and flagged."""
    issues = verify_response_grounding(
        "I'm forcing the environment initialization now so we can finally "
        "get to that Python script.",
        tool_trace=None,
    )
    assert len(issues) == 1
    assert "execute" in issues[0].required_categories


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


def test_registry_claim_is_grounded_by_skill_registry_write_tool():
    trace = ToolTrace(
        executed_results=[
            ToolResult(
                tool_name="skills.create_from_request",
                success=True,
                output="{}",
            ),
        ]
    )

    issues = verify_response_grounding(
        "Added. The requested capability is now part of my durable runtime context.",
        tool_trace=trace,
    )

    assert issues == []


def test_correction_preserves_successful_registry_write_context():
    trace = ToolTrace(
        executed_results=[
            ToolResult(
                tool_name="skills.create_from_request",
                success=True,
                output="{}",
                metadata={
                    "skill_id": "report-writer",
                    "enabled": True,
                    "status": "enabled",
                },
                side_effect_summary="skill registry updated: imported report-writer; enabled=true",
            ),
        ]
    )
    issues = verify_response_grounding(
        "I created the skill and ran the shell setup command.",
        tool_trace=trace,
    )

    response = grounding_correction_response(issues, tool_trace=trace)

    assert "Skill registry updated: report-writer" in response
    assert "enabled=true" in response
    assert "additional external actions" in response


def test_correction_prefers_registry_write_evidence_over_registry_issue():
    trace = ToolTrace(
        executed_results=[
            ToolResult(
                tool_name="skills.create_from_request",
                success=True,
                output="{}",
                metadata={"skill_id": "report-writer"},
            ),
        ]
    )

    response = grounding_correction_response(
        [
            # Defensive regression case: even if an upstream verifier reports a
            # registry-write issue, actual registry-write evidence must win.
            verify_response_grounding("I enabled the skill.", tool_trace=None)[0],
        ],
        tool_trace=trace,
    )

    assert "Skill registry updated: report-writer" in response
    assert "No skill-registry Tool Execution Result ran" not in response


def test_registry_claim_is_not_grounded_by_skill_list_tool():
    trace = ToolTrace(
        executed_results=[
            ToolResult(tool_name="skills.list", success=True, output="{}"),
        ]
    )

    issues = verify_response_grounding(
        "I enabled the skill in my registry.",
        tool_trace=trace,
    )

    assert len(issues) == 1
    assert issues[0].required_categories == ("registry_write",)
