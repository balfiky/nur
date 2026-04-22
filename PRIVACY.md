# Privacy & Data Retention

Project Nūr's entire premise is **persistent relational memory**. That means
it stores information about the people who interact with it. This document
describes what is stored, where, and how to inspect, export, or delete it.

## Storage Layout

All persistent data lives under a single configurable directory:

- `runtime_config.yaml → data_dir` (default: `./data/`)

Within `data_dir`, Nūr writes:

| Path | Contents |
|------|----------|
| `data/shared/self_model.db` | Shared self-model — observations and defense log for entity `__self__` |
| `data/<platform>_<user_id>/nur.db` | Per-user SQLite: emotional memory, relationship events, open loops, person/topic profiles, semantic memory |
| `data/<platform>_<user_id>/sessions/<chat_id>.json` | Per-session engine state: `modulator_snapshot`, `saved_at`, and `unresolved_items` |
| `data/<platform>_<user_id>/engine_state.json` | Legacy per-user engine-state snapshot (kept for backward-compatible restore) |

The entire `data/` directory is gitignored. Do not commit it.

## Tables Inside the Per-User DB (`nur.db`)

| Table | Columns of interest | Purpose |
|-------|--------------------|---------|
| `memories` | `source_person`, `topic`, `summary`, `emotional_valence`, `trust_delta`, `confidence`, `spike` | Distilled long-term emotional summaries |
| `relationship_events` | `source_person`, `event_kind` (rupture/repair/commitment/recurring_tension), `intensity`, `valence` | Social-arc events |
| `open_loops` | `source_person`, `loop_kind`, `status`, `description`, `intensity` | Unresolved tension or pending follow-up |
| `observations` | `entity_id`, `trait`, `value` | Behavioral observations for person and topic profiles |
| `extracted_traits` | `entity_id`, `trait`, `score` | Rolled-up traits derived from observations |
| `semantic_memories` | `source_person`, `kind`, `topic`, `summary`, `content` | Stated preferences, decisions, episodes, facts |

In the per-user `nur.db`, `entity_id` values are either a user id (person
profile) or a topic name (topic profile).

## Tables Inside the Shared DB (`data/shared/self_model.db`)

The runtime deliberately splits self-model storage away from per-user data
(`self_db_path = config.shared_db_path` in `runtime/sessions/manager.py`):

| Table | Columns of interest | Purpose |
|-------|--------------------|---------|
| `observations` | `entity_id` (always `__self__`), `trait`, `value` | Self-observation records |
| `extracted_traits` | `entity_id` (always `__self__`), `trait`, `score` | Rolled-up self-traits |
| `defense_events` | `timestamp`, `defense_type`, `raw_intensity`, `expressed_intensity`, `suppression_delta` | Defense-mechanism activations (self-log; no `entity_id` column) |

## What Is Actually Recorded

**Raw transcripts are not stored by default.** The `memories` table holds
*distilled emotional summaries*, not conversation logs. The `semantic_memories`
table may quote short user statements verbatim when the user expresses a
preference or fact (e.g., "I prefer concise replies") — review it before
sharing a database.

## Consent

If you deploy Nūr to talk to anyone other than yourself, **you are
responsible for obtaining their informed consent** to the persistence
described above. A minimum disclosure should state:

- Nūr remembers how conversations feel, not just what was said
- Memory persists across sessions unless explicitly wiped
- Trust and bonding build and break based on interaction history
- Users can request export or deletion (see below)

## Inspection APIs

The `/v1` API surfaces what Nūr knows about a user:

- `GET /v1/profiles/person?user_id=<id>`
- `GET /v1/profiles/self`
- `GET /v1/memory/long_term?user_id=<id>`
- `GET /v1/memory/relationship?user_id=<id>`
- `GET /v1/memory/semantic?user_id=<id>`

## Deletion

There is currently **no one-button user-data wipe**. To remove a user
identified as `<platform>:<user_id>` (e.g., `web:alice`, `telegram:42`):

1. Stop the runtime.
2. Delete the per-user directory:
   ```
   rm -rf data/<platform>_<user_id>
   ```
   This removes `nur.db` and all session JSON in one step.
3. The shared self-model (`data/shared/self_model.db`) stores only rows
   for entity `__self__` and a defense log with no user column. In normal
   operation it holds no user-identifiable data, so no per-user wipe is
   needed there. If you want a full reset of the assistant's self-model
   (e.g., before handing the system to a different operator), delete that
   file.

A first-class `DELETE /v1/user/<id>` endpoint is on the roadmap.

## Retention

There is no automatic expiration. Old memories decay in *activation*
(retrieval weighting) but remain on disk indefinitely. If you need
time-bounded retention, run a scheduled cleanup or add a retention policy
to your deployment.

## Data Leaving the Host

Nūr does not phone home. However, if you configure an external LLM backend
(MiniMax, OpenAI-compatible, etc.), **every turn sends the assembled
prompt — which includes retrieved memory, profiles, and relationship
context — to that provider**. Review the provider's data-handling policy
before enabling it for real users.

## Reporting Privacy Issues

See [SECURITY.md](SECURITY.md).
