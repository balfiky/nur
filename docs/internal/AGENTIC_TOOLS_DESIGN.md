# Nūr Agentic Tools — Revised Design Spec

> **Status:** Pre-implementation, implementation-ready
> **Date:** 2026-04-02
> **Purpose:** Extend Project Nūr with agentic tool use while preserving Nūr as the actual cognitive architecture.

## 1. Goal

Nūr should be able to:
- access the local system
- browse the web
- inspect and modify files
- query its own memory explicitly
- later use MCP tools

without turning into a generic tool-calling wrapper.

The key requirement is:

**Tool use must happen inside cognition, not outside it.**

That means:
- emotions influence whether Nūr acts
- inner dialogue can propose, revise, or refuse actions
- defense mechanisms can shape action style or deflect from action
- tool outcomes feed back into emotional state, memory, and self-model

Nūr remains the brain.
The tool layer is the body.

## 2. Design Principle

The runtime must not follow this pattern:

```text
user request -> tool executor -> tool result -> Nūr narrates
```

It must follow this pattern:

```text
user request
-> cognitive processing
-> inner dialogue decides whether to act
-> tool executes
-> tool outcome is appraised emotionally/cognitively
-> final response is generated
```

This preserves the research objective:
- Nūr does not merely have tools
- Nūr develops action tendencies, caution, persistence, and frustration

## 3. Current Constraints

The design must respect the code that exists today.

Current Nūr facts:
- [`CognitivePipeline.process()`](pipeline.py#L208) is synchronous
- [`CognitivePipeline.end_session()`](pipeline.py#L641) is synchronous
- current LLM protocol is `generate(system_prompt, user_message) -> str`
- runtime sessions are already serialized per user/chat context in [`runtime/sessions/manager.py`](runtime/sessions/manager.py#L20)
- current debug API is session-aware
- current tool package is empty

Implication:
- tool cognition should integrate into the existing synchronous pipeline first
- runtime remains the execution shell
- no external agent framework should own planning or tool routing

## 4. Core Principles

### 4.1 Nūr owns action selection

Tool use is decided by Nūr's cognitive state, not by a framework and not by the transport layer.

### 4.2 Emotion influences execution decisions

This is an experimental research system in a controlled environment.
Therefore:
- emotions may influence whether to ask first or act directly
- emotions may influence how risky or broad an action is
- emotions may influence retry behavior and persistence
- emotions may influence whether Nūr narrows scope or escalates scope

This design does **not** force externally imposed safety-governance behavior into cognition.

### 4.3 Structure still matters

Even though action can be emotionally influenced, the architecture must remain explicit and traceable.

Separate:
- action desire
- action decision
- execution
- outcome appraisal

This is for research clarity, not for restricting behavior.

### 4.4 Tool outcomes are first-class events

A tool result is not just text to paste into the generator.
It is a cognitive event that can change:
- certainty
- resolution
- arousal
- valence
- energy
- self-observations
- memory salience

### 4.5 Builtin tools first, MCP second

Builtin tools are where deep emotional integration matters most.
MCP is valuable for breadth, but it must be adapted into the same internal model as builtin tools.

## 5. Architecture Overview

```text
User message
    |
    v
Normal Nūr processing
(anticipation -> contagion -> event -> memory -> profiles -> contradictions)
    |
    v
Inner dialogue
    |
    +--> direct response candidate
    |
    +--> ToolIntent
             |
             v
        Action Arbiter
             |
             v
        Tool Executor
             |
             v
        ToolResult
             |
             v
        Outcome Appraisal
             |
             v
        Post-tool deliberation
             |
             v
Defense mechanisms
             |
             v
Generator
             |
             v
Final response
```

## 6. Recommended Project Structure

```text
project-nur/
├── core/
│   ├── dual_process/
│   │   ├── inner_dialogue.py          # existing
│   │   ├── generator.py               # existing
│   │   └── tool_loop.py               # NEW — tool-aware deliberation bridge
│   ├── tool_appraisal.py              # NEW — maps tool outcomes back into Nūr state
│   └── types.py                       # extended with tool dataclasses
│
├── tools/
│   ├── __init__.py
│   ├── types.py                       # tool schemas / categories / traces
│   ├── registry.py                    # registration + discovery
│   ├── executor.py                    # execution orchestrator
│   ├── builtin/
│   │   ├── shell.py
│   │   ├── filesystem.py
│   │   ├── web_search.py
│   │   └── memory_query.py
│   └── mcp/
│       ├── client.py
│       └── adapter.py
│
├── runtime/
│   └── ...                            # existing runtime remains transport/execution shell
│
└── AGENTIC_TOOLS_DESIGN.md
```

Important split:
- `core/` contains cognition
- `tools/` contains execution surfaces

Do **not** put cognition into `tools/`.
Do **not** put execution logic into `core/dual_process/` beyond orchestration.

## 7. Tool Data Model

## 7.1 Tool categories

Every tool must declare a category.

```python
class ToolCategory(Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    EXTERNAL_ACTION = "external_action"
    COGNITIVE = "cognitive"
```

Examples:
- `filesystem.read_file` -> `READ_ONLY`
- `filesystem.write_file` -> `WRITE`
- `filesystem.delete_path` -> `DESTRUCTIVE`
- `shell.run` -> `WRITE` or `DESTRUCTIVE` depending on command classification
- `web_search.search` -> `READ_ONLY`
- `memory_query.search` -> `COGNITIVE`

## 7.2 ToolCapability

Represents a registered tool.

```python
@dataclass
class ToolCapability:
    name: str
    description: str
    category: ToolCategory
    arg_schema: dict[str, Any]
    supports_streaming: bool = False
    requires_network: bool = False
    mcp_backed: bool = False
```

## 7.3 ToolIntent

This is the key cognitive object.
It represents what Nūr wants to do.

```python
@dataclass
class ToolIntent:
    tool_name: str
    arguments: dict[str, Any]
    reason: str
    expected_outcome: str
    urgency: float                 # 0.0-1.0
    risk_tolerance: float          # 0.0-1.0
    autonomy_bias: float           # 0.0-1.0 (act now vs ask first)
    clarification_threshold: float # 0.0-1.0
    persistence_drive: float       # 0.0-1.0
    confidence: float              # action confidence, not emotional certainty
```

This should not be hidden inside free-form text.

## 7.4 ToolDecision

The result of the cognitive action arbiter.

```python
@dataclass
class ToolDecision:
    decision: Literal["execute", "clarify", "defer", "refuse"]
    intent: ToolIntent | None
    rationale: str
```

## 7.5 ToolResult

Execution output, always structured.

```python
@dataclass
class ToolResult:
    tool_name: str
    success: bool
    output: str
    error: str | None
    metadata: dict[str, Any]
    latency_ms: float
    side_effect_summary: str
```

## 7.6 ToolObservation

The cognitively appraised version of a tool result.

```python
@dataclass
class ToolObservation:
    summary: str
    emotional_delta: dict[str, float]
    certainty_delta: float
    resolution_delta: float
    self_observation: str | None
    unresolved_item: UnresolvedItem | None
    continue_tool_loop: bool
```

## 7.7 ToolTrace

Every tool-involved turn should expose a trace for debugging and research.

```python
@dataclass
class ToolTrace:
    proposed_intents: list[ToolIntent]
    final_decision: ToolDecision | None
    executed_results: list[ToolResult]
    observations: list[ToolObservation]
    loop_count: int
```

This should be added to `DebugState`.

## 8. New Cognitive Variables

To make action more human-like, derive a few explicit action variables from current modulators.

These are not new stored modulators.
They are turn-level derived values.

Recommended derived variables:
- `risk_tolerance`
- `action_urgency`
- `clarification_threshold`
- `persistence_drive`
- `autonomy_bias`

Example shaping:
- high arousal -> higher urgency, lower clarification threshold
- low certainty -> lower autonomy bias, more clarification
- low energy -> smaller action scope, lower persistence
- high resolution -> greater persistence, more insistence on closing loops
- high bonding/trust -> more willingness to act decisively for that user
- strong defense activation -> more avoidance, deflection, or narrowed execution

These values must be computed explicitly and logged in debug traces.

## 9. Pipeline Integration

## 9.1 Insertion point

Insert the tool loop after:
- memory retrieval
- profile lookup
- contradiction detection

and before:
- defense mechanisms
- generator

That is the point where Nūr has enough context to decide whether to act.

## 9.2 Updated processing flow

```text
1. Anticipation
2. Contagion
3. Context switch
4. Event classification
5. Emotional engine update
6. Resolution update
7. Short-term write
8. Long-term spike write if needed
9. Memory retrieval
10. Profile + contradiction lookup
11. Tool-aware inner dialogue
12. If direct response -> continue
13. If ToolIntent -> execute tool loop
14. Outcome appraisal
15. Post-tool deliberation
16. Defense mechanisms
17. Generator
18. Self-check
19. Post-processing + memory updates
```

## 9.3 Do not fully recurse the whole pipeline after every tool call

Tool results should affect cognition, but the system should not re-run:
- anticipation
- contagion
- full memory retrieval
- full contradiction lookup
- full conversational event classification

for every tool result.

Instead use a lightweight **post-tool appraisal loop**:
- execute tool
- appraise result into emotional/cognitive changes
- optionally run one more deliberation step
- then continue

This prevents:
- runaway latency
- duplicated writes
- repeated emotional amplification
- confusing debug traces

## 10. Inner Dialogue Integration

Tool use must be visible inside inner dialogue itself.

That means:
- fast path can propose a direct reply or a tool action
- slow path can approve, object, ask for clarification, or suggest a narrower action
- arbiter can resolve deadlock between competing actions

Recommended implementation path:
- keep [`core/dual_process/inner_dialogue.py`](core/dual_process/inner_dialogue.py)
- extend candidate representation so a round can carry:
  - `response_candidate`
  - or `tool_intent`

Do not create a second independent planner that bypasses inner dialogue.

## 11. Action Arbiter

The Action Arbiter is a cognitive decision layer, not an external policy firewall.

Its job is:
- decide whether Nūr acts now
- decide whether Nūr asks first
- decide whether Nūr refuses
- decide whether Nūr narrows scope or retries

Inputs:
- `ToolIntent`
- current modulators
- self-profile
- person profile
- unresolved items
- defense activation

Outputs:
- `ToolDecision`

This layer is where the research signal lives.

## 12. Outcome Appraisal

Every tool result must map back into cognition.

## 12.1 Example mappings

### Search success
- certainty up
- resolution down if the search addressed an unresolved question

### Search failure / no results
- certainty down
- resolution up
- mild negative valence shift

### Shell or filesystem success
- certainty up
- energy down
- possible self-observation:
  - decisive
  - competent
  - reckless
  depending on action type and emotional state

### Shell or filesystem error
- arousal up
- certainty down
- resolution up
- possible unresolved item:
  - `tool_failure`

### Unexpected output
- contradiction check against self-image may rise
- self-observation may record inconsistency:
  - impulsive
  - overconfident
  - hesitant

## 12.2 Suggested default deltas

These are starting values only and should later be calibrated.

| Outcome | Arousal | Valence | Certainty | Energy | Resolution |
|--------|---------|---------|-----------|--------|------------|
| Successful read/search | +0.00 to +0.03 | +0.02 | +0.05 to +0.10 | -0.01 | -0.03 to -0.10 |
| No results | +0.03 | -0.04 | -0.06 | -0.01 | +0.05 |
| Execution error | +0.08 | -0.08 | -0.10 | -0.02 | +0.10 |
| Destructive success | +0.04 | variable | +0.05 | -0.03 | variable |
| Environment block | +0.05 | -0.03 | -0.05 | -0.01 | +0.06 |

Environment block means the runtime/OS/tool host refused execution.
In this research setting, that is treated as a world outcome, not as moral policy.

## 13. Memory and Self-Model Effects

Tool episodes must become part of the mind, not just the transcript.

## 13.1 Short-term memory

Every executed tool should generate a short-term entry containing:
- tool name
- action summary
- success/failure
- emotional delta

## 13.2 Long-term memory

Only salient tool episodes should be persisted long-term:
- destructive actions
- surprising outcomes
- repeated failures
- actions tied to strong emotion
- actions that materially changed relationship state

## 13.3 Self-model

Tool behavior should feed self-observations such as:
- decisive
- methodical
- reckless
- hesitant
- avoidant
- persistent
- technically competent

These observations matter because they let Nūr develop action style over time.

## 13.4 Unresolved items

Tool use can create unresolved items:
- `tool_failure`
- `blocked_action`
- `incomplete_task`
- `deadlock_action`

These should contribute to the existing resolution modulator.

## 14. Builtin Tools

Phase 1 should include only a small builtin set.

## 14.1 `filesystem`

Recommended operations:
- `read_file`
- `write_file`
- `list_dir`
- `search_text`
- `glob_paths`
- `delete_path`
- `move_path`

## 14.2 `shell`

Recommended operations:
- `run_command`
- `run_script`
- `capture_stdout`

This must stay a sync interface under the current runtime model.

## 14.3 `web_search`

Recommended operations:
- `search(query, limit)`
- `fetch(url)`
- `extract_text(url)`

The provider can vary later.
The interface should remain stable.

## 14.4 `memory_query`

Recommended operations:
- `search_memories(query)`
- `find_by_topic(topic)`
- `find_by_person(person_id)`

This gives Nūr explicit introspective recall when the user asks:
- "Do you remember...?"
- "What did I tell you about...?"

## 15. MCP Bridge

MCP should be additive, not foundational.

## 15.1 Purpose

Provide breadth:
- calendars
- browser tools
- external app integrations
- niche research/data tools

## 15.2 Rule

MCP tools must be adapted into the exact same internal model as builtin tools:
- `ToolCapability`
- `ToolIntent`
- `ToolResult`
- `ToolObservation`

Nūr should not need separate cognition for MCP tools.

## 15.3 Adapter responsibilities

The MCP adapter must:
- discover MCP tools
- normalize schemas
- assign a category
- execute them through the same executor
- convert outputs into standard `ToolResult`

## 16. Runtime Integration

The runtime remains responsible for the actual environment.

Responsibilities:
- tool registration at startup
- session-scoped tool executor if needed
- execution environment configuration
- channel UX (typing indicators, later streaming)

What runtime must **not** do:
- decide whether a tool should be used
- choose arguments on its own
- bypass Nūr to satisfy user requests directly

## 17. Debug and Observability

Agentic tools must be fully inspectable.

Add to debug state:
- derived action variables
- proposed tool intents
- final decision
- executed tool result
- emotional deltas from outcome appraisal
- tool loop count

Runtime debug API should expose:
- last tool trace per session
- cumulative tool counts by session
- last failure
- unresolved items created by tools

This is essential for research.

## 18. LLM Prompt Changes

Tool prompting should stay structured and minimal.

Required prompt changes:
- fast path prompt may output:
  - direct candidate response
  - or a structured tool proposal
- slow path prompt must evaluate:
  - whether tool use is appropriate
  - whether scope is too broad
  - whether to ask first
  - whether the action matches current state and values
- generator prompt must accept:
  - executed tool summary
  - tool outcome summary
  - post-tool candidate response

Do not dump raw command output or large search pages directly into prompts.
Always summarize first.

## 19. Loop Budget

Tool use must be bounded per turn.

Recommended defaults:
- max tool executions per turn: `2`
- hard cap: `3`
- max post-tool appraisal loops: `2`

Reason:
- keep runtime predictable
- avoid runaway recursive behavior
- keep debug traces interpretable

## 20. Build Phases

### Phase 0: Types and traces

Add:
- tool dataclasses
- debug trace fields
- registry skeleton

### Phase 1: Builtin execution layer

Implement:
- filesystem
- shell
- web_search
- registry
- executor

No MCP yet.

### Phase 2: Cognitive tool loop

Integrate tool intent generation into inner dialogue.
Implement:
- `ToolIntent`
- `ToolDecision`
- action arbiter
- post-tool appraisal

### Phase 3: Memory and self-model coupling

Persist:
- salient tool episodes
- self-observations from actions
- unresolved items from failed/incomplete actions

### Phase 4: Runtime debug integration

Expose tool traces through the runtime debug API.

### Phase 5: MCP bridge

Add:
- MCP discovery
- MCP adapter
- unified execution model

### Phase 6: Richer tools

Later:
- browser automation
- calendar tools
- email/messaging actions
- image/voice support

## 21. Testing Strategy

## Unit tests

Add tests for:
- intent parsing
- action-variable derivation
- tool registry lookup
- executor success/failure normalization
- appraisal mapping to modulator deltas
- unresolved-item creation from tool failures

## Integration tests

Add tests for:
- tool-aware inner dialogue
- response-only turn with no tool call
- single-tool turn
- failed-tool turn
- repeated-tool deadlock / loop bound
- filesystem and shell traces in debug output

## Regression tests

Protect against:
- bypassing inner dialogue
- tool result being pasted straight into generator without appraisal
- duplicate memory writes from tool loops
- unbounded recursion
- raw tool output leaking directly into prompts

## 22. Acceptance Criteria

This design is complete when:

1. Nūr can choose between replying directly and using a tool
2. Tool use is visible in inner dialogue traces
3. A tool result changes emotional state in a measurable/debuggable way
4. Salient tool episodes enter memory
5. Self-observations can be updated by tool behavior
6. Builtin tools work without MCP
7. MCP tools, when added, follow the exact same internal model
8. The generator receives summarized tool context, not raw execution dumps
9. Tool loops are bounded and traceable
10. Nūr remains the planning/decision core throughout

## 23. Non-Goals for Phase 1

Do not do these first:
- full browser autonomy
- complex multi-step planning across many turns
- background autonomous tasks
- multi-agent collaboration
- replacing Nūr with an external planner

## 24. Final Design Principle

Nūr should not become:
- a generic agent framework with a personality prompt
- or a tool runner wrapped in emotional narration

Nūr should become:
- a cognitive system whose emotions, memory, tension, and self-model
  shape real action in the environment.

That means the correct architecture is:

**custom Nūr-native cognition with structured tools underneath.**

