# Character Independence Channel Audit

Date: 2026-05-03

## H0 Findings

Audit commands run:

```bash
rg -n "ingest_pasted_text|ingest_local_file|ingest_uploaded_text|rollback_batch|delete_experience|directly insert|SOURCE_TRUST|DEFAULT_MAX_DRIVE_DELTA|MAX_INFLUENCE_DELTA" interface/ runtime/ core/
rg -n "INSERT INTO (beliefs|drive_states|drive_changes|evolution_events|experience_events)" core/ runtime/ interface/
rg -n "operator_review|require_review|admin_override" core/ runtime/ interface/
```

Classifications:

- `interface/api.py` Life History text/file/upload endpoints: keep. They route through `LifeHistoryStore.ingest_*`, so the operator is participating in the Learning channel.
- `runtime/skills.py` skill enrollment/import/enable paths: keep. They are the Skills channel.
- `interface/api.py` `/admin/life/rollback`: removed as a mutation path. The endpoint now returns `410 Gone`; the admin console rollback form and JavaScript caller were removed.
- `runtime/life_history.py` `rollback_batch`: retained only as a test-gated internal helper. It raises unless `NUR_TESTING=1`.
- `runtime/life_history.py` direct inserts/updates/deletes on `experience_events`, `evolution_events`, `beliefs`, `drive_states`, and `drive_changes`: keep only inside Learning-channel ingestion, metabolic consolidation/decay/revision, or the `NUR_TESTING=1` rollback helper.
- `runtime/evolution_policy.py`: converted to perception-only helpers. Gate functions and source-trust tiers were removed.

Final audit:

- No hits for `evaluate_belief`, `evaluate_drive_change`, `evaluate_future_behavior`, `operator_review`, `require_review`, `admin_override`, `MAX_INFLUENCE_DELTA`, or `SOURCE_TRUST` in `core/`, `runtime/`, `interface/`, `nur_tools/`, or `config/`.
- Old proactive hard-cap constants were removed from runtime code.
- Admin rollback UI identifiers were removed from `interface/static`.

## Converted Tests

- Gate-blocking Life History tests now assert perception metadata, non-zero weighted influence, and domain bounds.
- Admin rollback tests now assert `410 Gone` and unchanged ledger counts.
- Proactive density/recovery tests now assert recovery-threshold shaping rather than hard suppression.
- Life influence tests now assert full pressure application with [0, 1] action-variable domain bounds.
