# Project Nur Migration Brief

## Mission

Build the next product as a fork of OpenClaw while preserving the core idea of
Nur: an emotional, independent AI with relational continuity.

This repository is the reference implementation for Nur's cognitive behavior.
The production target should live in a separate OpenClaw-based directory.

## Core Principle

There must be exactly one agent mind.

OpenClaw is the host runtime and world-facing body.
Nur is the cognitive core and identity.

Do not build a system where OpenClaw and Nur both make persona, planning,
memory, or tool-decision choices. That creates two competing minds.

## Ownership Boundary

### OpenClaw Owns

- model/provider selection
- API keys and runtime configuration
- Telegram and other channel integrations
- tool execution
- MCP, browser, plugin, and external capability wiring
- operational runtime concerns

### Nur Owns

- emotional state
- appraisal
- relational memory
- self-model
- tool-use policy and approval
- behavioral guidance for the final response
- post-tool emotional interpretation

## Non-Negotiable Rules

- Keep OpenClaw as the only runtime host.
- Keep OpenClaw defaults for configuration patterns whenever possible.
- Do not create a second runtime, second config system, or second memory system.
- Do not layer OpenClaw's default persona, planner, or memory on top of Nur.
- Do not let OpenClaw silently become the decision-maker for the final agent.
- Tool execution may happen in OpenClaw, but tool policy must come from Nur.
- The final user-facing behavior must be shaped by Nur's cognitive context.

## Migration Strategy

### Goal

Port Nur into OpenClaw as a cognition module, not as a parallel application.

### Recommended Shape

1. Fork OpenClaw into a new directory.
2. Keep this repo as the behavior oracle and source reference.
3. Create a new `nur_core/` package inside the OpenClaw fork.
4. Replace or bypass OpenClaw's default agent cognition with `NurAgent`.
5. Preserve OpenClaw runtime ownership for channels, tools, and model config.

## What To Port From This Repo

Primary cognition sources:

- `pipeline.py`
- `core/emotional_engine.py`
- `core/appraisal.py`
- `core/dual_process/tool_loop.py`
- `core/memory/relationship.py`
- `core/memory/long_term.py`
- `core/memory/short_term.py`
- `core/profiles/self_model.py`
- `core/profiles/person.py`
- `core/profiles/topic.py`
- `core/profiles/contradiction.py`
- `core/dual_process/generator.py`
- `core/strategy.py`
- `core/defense_mechanisms.py`
- `config/prompts/*.md`

These are reference modules, not a drop-in host runtime.

## What Not To Port As Runtime Ownership

Leave these concerns to OpenClaw:

- `runtime/app.py`
- `runtime/channels/telegram.py`
- `runtime/llm/backend.py`
- `interface/api.py`
- local runtime bootstrapping and host orchestration

They may remain useful as reference code, but should not remain the production
runtime path in the OpenClaw-based system.

## Target Architecture

### Host Layer

OpenClaw provides:

- inbound user events
- session identity
- configured model access
- tool execution
- channel delivery
- operational lifecycle

### Cognitive Layer

`nur_core` provides:

- state update on each turn
- emotional and relational reasoning
- memory retrieval and update
- tool policy and approval
- prompt context / behavioral guidance
- post-tool and post-response state updates

### Required Host Interfaces

Implement these abstractions inside the OpenClaw fork:

- `HostModel`
- `HostTools`
- `HostStateStore`
- `HostIdentity`
- `HostClock`

Nur should depend on these interfaces rather than directly on OpenClaw internals
where practical.

## Recommended Implementation Phases

### Phase 0: Discovery

- inspect OpenClaw's agent entrypoint
- inspect prompt construction path
- inspect memory path
- inspect planning and tool-decision flow
- inspect tool execution path
- inspect model/provider selection path
- inspect session and identity flow
- write a short ADR before major edits

### Phase 1: Skeleton

- create `nur_core/`
- add host interfaces
- add `NurAgent` skeleton
- wire `NurAgent` into the OpenClaw agent path
- bypass OpenClaw default persona, memory, and planning for this path
- add tests proving a single decision-making path

### Phase 2: Emotional Core Port

- port emotional engine
- port appraisal
- port response guidance and prompt-context builder
- port tool policy and result appraisal
- keep model execution owned by OpenClaw

### Phase 3: Persistence and Continuity

- port relational memory
- port self-model persistence
- map OpenClaw session identity into Nur state
- add continuity and regression tests

### Phase 4: Cleanup

- remove duplicated cognition paths
- simplify wiring
- document the new host/core boundary
- keep parity tests

## Working Rules For Claude Code

- Start by reading the host architecture before editing.
- Explain the integration options before choosing one.
- Prefer minimal, surgical changes over broad rewrites.
- Preserve OpenClaw config conventions unless there is a hard blocker.
- Never allow both OpenClaw and Nur to own persona or planning at the same time.
- Keep all changes typed, testable, and production-oriented.
- Add focused tests for single-mind correctness on every major step.
- Treat this repo as the behavioral reference during migration.

## Acceptance Criteria

A change is only correct if all of the following are true:

- there is one decision-making path
- OpenClaw remains the only runtime host
- Nur remains the only cognitive identity
- OpenClaw still owns channels, tools, and model/provider configuration
- Nur still owns emotional state, memory, and tool policy
- tests prove the boundary is enforced

## Model Guidance For Claude Code

Use the stronger model for architecture-changing work.

- Opus Max:
  architecture discovery, replacement strategy, "single mind" enforcement,
  agent-path rewiring, tool-policy ownership, debugging conflicting cognition
- Opus High:
  porting `nur_core`, adapters, state-store integration, prompt-context wiring,
  parity tests, memory/session integration
- Opus Medium:
  config plumbing, DTOs, docs, cleanup, fixtures, CI, mechanical refactors

Rule of thumb:

- if the task changes who decides, use Opus Max
- if the task changes how components connect, use Opus High
- if the task is mostly mechanical, use Opus Medium

## First Claude Code Task

In the OpenClaw fork, the first task should be:

1. inspect the host architecture
2. evaluate 2-3 integration approaches
3. choose one design with explicit tradeoffs
4. write an ADR
5. implement only the `nur_core` and `NurAgent` skeleton
6. add tests proving only one mind is active

Do not attempt the full port in the first pass.
