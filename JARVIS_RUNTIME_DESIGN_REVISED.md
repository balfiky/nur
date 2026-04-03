# Jarvis Runtime — Revised Implementation Spec

> **Status:** Pre-implementation, implementation-ready
> **Purpose:** Build a purpose-built runtime around Project Nūr without fighting a framework and without assuming APIs Nūr does not currently expose.

## 1. Goal

Jarvis Runtime is the transport and lifecycle layer around Nūr.

It is responsible for:
- receiving messages from channels
- isolating sessions per user
- routing model calls to local backends
- persisting hot state across restarts
- exposing runtime-aware debug inspection
- shutting down cleanly

Nūr remains the brain.

## 2. Correct Scope

This runtime is **not** a new cognitive framework. It must not replace or duplicate:
- emotional processing
- memory logic
- profile logic
- inner dialogue
- defense logic
- response generation

It only wraps Nūr and gives it a body.

## 3. Reality Check: Current Nūr Constraints

The runtime must be designed around the code that exists today.

Current Nūr facts:
- `CognitivePipeline.process()` is synchronous
- `CognitivePipeline.end_session()` is synchronous
- current LLM protocol is `generate(system_prompt, user_message) -> str`
- current debug API is single-pipeline and not session-aware
- self-profile currently shares the same `ProfileStore` as person data inside one pipeline
- unresolved items are currently in-memory only
- `EmotionalEngine` has `snapshot()` but no `restore()`

These constraints define phase 0.

## 4. Required Phase 0 Before Runtime Work

These are mandatory embedding changes inside Nūr.

### 4.1 Add state restore

Add one of:
- `EmotionalEngine.restore(snapshot: dict, saved_at: str | None = None)`
- or `CognitivePipeline.restore_state(snapshot: dict, saved_at: str | None = None)`

Behavior:
- restore modulators from snapshot
- if `saved_at` exists, decay forward by elapsed wall-clock time before use

### 4.2 Add pipeline cleanup

Add `CognitivePipeline.close()` that closes:
- long-term memory store
- profile store
- person profile DB connection
- topic profile DB connection

### 4.3 Split shared vs per-user storage

Refactor pipeline construction so:
- per-user storage holds:
  - long-term memory
  - person profiles
  - topic profiles
- shared storage holds:
  - self-profile observations
  - defense history

Without this, “one Jarvis personality across all users” is false.

### 4.4 Keep Nūr synchronous in phase 1

Do not async-refactor the brain yet.

The runtime must call Nūr in worker threads, but not on the event loop.

Current implementation:
- `SessionManager` owns a dedicated `ThreadPoolExecutor`
- `UserSession` serializes turns and submits synchronous pipeline work through that executor
- `end_session`, `rest`, and proactive calls use the same executor path

## 5. Architecture

```text
Channels (Telegram / Console / later WhatsApp)
        │
        ▼
JarvisApp
        │
        ▼
SessionManager
        │
        ▼
UserSession(user_id)
- pipeline
- serialized turn lock + bounded backlog counters
- last_activity
- idle timeout task
        │
        ▼
CognitivePipeline (Nūr)
        │
        ▼
Task-routed sync LLM backends
```

## 6. Core Design Rules

### 6.0 Identity and routing invariants

The runtime must distinguish between **relationship identity** and **session identity**.

- Relationship state key: `platform:user_id`
- Session state key: `platform:user_id:chat_id`

Implication:
- the same human can have one accumulated relationship with Jarvis
- but distinct active conversational sessions across DM vs group contexts

Default group-chat interpretation:
- relationship state is tied to the human sender
- session state is tied to the chat context

### 6.1 Per-user processing must be serialized

Messages for the same user must never run concurrently.

Reason:
- emotional state is order-dependent
- current SQLite-backed components are not designed for concurrent semantic mutation
- a user session is logically a single-threaded mindstream

Use either:
- `asyncio.Lock` per user
- or a mailbox queue per user

Current runtime: serialized async lock plus bounded backlog accounting.

### 6.1.1 Dedupe is mandatory

Channel adapters must reject duplicate deliveries before they reach Nūr.

Minimum requirement:
- maintain a per-channel TTL cache of recently processed platform message/update IDs
- store:
  - platform message/update ID
  - received timestamp
- ignore duplicates inside the TTL window

Reason:
- duplicate processing would double-apply emotional updates, trust changes,
  memory writes, and self-observations

### 6.2 Channels stay async

Channels should remain async because:
- Telegram polling/webhooks are async-friendly
- typing indicators need async timers
- multiple users can be active at once

### 6.3 Nūr runs off the event loop

All pipeline calls must run in worker threads, off the event loop.

That keeps:
- typing indicators alive
- channel listeners responsive
- debug server responsive

Current runtime uses a shared `ThreadPoolExecutor` instead of the event loop's
default executor so shutdown remains deterministic under the conda test stack.

### 6.4 Backpressure is explicit

The runtime must define queue and session limits up front.

Default limits:
- `max_queue_per_user = 3`
- `max_active_sessions = 10`

Default policy:
- if a user's queue is full, reject the newest incoming message with a short
  busy response
- do not allow unbounded queue growth
- if active session capacity is full, reject creation of new sessions until
  capacity frees up

Reason:
- one spammy user must not create unbounded latency or memory growth

## 7. Storage Model

## Per-user

`data/{platform}_{user_id}/`
- `nur.db`
- `sessions/{chat_id}.json`

`nur.db` contains:
- long-term memories
- person profile data
- topic profile data
- any other per-user relational state

Each session-state JSON contains:
- modulator snapshot
- saved timestamp
- optional context metadata

## Shared

`data/shared/`
- `self_model.db`

`self_model.db` contains:
- self observations
- self trait extraction cache if kept
- defense history
- any future global Jarvis identity state

Shared self-model write invariant:
- all writes to the shared self-model happen only inside serialized per-user
  worker execution
- SQLite must run in WAL mode with a busy timeout and retry policy

At expected scale, this is sufficient. A separate self-model service is not
required in phase 1.

## Not persisted in phase 1 unless implemented explicitly

- unresolved item structures

If unresolved items stay in-memory only, the spec must say so plainly.

## 8. Session Lifecycle

### Session creation

When first message arrives for a user:
1. create user directory if missing
2. create pipeline with per-user DB paths + shared self DB path
3. load session-specific engine state if present
4. if missing, optionally fall back once to a legacy per-user `engine_state.json`
5. restore state with elapsed decay
6. start/reset idle timer

### Message handling

For each message:
1. enqueue into user session
2. acquire serialization boundary
3. start typing indicator
4. run pipeline in worker thread
5. send response
6. update `last_activity`
7. reset idle timer

### Session timeout

On inactivity timeout:
1. run `end_session()` in worker thread
2. save engine state
3. close pipeline
4. evict session from memory

This must be timer-driven, not “checked on next message”.

### Shutdown

On SIGINT/SIGTERM:
1. stop channels from receiving new input
2. drain active user queues up to timeout
3. run `end_session()` for active sessions
4. save engine state
5. close pipelines
6. close LLM backends
7. exit

## 9. LLM Backend Design

Phase 1 should use a **sync** backend adapter that matches current Nūr.

### Interface

```python
class SyncLLMBackend:
    def generate(self, system_prompt: str, user_message: str) -> str:
        ...
```

### Routing

Use a small router wrapper:
- primary backend for:
  - fast path
  - revision
  - arbiter
  - generator
  - digestion
- lightweight backend for:
  - slow path
  - self-check

If only one backend is configured, use it for both.

Do not introduce a separate async `complete(messages=...)` protocol in phase 1.

## 10. Channels

### Phase 1

- Console
- Telegram

### Telegram requirements

- allowlist by numeric user ID
- text messages only in phase 1
- one-shot typing indicators resent periodically
- commands:
  - `/status`
  - `/reset`
  - `/debug`

### Command semantics

- `/status`: show current modulators
- `/reset`: digest current session, persist state, remove active session from memory
- `/debug`: return local debug URL

Optional later:
- `/wipe`: delete all persisted per-user state

## 11. Debug Interface

The existing debug server must not remain single-pipeline.

Replace it with runtime-aware inspection:
- `GET /sessions`
- `GET /sessions/{user_id}/debug`
- `POST /sessions/{user_id}/reset`
- optional websocket stream for selected user

The debug server must read live sessions from `SessionManager`.

## 12. Minimal Project Structure

```text
runtime/
  app.py
  config.py
  channels/
    base.py
    console.py
    telegram.py
  sessions/
    manager.py
    user_session.py
    persistence.py
  llm/
    backend.py
    router.py
  debug/
    api.py
main.py
runtime_config.yaml
```

## 13. Build Plan

### Phase 0

Nūr embedding changes:
- restore API
- close API
- shared self-store split
- schema version support for per-user and shared DBs

### Phase 1

Console runtime:
- session manager
- per-user serialization
- worker-thread pipeline execution
- state persistence

### Phase 2

Telegram:
- polling
- allowlist
- typing indicators
- commands

### Phase 3

Timeouts and shutdown:
- real inactivity timers
- graceful drain
- save/restore

### Phase 4

Runtime-aware debug API

### Phase 5

Optional:
- WhatsApp
- health endpoint
- voice/image support
- vector retrieval

## 14. Testing Strategy

### Runtime unit tests

- channel message normalization
- allowlist rejection
- state save/load
- session creation/eviction
- per-user serialization
- backend routing

### Runtime integration tests

- console end-to-end
- two-user isolation
- restart restore
- timeout digestion
- shutdown persistence

### Regression rule

All existing Nūr tests must still pass.

### Persistence invariants to test

- duplicate channel deliveries are ignored
- DM and group-chat contexts do not collapse into one session
- shared self-model survives writes from multiple active user sessions
- startup fails loudly on unknown future schema versions

## 15. Schema Versioning

Both per-user and shared databases must carry explicit schema versions.

Required:
- `schema_version` table in `nur.db`
- `schema_version` table in `self_model.db`
- startup migration check before runtime begins serving messages

Rules:
- if schema is older and a known migration exists, migrate
- if schema is newer than the runtime understands, fail loudly and stop
- do not continue silently on unknown future schemas

## 16. Acceptance Criteria

The runtime is complete when:
1. console input goes through runtime into Nūr and returns a response
2. Telegram messages work with typing indicators
3. two users maintain different emotional states
4. one shared self-profile is visible across users
5. restart restores modulators with elapsed decay applied
6. inactivity timeout digests and evicts sessions without waiting for another message
7. shutdown persists active sessions cleanly
8. debug API can inspect a selected live user session
9. existing Nūr tests remain green
10. runtime tests pass independently

## 17. Non-Goals for Phase 1

Do not do these in the first runtime pass:
- async refactor of all Nūr internals
- vector memory
- multi-agent routing
- streaming token-by-token responses
- proactive outbound messages
- cross-device sync

## 18. Final Design Principle

Jarvis Runtime should be thin, but not fake-thin.

The correct thinness is:
- channels
- lifecycle
- routing
- persistence
- observability

Not:
- pretending current Nūr APIs already support shared identity, async processing, or restart restoration.
