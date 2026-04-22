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

### API (recommended)

`DELETE /v1/users/{platform}/{user_id}` wipes all persisted data for a
user on a given platform in one call:

- Evicts every live session for that `platform:user_id` (drain,
  digest, close) before touching disk.
- Deletes the per-user SQLite DB (`nur.db`) and every session JSON.
- Removes the per-user directory itself.
- **Does not touch** `data/shared/self_model.db` — the shared self-model
  holds only rows for entity `__self__` and a defense log with no
  per-user column, so it is preserved by design.

```bash
# With auth disabled (api_key empty):
curl -X DELETE http://localhost:8000/v1/users/web/alice

# With auth enabled:
curl -X DELETE http://localhost:8000/v1/users/web/alice \
  -H "Authorization: Bearer my-secret-token"
```

Python client:

```python
from interface.client import NurClient

with NurClient("http://localhost:8000", api_key="my-secret-token") as nur:
    result = nur.delete_user("alice", platform="web")
    print(result)
    # {
    #   "deleted": true,
    #   "rel_key": "web:alice",
    #   "rows_deleted": {"memories": 3, "relationship_events": 1, ...},
    #   "session_files_removed": 2,
    #   "sessions_evicted": ["web:alice:default"],
    #   "path_removed": "data/web_alice",
    #   "shared_self_model_db_preserved": true
    # }
```

Returns `404` if no data exists for that `platform:user_id`. Row counts
are best-effort: if a count query fails, that table reports `null` but
the wipe still proceeds.

### Manual (if the service is stopped)

```
rm -rf data/<platform>_<user_id>
```

This removes `nur.db` and all session JSON in one step.

If you want a full reset of the assistant's self-model (e.g., before
handing the system to a different operator), also delete
`data/shared/self_model.db`.

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
