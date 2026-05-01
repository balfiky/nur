"""LangGraph-backed tool orchestration for Nūr.

This adapter keeps Nūr's existing tool registry/executor, appraisal, traces,
and memory coupling, but delegates tool selection/execution flow to a
LangGraph tool-calling graph when configured.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import Field, create_model

from core.action_variables import derive_action_variables
from core.dual_process.tool_loop import (
    HARD_CAP_EXECUTIONS,
    DEFAULT_MAX_EXECUTIONS,
    ToolLoopResult,
    _apply_emotional_deltas,
    _summarize_for_generator,
    make_tool_decision,
    run_tool_loop as run_heuristic_tool_loop,
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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from nur_tools.executor import ToolExecutor


_TOOL_SYSTEM_PROMPT = """You are Nūr's tool router.

Use the provided tools when the user asks to inspect, search, fetch, run,
check, calculate, list, read, write, or act on external state. Do not answer
runtime facts from memory. If a tool is needed, call the tool. If no tool is
needed, return a short final answer without a tool call.

For machine inspection requests, prefer first-class system tools when present:
system__hostname for host identity, system__disk_usage for storage usage,
system__uname for OS/kernel, and system__installed_packages for installed
package inventory. Use shell__run_command only for explicit shell commands or
machine inspection that has no first-class tool.
For product, document, or other referenced links, use recent conversation to
identify the referenced items and call web__search with a targeted query.
Do not claim cleanup, deletion, or state changes unless a tool call actually
performed that action.
"""

_STRUCTURED_ROUTER_SYSTEM_PROMPT = """You are Nūr's structured tool router.

Return ONLY one JSON object:
{"tool_name": string|null, "arguments": object, "confidence": number, "rationale": string}

Choose a tool when the user is asking for external state, live/runtime facts,
machine inspection, web results, filesystem inspection, browser/calendar work,
or command execution. Do not answer those requests from memory.

The user_message field may include recent conversation. Use that history only
to resolve references like "it" or "that"; route the current user request.
If the current message is a short confirmation/correction after a previous
actionable request, route the previous actionable request instead of returning
no tool.

If a first-class tool can satisfy the request, choose it. Use shell.run_command
for explicit shell commands or machine inspection that has no safer first-class
tool. For shell.run_command, provide the exact command in {"cmd": "..."}.
Use precise read-only commands for inspection unless the user explicitly asks
for a state-changing command.

Examples:
- disk usage, storage fullness, drive capacity -> system.disk_usage {"path": "/"}
- hostname, machine name, node name -> system.hostname {}
- installed package prefix request -> system.installed_packages {"prefix": "<prefix>"}
- referenced product/document links -> web.search {"query": "<targeted referenced item query>"}
- current/latest web facts -> web.search {"query": "..."}

If no tool is needed, return {"tool_name": null, "arguments": {}, "confidence": 0, "rationale": "no tool needed"}.
"""

_ROUTER_PARSE_FAILED = object()


class LangGraphToolRunner:
    """Run Nūr tools through a LangGraph tool-calling loop."""

    def __init__(
        self,
        *,
        executor: ToolExecutor,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        fallback_to_heuristic: bool = False,
        chat_model: Any | None = None,
    ) -> None:
        if chat_model is None:
            if not base_url:
                raise ValueError("LangGraph tool runner requires llm_base_url")
            if not model:
                raise ValueError("LangGraph tool runner requires llm_model")
            chat_model = ChatOpenAI(
                model=model,
                base_url=base_url,
                api_key=api_key,
                temperature=0,
                timeout=timeout,
                max_retries=0,
            )

        self._executor = executor
        self._base_model = chat_model
        self._fallback_to_heuristic = fallback_to_heuristic
        self._safe_to_original: dict[str, str] = {}
        self._original_to_safe: dict[str, str] = {}
        self._last_results: list[ToolResult] = []

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
        """Run one tool turn and return the same shape as the legacy loop."""
        trust = person.trust if person else 0.5
        action_vars = derive_action_variables(
            state, trust=trust, defense_active=defense_active,
        )
        effective_action_vars = action_vars
        life_effects: dict[str, float] = {}

        # Preserve the existing explicit task-plan state machine. LangGraph
        # handles fresh multi-tool requests, but active plans are Nūr state.
        if active_plan and not active_plan.is_terminal:
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
                summary = _summarize_for_generator(observations, executed)
                return ToolLoopResult(
                    trace=ToolTrace(
                        executed_results=executed,
                        observations=observations,
                        loop_count=task_trace.steps_executed,
                        task_trace=task_trace,
                    ),
                    action_variables=action_vars,
                    tool_context_summary=summary,
                )

        tools = self._tools_allowed_by_policy(
            action_vars=action_vars,
            trust=trust,
            agency_decision=agency_decision,
            autonomy_level=autonomy_level,
            life_influence=life_influence,
        )
        if not tools:
            return ToolLoopResult(
                trace=ToolTrace(),
                action_variables=action_vars,
                tool_context_summary="",
            )

        self._last_results = []
        try:
            graph = self._compile_graph(tools)
            output = graph.invoke(
                {"messages": [HumanMessage(content=user_message)]},
                config={"recursion_limit": max(3, min(max_executions, hard_cap) * 2 + 3)},
            )
        except Exception as exc:
            if self._fallback_to_heuristic:
                return self._run_heuristic(
                    user_message=user_message,
                    state=state,
                    person=person,
                    defense_active=defense_active,
                    engine=engine,
                    max_executions=max_executions,
                    hard_cap=hard_cap,
                    active_plan=active_plan,
                    agency_decision=agency_decision,
                    autonomy_level=autonomy_level,
                    life_influence=life_influence,
                )
            failure = ToolResult(
                tool_name="langgraph.agent",
                success=False,
                output="",
                error=f"{type(exc).__name__}: {exc}",
            )
            return ToolLoopResult(
                trace=ToolTrace(executed_results=[failure], loop_count=0),
                action_variables=action_vars,
                tool_context_summary=f"[langgraph.agent] Failed: {failure.error}",
            )

        proposed = self._extract_proposed_intents(output, action_vars)
        executed_results = list(self._last_results)
        if not executed_results and self._fallback_to_heuristic:
            routed = self._run_structured_router(
                user_message=user_message,
                action_vars=action_vars,
                trust=trust,
                agency_decision=agency_decision,
                autonomy_level=autonomy_level,
                life_influence=life_influence,
                engine=engine,
            )
            if routed is not None:
                return routed
            return self._run_heuristic(
                user_message=user_message,
                state=state,
                person=person,
                defense_active=defense_active,
                engine=engine,
                max_executions=max_executions,
                hard_cap=hard_cap,
                active_plan=active_plan,
                agency_decision=agency_decision,
                autonomy_level=autonomy_level,
                life_influence=life_influence,
            )

        observations = [
            self._appraise_and_apply(result, engine) for result in executed_results
        ]
        if proposed:
            capability = self._executor._registry.get(proposed[0].tool_name)
            category = capability.category if capability else ToolCategory.READ_ONLY
            effective_action_vars = _with_life_influence(
                action_vars,
                life_influence,
                category,
            )
            life_effects.update(_action_effects(action_vars, effective_action_vars))
            for intent in proposed:
                _apply_action_variables_to_intent(intent, effective_action_vars)
        summary = _summarize_for_generator(observations, executed_results)
        final_decision = (
            ToolDecision(decision="execute", intent=proposed[0], rationale="LangGraph tool call")
            if executed_results and proposed
            else None
        )
        return ToolLoopResult(
            trace=ToolTrace(
                proposed_intents=proposed,
                final_decision=final_decision,
                executed_results=executed_results,
                observations=observations,
                loop_count=len(executed_results),
            ),
            action_variables=effective_action_vars,
            tool_context_summary=summary,
            life_influence_effects=life_effects,
        )

    def _compile_graph(self, tools: list[StructuredTool]) -> Any:
        model = self._base_model
        bind_tools = getattr(model, "bind_tools", None)
        if callable(bind_tools):
            try:
                model = bind_tools(tools)
            except NotImplementedError:
                pass

        def call_model(graph_state: MessagesState) -> dict[str, list[Any]]:
            messages = [SystemMessage(content=_TOOL_SYSTEM_PROMPT)]
            messages.extend(graph_state["messages"])
            return {"messages": [model.invoke(messages)]}

        def route(graph_state: MessagesState) -> str:
            last = graph_state["messages"][-1]
            return "tools" if getattr(last, "tool_calls", None) else END

        builder = StateGraph(MessagesState)
        builder.add_node("agent", call_model)
        builder.add_node("tools", ToolNode(tools))
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", route, {"tools": "tools", END: END})
        builder.add_edge("tools", "agent")
        return builder.compile()

    def _tools_allowed_by_policy(
        self,
        *,
        action_vars: ActionVariables,
        trust: float,
        agency_decision: AgencyDecision | None,
        autonomy_level: str,
        life_influence: LifeInfluence | None = None,
    ) -> list[StructuredTool]:
        return [
            self._to_langchain_tool(capability.name)
            for capability in self._capabilities_allowed_by_policy(
                action_vars=action_vars,
                trust=trust,
                agency_decision=agency_decision,
                autonomy_level=autonomy_level,
                life_influence=life_influence,
            )
        ]

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

    def _to_langchain_tool(self, original_name: str) -> StructuredTool:
        capability = self._executor._registry.get(original_name)
        if capability is None:
            raise ValueError(f"Unknown tool: {original_name}")
        safe_name = self._safe_tool_name(original_name)
        description = (
            f"{capability.description}. Nūr tool name: {original_name}. "
            f"Category: {capability.category.value}."
        )
        args_schema = _pydantic_args_schema(safe_name, capability.arg_schema)

        def call_tool(**kwargs: Any) -> str:
            result = self._executor.execute(original_name, dict(kwargs))
            self._last_results.append(result)
            if result.success:
                return result.output or result.side_effect_summary or "success"
            return f"ERROR: {result.error or 'tool failed'}"

        return StructuredTool.from_function(
            func=call_tool,
            name=safe_name,
            description=description,
            args_schema=args_schema,
        )

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

    def _extract_proposed_intents(
        self, output: dict[str, Any], action_vars: ActionVariables,
    ) -> list[ToolIntent]:
        intents: list[ToolIntent] = []
        for message in output.get("messages", []):
            if not isinstance(message, AIMessage):
                continue
            for call in getattr(message, "tool_calls", None) or []:
                safe_name = str(call.get("name") or "")
                original_name = self._safe_to_original.get(safe_name, safe_name)
                args = call.get("args") or {}
                intents.append(
                    ToolIntent(
                        tool_name=original_name,
                        arguments=dict(args),
                        reason="LangGraph model-native tool call",
                        expected_outcome=f"Execute {original_name}",
                        urgency=action_vars.action_urgency,
                        risk_tolerance=action_vars.risk_tolerance,
                        autonomy_bias=action_vars.autonomy_bias,
                        clarification_threshold=action_vars.clarification_threshold,
                        persistence_drive=action_vars.persistence_drive,
                        confidence=0.7,
                    )
                )
        return intents

    def _run_structured_router(
        self,
        *,
        user_message: str,
        action_vars: ActionVariables,
        trust: float,
        agency_decision: AgencyDecision | None,
        autonomy_level: str,
        life_influence: LifeInfluence | None,
        engine: Any,
    ) -> ToolLoopResult | None:
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

        route = self._ask_structured_router(user_message, capabilities)
        if route is _ROUTER_PARSE_FAILED:
            return None
        if route is None:
            return ToolLoopResult(
                trace=ToolTrace(),
                action_variables=action_vars,
                tool_context_summary="",
            )

        tool_name, arguments, confidence, rationale = route
        capability = self._executor._registry.get(tool_name)
        if capability is None:
            return None
        adjusted_action_vars = _with_life_influence(
            action_vars,
            life_influence,
            capability.category,
        )
        life_effects = _action_effects(action_vars, adjusted_action_vars)

        intent = ToolIntent(
            tool_name=tool_name,
            arguments=arguments,
            reason=rationale or "Structured router selected tool",
            expected_outcome=f"Execute {tool_name}",
            urgency=adjusted_action_vars.action_urgency,
            risk_tolerance=adjusted_action_vars.risk_tolerance,
            autonomy_bias=adjusted_action_vars.autonomy_bias,
            clarification_threshold=adjusted_action_vars.clarification_threshold,
            persistence_drive=adjusted_action_vars.persistence_drive,
            confidence=confidence,
        )
        decision = make_tool_decision(
            intent,
            adjusted_action_vars,
            capability.category,
            trust,
            agency_decision=agency_decision,
            autonomy_level=autonomy_level,
        )
        if decision.decision != "execute":
            return ToolLoopResult(
                trace=ToolTrace(
                    proposed_intents=[intent],
                    final_decision=decision,
                    loop_count=0,
                ),
                action_variables=adjusted_action_vars,
                tool_context_summary="",
                life_influence_effects=life_effects,
            )

        result = self._executor.execute(tool_name, arguments)
        observation = self._appraise_and_apply(result, engine)
        return ToolLoopResult(
            trace=ToolTrace(
                proposed_intents=[intent],
                final_decision=decision,
                executed_results=[result],
                observations=[observation],
                loop_count=1,
            ),
            action_variables=adjusted_action_vars,
            tool_context_summary=_summarize_for_generator([observation], [result]),
            life_influence_effects=life_effects,
        )

    def _ask_structured_router(
        self,
        user_message: str,
        capabilities: list[Any],
    ) -> (
        tuple[str, dict[str, Any], float, str]
        | None
        | object
    ):
        tools_payload = [
            {
                "name": capability.name,
                "description": capability.description,
                "category": capability.category.value,
                "arguments": capability.arg_schema,
            }
            for capability in capabilities
        ]
        payload = {
            "user_message": user_message,
            "available_tools": tools_payload,
        }
        messages = [
            SystemMessage(content=_STRUCTURED_ROUTER_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]

        data: dict[str, Any] | None = None
        bind = getattr(self._base_model, "bind", None)
        if callable(bind):
            try:
                response = bind(response_format={"type": "json_object"}).invoke(messages)
                data = _parse_router_json(_message_text(response))
            except Exception:
                data = None

        if data is None:
            try:
                response = self._base_model.invoke(messages)
            except Exception:
                return _ROUTER_PARSE_FAILED
            data = _parse_router_json(_message_text(response))
            if data is None:
                return _ROUTER_PARSE_FAILED

        tool_name_raw = data.get("tool_name")
        if tool_name_raw is None:
            return None
        tool_name = str(tool_name_raw).strip()
        allowed = {capability.name for capability in capabilities}
        if tool_name not in allowed:
            return _ROUTER_PARSE_FAILED

        arguments_raw = data.get("arguments", {})
        if not isinstance(arguments_raw, dict):
            return _ROUTER_PARSE_FAILED
        confidence = _coerce_confidence(data.get("confidence"))
        rationale = str(data.get("rationale") or "Structured router selected tool")
        return tool_name, dict(arguments_raw), confidence, rationale

    def _appraise_and_apply(self, result: ToolResult, engine: Any) -> ToolObservation:
        capability = self._executor._registry.get(result.tool_name)
        category = capability.category if capability else ToolCategory.READ_ONLY
        observation = appraise_tool_result(result, category)
        _apply_emotional_deltas(engine, observation)
        return observation

    def _run_heuristic(self, **kwargs: Any) -> ToolLoopResult:
        return run_heuristic_tool_loop(executor=self._executor, **kwargs)


def _pydantic_args_schema(tool_name: str, arg_schema: dict[str, Any]) -> type[Any]:
    fields: dict[str, tuple[type[Any], Any]] = {}
    for arg_name, spec in arg_schema.items():
        py_type = _schema_type(spec.get("type"))
        required = bool(spec.get("required", False))
        default = ... if required else spec.get("default", None)
        fields[arg_name] = (
            py_type,
            Field(default, description=str(spec.get("description", ""))),
        )
    if not fields:
        fields["unused"] = (str | None, Field(None, description="unused"))
    return create_model(f"{tool_name}_Args", **fields)


def _schema_type(value: str | None) -> type[Any]:
    if value == "number":
        return float
    if value == "integer":
        return int
    if value == "boolean":
        return bool
    if value == "object":
        return dict
    if value == "array":
        return list
    return str


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, dict) and isinstance(item.get("content"), str):
                parts.append(item["content"])
        return "\n".join(parts)
    return str(content or "")


def _parse_router_json(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
        candidate = re.sub(r"\s*```$", "", candidate)
    if not candidate.startswith("{"):
        match = re.search(r"\{.*\}", candidate, flags=re.S)
        if match:
            candidate = match.group(0)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, confidence))


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
