"""OpenAI-compatible native tool-call orchestration for Nūr.

This runner talks directly to an OpenAI-compatible chat-completions endpoint
and executes the returned ``message.tool_calls`` through Nūr's existing
ToolExecutor. It is intentionally small: model-native tool selection,
policy check, execution, appraisal, and a generator-ready summary.
"""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from core.action_variables import derive_action_variables
from core.dual_process.tool_loop import (
    HARD_CAP_EXECUTIONS,
    DEFAULT_MAX_EXECUTIONS,
    ToolLoopResult,
    _apply_emotional_deltas,
    _summarize_for_generator,
    make_tool_decision,
)
from core.life_influence import (
    LifeInfluence,
    action_variable_deltas,
    apply_life_influence_to_action_variables,
)
from core.task_planning import (
    execute_plan,
    is_continue_request,
    is_status_request,
    summarize_plan_status,
)
from core.tool_appraisal import appraise_tool_result
from core.types import (
    ActionVariables,
    AgencyDecision,
    ModulatorState,
    PersonProfile,
    TaskPlan,
    ToolCategory,
    ToolDecision,
    ToolIntent,
    ToolObservation,
    ToolResult,
    ToolTrace,
)
from nur_tools.executor import ToolExecutor


_NO_TOOL_SAFE_NAME = "control__no_tool"

_NATIVE_TOOL_SYSTEM_PROMPT = """You are Nūr's tool-call controller.

Use the provided tools when the user asks to inspect, search, fetch, run,
check, calculate, list, read, write, or act on external state. Do not answer
runtime facts from memory.

Always choose exactly one of the provided tools. If no external tool is needed,
choose control__no_tool and explain why in its reason argument.

Prefer first-class system tools for hostname, OS/kernel, and disk-usage
questions. Use shell tools only for explicit shell commands or machine
inspection that has no first-class tool.

For installed OS package inventory, apt/dpkg package lists, and package-name
prefix questions such as "packages starting with nvidia", prefer
system.installed_packages over ad-hoc shell commands.

For Amazon/product links or follow-ups like "their links", "all", or "give me
the Amazon links", use recent conversation to identify the referenced items and
call web.search. For specific book/product titles, search targeted queries such
as site:amazon.com plus the title instead of asking the user to search manually.

If the user is correcting a previous assistant message like "give me output,
not the command" or confirming a pending tool action, resolve that request
from the recent conversation and call the needed tool. Do not say you are
running, checking, waiting for, or executing anything unless you actually call
a tool.
"""


class NativeToolCallRunner:
    """Run Nūr tools through native OpenAI-compatible ``tool_calls``."""

    def __init__(
        self,
        *,
        executor: ToolExecutor,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        client: Any | None = None,
    ) -> None:
        if client is None:
            if not base_url:
                raise ValueError("Native tool runner requires llm_base_url")
            if not model:
                raise ValueError("Native tool runner requires llm_model")
            client = OpenAI(
                base_url=base_url,
                api_key=api_key or "dummy",
                timeout=timeout,
                max_retries=0,
            )

        self._executor = executor
        self._client = client
        self._model = model
        self._safe_to_original: dict[str, str] = {}
        self._original_to_safe: dict[str, str] = {}

    def run_tool_loop(
        self,
        *,
        user_message: str,
        state: ModulatorState,
        person: PersonProfile | None,
        defense_active: bool,
        engine: Any,
        max_executions: int = DEFAULT_MAX_EXECUTIONS,
        hard_cap: int = HARD_CAP_EXECUTIONS,
        active_plan: TaskPlan | None = None,
        agency_decision: AgencyDecision | None = None,
        autonomy_level: str = "autonomous",
        life_influence: LifeInfluence | None = None,
    ) -> ToolLoopResult:
        trust = person.trust if person else 0.5
        action_vars = derive_action_variables(
            state, trust=trust, defense_active=defense_active,
        )
        effective_action_vars = action_vars
        life_effects: dict[str, float] = {}

        plan_result = self._maybe_run_active_plan(
            user_message=user_message,
            active_plan=active_plan,
            action_vars=action_vars,
            engine=engine,
        )
        if plan_result is not None:
            return plan_result

        capabilities = self._capabilities_allowed_by_policy(
            action_vars=action_vars,
            trust=trust,
            agency_decision=agency_decision,
            autonomy_level=autonomy_level,
            life_influence=life_influence,
        )
        if not capabilities:
            return ToolLoopResult(
                trace=ToolTrace(),
                action_variables=action_vars,
                tool_context_summary="",
            )

        tools = [self._tool_payload(capability) for capability in capabilities]
        tools.append(_no_tool_payload())
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _NATIVE_TOOL_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

        proposed: list[ToolIntent] = []
        executed: list[ToolResult] = []
        observations: list[ToolObservation] = []
        final_decision: ToolDecision | None = None
        execution_limit = max(1, min(max_executions, hard_cap))

        try:
            while len(executed) < execution_limit:
                message = self._create_tool_choice(messages, tools)
                tool_calls = _extract_tool_calls(message)
                if not tool_calls:
                    break

                messages.append(_assistant_message_for_history(message, tool_calls))
                executed_this_round = 0
                for call in tool_calls:
                    if len(executed) >= execution_limit:
                        break
                    if self._is_no_tool_call(call):
                        return ToolLoopResult(
                            trace=ToolTrace(
                                proposed_intents=proposed,
                                final_decision=final_decision,
                                executed_results=executed,
                                observations=observations,
                                loop_count=len(executed),
                            ),
                            action_variables=action_vars,
                            tool_context_summary=_summarize_for_generator(
                                observations, executed,
                            ),
                        )

                    intent, parse_error = self._intent_from_tool_call(
                        call,
                        action_vars=action_vars,
                    )
                    if intent is None:
                        result = parse_error or ToolResult(
                            tool_name="native.tool_call",
                            success=False,
                            output="",
                            error="Malformed model tool call",
                        )
                        executed.append(result)
                        messages.append(_tool_message_for_history(call, result))
                        continue

                    proposed.append(intent)
                    capability = self._executor._registry.get(intent.tool_name)
                    category = capability.category if capability else ToolCategory.READ_ONLY
                    adjusted_action_vars = _with_life_influence(
                        action_vars,
                        life_influence,
                        category,
                    )
                    effective_action_vars = adjusted_action_vars
                    life_effects.update(_action_effects(action_vars, adjusted_action_vars))
                    _apply_action_variables_to_intent(intent, adjusted_action_vars)
                    decision = make_tool_decision(
                        intent,
                        adjusted_action_vars,
                        category,
                        trust,
                        agency_decision=agency_decision,
                        autonomy_level=autonomy_level,
                    )
                    if final_decision is None:
                        final_decision = decision
                    if decision.decision != "execute":
                        return ToolLoopResult(
                            trace=ToolTrace(
                                proposed_intents=proposed,
                                final_decision=decision,
                                executed_results=executed,
                                observations=observations,
                                loop_count=len(executed),
                            ),
                            action_variables=adjusted_action_vars,
                            tool_context_summary=_summarize_for_generator(
                                observations, executed,
                            ),
                            life_influence_effects=life_effects,
                        )

                    result = self._executor.execute(intent.tool_name, intent.arguments)
                    observation = self._appraise_and_apply(result, engine)
                    executed.append(result)
                    observations.append(observation)
                    executed_this_round += 1
                    messages.append(_tool_message_for_history(call, result))

                if executed_this_round == 0:
                    break
        except Exception as exc:
            failure = ToolResult(
                tool_name="native.tool_call",
                success=False,
                output="",
                error=f"{type(exc).__name__}: {exc}",
            )
            return ToolLoopResult(
                trace=ToolTrace(executed_results=[failure], loop_count=0),
                action_variables=action_vars,
                tool_context_summary=f"[native.tool_call] Failed: {failure.error}",
            )

        return ToolLoopResult(
            trace=ToolTrace(
                proposed_intents=proposed,
                final_decision=final_decision,
                executed_results=executed,
                observations=observations,
                loop_count=len(executed),
            ),
            action_variables=effective_action_vars,
            tool_context_summary=_summarize_for_generator(observations, executed),
            life_influence_effects=life_effects,
        )

    def _maybe_run_active_plan(
        self,
        *,
        user_message: str,
        active_plan: TaskPlan | None,
        action_vars: ActionVariables,
        engine: Any,
    ) -> ToolLoopResult | None:
        if not active_plan or active_plan.is_terminal:
            return None
        if is_status_request(user_message):
            return ToolLoopResult(
                trace=ToolTrace(),
                action_variables=action_vars,
                tool_context_summary=summarize_plan_status(active_plan),
            )
        if is_continue_request(user_message):
            task_trace = execute_plan(
                active_plan, self._executor, action_vars, engine=engine,
            )
            executed = [
                step.result for step in active_plan.steps if step.result is not None
            ]
            observations = [
                step.observation
                for step in active_plan.steps
                if step.observation is not None
            ]
            return ToolLoopResult(
                trace=ToolTrace(
                    executed_results=executed,
                    observations=observations,
                    loop_count=task_trace.steps_executed,
                    task_trace=task_trace,
                ),
                action_variables=action_vars,
                tool_context_summary=_summarize_for_generator(observations, executed),
            )
        return None

    def _capabilities_allowed_by_policy(
        self,
        *,
        action_vars: ActionVariables,
        trust: float,
        agency_decision: AgencyDecision | None,
        autonomy_level: str,
        life_influence: LifeInfluence | None = None,
    ) -> list[Any]:
        capabilities: list[Any] = []
        for capability in self._executor._registry.list_tools():
            adjusted_action_vars = _with_life_influence(
                action_vars,
                life_influence,
                capability.category,
            )
            intent = ToolIntent(
                tool_name=capability.name,
                arguments={},
                reason="Tool availability policy",
                expected_outcome="Allow model-native tool selection",
                urgency=adjusted_action_vars.action_urgency,
                risk_tolerance=adjusted_action_vars.risk_tolerance,
                autonomy_bias=adjusted_action_vars.autonomy_bias,
                clarification_threshold=adjusted_action_vars.clarification_threshold,
                persistence_drive=adjusted_action_vars.persistence_drive,
            )
            decision = make_tool_decision(
                intent,
                adjusted_action_vars,
                capability.category,
                trust,
                agency_decision=agency_decision,
                autonomy_level=autonomy_level,
            )
            if decision.decision == "execute":
                capabilities.append(capability)
        return capabilities

    def _tool_payload(self, capability: Any) -> dict[str, Any]:
        safe_name = self._safe_tool_name(capability.name)
        description = (
            f"{capability.description}. Nūr tool name: {capability.name}. "
            f"Category: {capability.category.value}."
        )
        return {
            "type": "function",
            "function": {
                "name": safe_name,
                "description": description,
                "parameters": _json_schema(capability.arg_schema),
            },
        }

    def _safe_tool_name(self, original_name: str) -> str:
        if original_name in self._original_to_safe:
            return self._original_to_safe[original_name]
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "__", original_name).strip("_")
        if not safe:
            safe = "tool"
        base = safe
        counter = 2
        while safe in self._safe_to_original:
            safe = f"{base}_{counter}"
            counter += 1
        self._original_to_safe[original_name] = safe
        self._safe_to_original[safe] = original_name
        return safe

    def _create_tool_choice(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools,
            tool_choice="required",
            temperature=0,
        )
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not choices:
            raise RuntimeError("tool-call response contained no choices")
        choice = choices[0]
        return _get_value(choice, "message")

    def _intent_from_tool_call(
        self,
        call: Any,
        *,
        action_vars: ActionVariables,
    ) -> tuple[ToolIntent | None, ToolResult | None]:
        function = _get_value(call, "function")
        safe_name = str(_get_value(function, "name") or "")
        original_name = self._safe_to_original.get(safe_name)
        if not original_name:
            return None, ToolResult(
                tool_name=safe_name or "native.tool_call",
                success=False,
                output="",
                error=f"Unknown model tool call: {safe_name or '<empty>'}",
            )

        arguments_text = _get_value(function, "arguments") or "{}"
        try:
            arguments = json.loads(arguments_text)
        except (TypeError, json.JSONDecodeError) as exc:
            return None, ToolResult(
                tool_name=original_name,
                success=False,
                output="",
                error=f"Invalid tool arguments: {exc}",
                metadata={"raw_arguments": str(arguments_text)},
            )
        if not isinstance(arguments, dict):
            return None, ToolResult(
                tool_name=original_name,
                success=False,
                output="",
                error="Tool arguments must be a JSON object",
                metadata={"raw_arguments": str(arguments_text)},
            )

        return ToolIntent(
            tool_name=original_name,
            arguments=arguments,
            reason="OpenAI-compatible model-native tool call",
            expected_outcome=f"Execute {original_name}",
            urgency=action_vars.action_urgency,
            risk_tolerance=action_vars.risk_tolerance,
            autonomy_bias=action_vars.autonomy_bias,
            clarification_threshold=action_vars.clarification_threshold,
            persistence_drive=action_vars.persistence_drive,
            confidence=0.8,
        ), None

    def _is_no_tool_call(self, call: Any) -> bool:
        function = _get_value(call, "function")
        return str(_get_value(function, "name") or "") == _NO_TOOL_SAFE_NAME

    def _appraise_and_apply(self, result: ToolResult, engine: Any) -> ToolObservation:
        capability = self._executor._registry.get(result.tool_name)
        category = capability.category if capability else ToolCategory.READ_ONLY
        observation = appraise_tool_result(result, category)
        _apply_emotional_deltas(engine, observation)
        return observation

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


def _json_schema(arg_schema: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, spec in arg_schema.items():
        if bool(spec.get("required", False)):
            required.append(name)
        prop: dict[str, Any] = {"type": _json_type(spec.get("type"))}
        if spec.get("description"):
            prop["description"] = str(spec["description"])
        if "default" in spec:
            prop["default"] = spec["default"]
        if "enum" in spec:
            prop["enum"] = spec["enum"]
        properties[name] = prop
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _no_tool_payload() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": _NO_TOOL_SAFE_NAME,
            "description": (
                "Choose this when the user does not need external state, live "
                "runtime facts, files, web, browser, calendar, or command execution."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why no external tool is needed.",
                    },
                },
                "required": ["reason"],
            },
        },
    }


def _json_type(value: str | None) -> str:
    if value in {"string", "number", "integer", "boolean", "object", "array"}:
        return value
    return "string"


def _extract_tool_calls(message: Any) -> list[Any]:
    calls = _get_value(message, "tool_calls")
    if calls is None:
        return []
    return list(calls)


def _assistant_message_for_history(message: Any, tool_calls: list[Any]) -> dict[str, Any]:
    content = _get_value(message, "content")
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [_tool_call_for_history(call) for call in tool_calls],
    }


def _tool_call_for_history(call: Any) -> dict[str, Any]:
    call_id = str(_get_value(call, "id") or "")
    function = _get_value(call, "function")
    return {
        "id": call_id,
        "type": str(_get_value(call, "type") or "function"),
        "function": {
            "name": str(_get_value(function, "name") or ""),
            "arguments": str(_get_value(function, "arguments") or "{}"),
        },
    }


def _tool_message_for_history(call: Any, result: ToolResult) -> dict[str, Any]:
    safe_name = str(_get_value(_get_value(call, "function"), "name") or "")
    return {
        "role": "tool",
        "tool_call_id": str(_get_value(call, "id") or ""),
        "name": safe_name,
        "content": _tool_result_content(result),
    }


def _tool_result_content(result: ToolResult) -> str:
    return json.dumps(
        {
            "tool_name": result.tool_name,
            "success": result.success,
            "output": result.output,
            "error": result.error,
            "metadata": result.metadata,
            "side_effect_summary": result.side_effect_summary,
        },
        ensure_ascii=False,
    )


def _get_value(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _with_life_influence(
    action_vars: ActionVariables,
    life_influence: LifeInfluence | None,
    category: ToolCategory,
) -> ActionVariables:
    if life_influence is None or life_influence.is_neutral:
        return action_vars
    return apply_life_influence_to_action_variables(
        action_vars,
        life_influence,
        read_only_action=category == ToolCategory.READ_ONLY,
    )


def _action_effects(before: ActionVariables, after: ActionVariables) -> dict[str, float]:
    return {
        key: value
        for key, value in action_variable_deltas(before, after).items()
        if value != 0.0
    }


def _apply_action_variables_to_intent(
    intent: ToolIntent,
    action_vars: ActionVariables,
) -> None:
    intent.urgency = action_vars.action_urgency
    intent.risk_tolerance = action_vars.risk_tolerance
    intent.autonomy_bias = action_vars.autonomy_bias
    intent.clarification_threshold = action_vars.clarification_threshold
    intent.persistence_drive = action_vars.persistence_drive
