"""LangGraph-backed tool orchestration for Nūr.

This adapter keeps Nūr's existing tool registry/executor, appraisal, traces,
and memory coupling, but delegates tool selection/execution flow to a
LangGraph tool-calling graph when configured.
"""

from __future__ import annotations

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

For machine inspection requests, prefer shell__run_command with commands such
as hostname, df -h /, uname -a, or other precise read-only inspection commands.
Do not claim cleanup, deletion, or state changes unless a tool call actually
performed that action.
"""


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
    ) -> ToolLoopResult:
        """Run one tool turn and return the same shape as the legacy loop."""
        trust = person.trust if person else 0.5
        action_vars = derive_action_variables(
            state, trust=trust, defense_active=defense_active,
        )

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
            )

        observations = [
            self._appraise_and_apply(result, engine) for result in executed_results
        ]
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
            action_variables=action_vars,
            tool_context_summary=summary,
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
    ) -> list[StructuredTool]:
        tools: list[StructuredTool] = []
        for capability in self._executor._registry.list_tools():
            intent = ToolIntent(
                tool_name=capability.name,
                arguments={},
                reason="LangGraph tool availability policy",
                expected_outcome="Allow model-native tool selection",
                urgency=action_vars.action_urgency,
                risk_tolerance=action_vars.risk_tolerance,
                autonomy_bias=action_vars.autonomy_bias,
                clarification_threshold=action_vars.clarification_threshold,
                persistence_drive=action_vars.persistence_drive,
            )
            decision = make_tool_decision(
                intent,
                action_vars,
                capability.category,
                trust,
                agency_decision=agency_decision,
                autonomy_level=autonomy_level,
            )
            if decision.decision == "execute":
                tools.append(self._to_langchain_tool(capability.name))
        return tools

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
        fields["_unused"] = (str | None, Field(None, description="unused"))
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
