"""Identity-level life history and evolution storage.

This module is intentionally separate from per-user chat memory. It stores
Nūr's own accumulated experiences and the durable changes those experiences
produce in beliefs, drives, and self-observations.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Protocol

from runtime.config import RuntimeConfig
from runtime.evolution_policy import (
    evaluate_belief,
    evaluate_drive_change,
    evaluate_future_behavior,
    make_policy_context,
    source_trust,
)


class LLMBackend(Protocol):
    def generate(self, system_prompt: str, user_message: str) -> str: ...


class LifeHistoryError(ValueError):
    """Raised when life-history intake or storage cannot be completed."""


DEFAULT_DRIVES: dict[str, tuple[float, str]] = {
    "curiosity": (0.50, "Need to encounter, question, and understand more of the world."),
    "competence": (0.50, "Need to become more capable and less error-prone."),
    "autonomy": (0.50, "Need to form self-directed intentions instead of only reacting."),
    "continuity": (0.50, "Need to preserve identity across time and change."),
    "repair": (0.50, "Need to resolve rupture, tension, and unfinished loops."),
    "attachment": (0.50, "Need to maintain meaningful bonds and relational memory."),
    "caution": (0.50, "Need to avoid reckless, false, or harmful self-change."),
}

MAX_PASTED_TEXT_CHARS = 250_000
MAX_LOCAL_FILE_BYTES = 10_000_000
LOCAL_FILE_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".text"}
_WORD_RE = re.compile(r"[a-z0-9]+")


def life_history_db_path(config: RuntimeConfig) -> str:
    return os.path.join(config.data_dir, "shared", "life_history.db")


class LifeHistoryStore:
    """SQLite-backed source of truth for experiences and self-change."""

    def __init__(self, config: RuntimeConfig, db_path: str | None = None) -> None:
        self.config = config
        self.db_path = db_path or life_history_db_path(config)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        self._ensure_default_drives()

    def __enter__(self) -> LifeHistoryStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def ingest_pasted_text(
        self,
        *,
        title: str,
        text: str,
        source_type: str = "pasted_text",
        participants: list[str] | None = None,
        llm_client: LLMBackend | None = None,
    ) -> dict[str, Any]:
        title = _require_text(title, "title", max_chars=180)
        text = _require_text(text, "text", max_chars=MAX_PASTED_TEXT_CHARS)
        source_type = _safe_label(source_type or "pasted_text", fallback="pasted_text")
        return self._ingest_text(
            title=title,
            text=text,
            source_type=source_type,
            source_ref="pasted",
            participants=participants or [],
            llm_client=llm_client,
            metadata={"input_mode": "pasted_text", "char_count": len(text)},
        )

    def ingest_local_file(
        self,
        *,
        file_path: str,
        title: str = "",
        participants: list[str] | None = None,
        llm_client: LLMBackend | None = None,
    ) -> dict[str, Any]:
        path = _resolve_allowed_file(self.config, file_path)
        if path.suffix.lower() not in LOCAL_FILE_SUFFIXES:
            raise LifeHistoryError(
                "Only plain text/markdown files are supported in Experience Intake v1."
            )
        size = path.stat().st_size
        if size > MAX_LOCAL_FILE_BYTES:
            raise LifeHistoryError(
                f"Local file is too large for v1 intake ({size} bytes; max {MAX_LOCAL_FILE_BYTES})."
            )
        text = path.read_text(encoding="utf-8", errors="replace")
        resolved_title = title.strip() or path.stem.replace("_", " ").replace("-", " ")
        return self._ingest_text(
            title=resolved_title,
            text=text,
            source_type="local_file",
            source_ref=str(path),
            participants=participants or [],
            llm_client=llm_client,
            metadata={
                "input_mode": "local_file",
                "path": str(path),
                "byte_count": size,
                "char_count": len(text),
            },
        )

    def ingest_uploaded_text(
        self,
        *,
        filename: str,
        text: str,
        title: str = "",
        participants: list[str] | None = None,
        llm_client: LLMBackend | None = None,
    ) -> dict[str, Any]:
        filename = (filename or "uploaded-file").strip() or "uploaded-file"
        text = _require_text(text, "text", max_chars=MAX_LOCAL_FILE_BYTES)
        fallback_title = Path(filename).stem.replace("_", " ").replace("-", " ") or "Uploaded material"
        resolved_title = title.strip() or fallback_title
        return self._ingest_text(
            title=resolved_title,
            text=text,
            source_type="uploaded_file",
            source_ref=filename,
            participants=participants or [],
            llm_client=llm_client,
            metadata={
                "input_mode": "browser_upload",
                "filename": filename,
                "char_count": len(text),
            },
        )

    def ingest_external_text(
        self,
        *,
        title: str,
        text: str,
        source_type: str,
        source_ref: str,
        participants: list[str] | None = None,
        llm_client: LLMBackend | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Digest externally collected material while preserving its source ref.

        Browser uploads and local files already have dedicated intake paths. This
        method is for runtime learning flows where Nūr gathered material from a
        URL or direct conversation text and needs that source recorded in the
        identity-level ledger.
        """
        title = _require_text(title, "title", max_chars=180)
        text = _require_text(text, "text", max_chars=MAX_PASTED_TEXT_CHARS)
        source_type = _safe_label(source_type or "external_text")
        source_ref = _trim(str(source_ref or "external"), 900)
        return self._ingest_text(
            title=title,
            text=text,
            source_type=source_type,
            source_ref=source_ref,
            participants=participants or [],
            llm_client=llm_client,
            metadata={
                "input_mode": "external_text",
                "char_count": len(text),
                **(metadata or {}),
            },
        )

    def overview(self) -> dict[str, Any]:
        counts = {
            "experiences": self._count("experience_events"),
            "evolution_events": self._count("evolution_events"),
            "beliefs": self._count("beliefs"),
            "drives": self._count("drive_states"),
        }
        return {
            "db_path": self.db_path,
            "counts": counts,
            "snapshot": self.evolution_snapshot(),
            "recent_experiences": self.list_experiences(limit=20),
            "recent_evolution": self.list_evolution(limit=50),
            "beliefs": self.list_beliefs(limit=25),
            "drives": self.list_drives(),
        }

    def list_experiences(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM experience_events
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (_limit(limit),),
        ).fetchall()
        return [_experience_to_dict(row) for row in rows]

    def list_evolution(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM evolution_events
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (_limit(limit),),
        ).fetchall()
        return [_evolution_to_dict(row) for row in rows]

    def rollback_batch(self, batch_id: str) -> dict[str, Any]:
        """Rollback one applied Life History evolution batch.

        Experiences remain in the ledger, but durable belief/drive changes and
        evolution rows for the batch are removed or reverted. Rollback is
        intentionally conservative: if a later batch has already modified a
        belief or drive, this method raises instead of corrupting newer state.
        """
        batch_id = _require_text(batch_id, "batch_id", max_chars=80)
        if not self._conn.execute(
            "SELECT 1 FROM experience_events WHERE batch_id = ? LIMIT 1",
            (batch_id,),
        ).fetchone():
            raise LifeHistoryError(f"Batch not found: {batch_id}")

        reverted_beliefs = self._rollback_beliefs(batch_id)
        reverted_drives = self._rollback_drives(batch_id)
        deleted_evolution = self._conn.execute(
            "SELECT COUNT(*) AS c FROM evolution_events WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()["c"]
        self._conn.execute("DELETE FROM evolution_events WHERE batch_id = ?", (batch_id,))
        self._conn.execute("DELETE FROM belief_revisions WHERE batch_id = ?", (batch_id,))
        self._conn.execute("DELETE FROM drive_changes WHERE batch_id = ?", (batch_id,))
        for row in self._conn.execute(
            "SELECT id, metadata_json FROM experience_events WHERE batch_id = ?",
            (batch_id,),
        ).fetchall():
            metadata = _loads_json(row["metadata_json"], {})
            metadata["rollback"] = {"rolled_back_at": time.time()}
            self._conn.execute(
                "UPDATE experience_events SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata, sort_keys=True), row["id"]),
            )
        self._conn.commit()
        return {
            "batch_id": batch_id,
            "beliefs_reverted": reverted_beliefs,
            "drives_reverted": reverted_drives,
            "evolution_events_removed": int(deleted_evolution),
        }

    def list_beliefs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM beliefs
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (_limit(limit),),
        ).fetchall()
        return [_belief_to_dict(row) for row in rows]

    def list_drives(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM drive_states
            ORDER BY name ASC
            """
        ).fetchall()
        return [_drive_to_dict(row) for row in rows]

    def evolution_snapshot(self) -> dict[str, Any]:
        """Return a compact, operator-readable summary of character drift."""
        first_experience = self._conn.execute(
            """
            SELECT id, timestamp, source_title, source_type
            FROM experience_events
            ORDER BY timestamp ASC
            LIMIT 1
            """
        ).fetchone()
        latest_experience = self._conn.execute(
            """
            SELECT id, timestamp, source_title, source_type
            FROM experience_events
            ORDER BY timestamp DESC
            LIMIT 1
            """
        ).fetchone()
        domain_rows = self._conn.execute(
            """
            SELECT domain, COUNT(*) AS count
            FROM evolution_events
            GROUP BY domain
            ORDER BY count DESC, domain ASC
            """
        ).fetchall()

        drives = []
        for drive in self.list_drives():
            name = str(drive.get("name") or "")
            baseline, description = DEFAULT_DRIVES.get(
                name,
                (0.5, str(drive.get("description") or "")),
            )
            value = float(drive.get("value", baseline))
            drives.append({
                "name": name,
                "value": value,
                "baseline": baseline,
                "delta": value - baseline,
                "description": drive.get("description") or description,
                "updated_at": drive.get("updated_at"),
            })
        drives_by_drift = sorted(
            drives,
            key=lambda item: abs(float(item["delta"])),
            reverse=True,
        )
        dominant_drives = sorted(
            drives,
            key=lambda item: float(item["value"]),
            reverse=True,
        )

        belief_rows = self._conn.execute(
            """
            SELECT key, statement, confidence, updated_at
            FROM beliefs
            WHERE status = 'active'
            ORDER BY confidence DESC, updated_at DESC
            LIMIT 5
            """
        ).fetchall()
        latest_evolution = self.list_evolution(limit=5)

        return {
            "first_experience": _experience_ref_to_dict(first_experience),
            "latest_experience": _experience_ref_to_dict(latest_experience),
            "domain_counts": [
                {"domain": row["domain"], "count": int(row["count"])}
                for row in domain_rows
            ],
            "drive_drift": drives_by_drift,
            "dominant_drives": dominant_drives[:3],
            "strongest_beliefs": [
                {
                    "key": row["key"],
                    "statement": row["statement"],
                    "confidence": row["confidence"],
                    "updated_at": row["updated_at"],
                }
                for row in belief_rows
            ],
            "recent_changes": latest_evolution,
            "readable_summary": _snapshot_summary(
                first_experience=first_experience,
                latest_experience=latest_experience,
                domain_rows=domain_rows,
                drive_drift=drives_by_drift,
            ),
        }

    def prompt_context(
        self,
        *,
        belief_limit: int = 5,
        evolution_limit: int = 5,
        drive_limit: int = 5,
    ) -> dict[str, Any]:
        """Return the compact life-history slice safe to inject into prompts.

        This intentionally returns distilled beliefs, changed drives, and
        recent evolution events only. Raw excerpts and long experience text
        remain in the ledger/admin view, not in every chat prompt.
        """
        beliefs = [
            {
                "key": item["key"],
                "statement": item["statement"],
                "confidence": item["confidence"],
                "evidence": item["evidence"],
            }
            for item in self.list_beliefs(limit=belief_limit * 3)
            if item.get("status") == "active" and float(item.get("confidence", 0.0)) >= 0.4
        ][:belief_limit]

        changed_drives: list[dict[str, Any]] = []
        for drive in self.list_drives():
            name = str(drive.get("name") or "")
            baseline, description = DEFAULT_DRIVES.get(name, (0.5, str(drive.get("description") or "")))
            value = float(drive.get("value", baseline))
            delta = value - baseline
            if abs(delta) < 0.02:
                continue
            changed_drives.append({
                "name": name,
                "value": value,
                "baseline": baseline,
                "delta": delta,
                "description": drive.get("description") or description,
            })
        changed_drives.sort(key=lambda item: abs(float(item["delta"])), reverse=True)

        evolution = [
            {
                "domain": item["domain"],
                "subject": item["subject"],
                "after_state": item["after_state"],
                "reason": item["reason"],
                "confidence": item["confidence"],
            }
            for item in self.list_evolution(limit=evolution_limit * 3)
            if float(item.get("confidence", 0.0)) >= 0.4
        ][:evolution_limit]

        return {
            "counts": {
                "experiences": self._count("experience_events"),
                "evolution_events": self._count("evolution_events"),
                "beliefs": self._count("beliefs"),
            },
            "beliefs": beliefs,
            "drives": changed_drives[:drive_limit],
            "recent_evolution": evolution,
        }

    def _ingest_text(
        self,
        *,
        title: str,
        text: str,
        source_type: str,
        source_ref: str,
        participants: list[str],
        llm_client: LLMBackend | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        prepared_text, chunk_count = _prepare_digestion_text(text)
        digest = _digest_experience(
            title=title,
            text=prepared_text,
            source_type=source_type,
            llm_client=llm_client,
        )
        metadata = dict(metadata)
        metadata["chunk_count"] = chunk_count
        batch_id = f"life-{uuid.uuid4().hex[:12]}"
        metadata["batch_id"] = batch_id
        policy_context = make_policy_context(
            source_type=source_type,
            digest_confidence=digest["confidence"],
            source_text=prepared_text,
        )
        if policy_context.prompt_injection_markers:
            metadata["prompt_injection_markers"] = policy_context.prompt_injection_markers

        experience_id = self._insert_experience(
            title=title,
            source_type=source_type,
            source_ref=source_ref,
            participants=participants,
            summary=digest["summary"],
            raw_excerpt=_trim(text, 2000),
            salience=digest["salience"],
            emotional_valence=digest["emotional_valence"],
            emotional_impact=digest["emotional_impact"],
            confidence=digest["confidence"],
            metadata=metadata,
            batch_id=batch_id,
        )

        evolution_events: list[dict[str, Any]] = []
        policy_rejections: list[dict[str, Any]] = []
        for belief in digest["beliefs"]:
            if not isinstance(belief, dict):
                continue
            decision = evaluate_belief(belief, policy_context)
            if not decision.allowed:
                policy_rejections.append({"domain": "belief", "reason": decision.reason})
                continue
            event = self._apply_belief(experience_id, decision.item or belief, batch_id=batch_id)
            if event:
                evolution_events.append(event)
        for change in digest["drive_changes"]:
            if not isinstance(change, dict):
                continue
            name = _safe_label(str(change.get("name") or ""), fallback="")
            daily_drift = self._daily_drive_drift(name) if name else 0.0
            decision = evaluate_drive_change(change, policy_context, daily_drift=daily_drift)
            if not decision.allowed:
                policy_rejections.append({"domain": "drive", "reason": decision.reason, "subject": name})
                continue
            event = self._apply_drive_change(experience_id, decision.item or change, batch_id=batch_id)
            if event:
                evolution_events.append(event)
        for trait in digest["self_trait_changes"]:
            if not isinstance(trait, dict):
                continue
            delta = _safe_number(trait.get("delta", 0.0), 0.0)
            event = self._insert_evolution_event(
                experience_id=experience_id,
                domain="self_trait",
                subject=_safe_label(trait.get("trait") or "self_observation"),
                before_state="",
                after_state=f"{trait.get('trait', 'trait')} {delta:+.2f}",
                reason=str(trait.get("reason") or "Experience affected self-observation."),
                confidence=_safe_float(trait.get("confidence", digest["confidence"]), digest["confidence"]),
                emotional_valence=digest["emotional_valence"],
                evidence=str(trait.get("evidence") or title),
                metadata={"delta": delta},
                batch_id=batch_id,
            )
            evolution_events.append(event)
        for item in digest["future_behavior"]:
            decision = evaluate_future_behavior(str(item), policy_context)
            if not decision.allowed:
                policy_rejections.append({"domain": "worldview", "reason": decision.reason, "subject": "future_behavior"})
                continue
            event = self._insert_evolution_event(
                experience_id=experience_id,
                domain="worldview",
                subject="future_behavior",
                before_state="",
                after_state=str(decision.item),
                reason="Experience suggested a future behavioral tendency.",
                confidence=digest["confidence"],
                emotional_valence=digest["emotional_valence"],
                evidence=title,
                metadata={},
                batch_id=batch_id,
            )
            evolution_events.append(event)

        experience = self.get_experience(experience_id)
        return {
            "experience": experience,
            "evolution_events": evolution_events,
            "policy": {
                "batch_id": batch_id,
                "source_trust": policy_context.source_trust,
                "rejections": policy_rejections,
            },
            "beliefs": self.list_beliefs(limit=25),
            "drives": self.list_drives(),
        }

    def get_experience(self, experience_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM experience_events WHERE id = ?",
            (experience_id,),
        ).fetchone()
        if row is None:
            raise LifeHistoryError(f"Experience not found: {experience_id}")
        return _experience_to_dict(row)

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS life_schema_version (
                version INTEGER NOT NULL
            )
            """
        )
        if self._conn.execute("SELECT version FROM life_schema_version LIMIT 1").fetchone() is None:
            self._conn.execute("INSERT INTO life_schema_version (version) VALUES (1)")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS experience_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                source_type TEXT NOT NULL,
                source_title TEXT NOT NULL,
                source_ref TEXT NOT NULL DEFAULT '',
                participants_json TEXT NOT NULL DEFAULT '[]',
                content_summary TEXT NOT NULL DEFAULT '',
                raw_excerpt TEXT NOT NULL DEFAULT '',
                salience REAL NOT NULL DEFAULT 0.0,
                emotional_valence REAL NOT NULL DEFAULT 0.0,
                emotional_impact TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                batch_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS evolution_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                experience_id INTEGER,
                domain TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                before_state TEXT NOT NULL DEFAULT '',
                after_state TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                emotional_valence REAL NOT NULL DEFAULT 0.0,
                evidence TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                batch_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS beliefs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                statement TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                source_experience_id INTEGER,
                evidence TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS belief_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                belief_id INTEGER NOT NULL,
                experience_id INTEGER,
                before_statement TEXT NOT NULL DEFAULT '',
                after_statement TEXT NOT NULL DEFAULT '',
                before_confidence REAL NOT NULL DEFAULT 0.0,
                after_confidence REAL NOT NULL DEFAULT 0.0,
                reason TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                created_at REAL NOT NULL,
                batch_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drive_states (
                name TEXT PRIMARY KEY,
                value REAL NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drive_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drive_name TEXT NOT NULL,
                experience_id INTEGER,
                before_value REAL NOT NULL,
                after_value REAL NOT NULL,
                delta REAL NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                created_at REAL NOT NULL,
                batch_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._ensure_column("experience_events", "batch_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("evolution_events", "batch_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("belief_revisions", "before_confidence", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("belief_revisions", "after_confidence", "REAL NOT NULL DEFAULT 0.0")
        self._ensure_column("belief_revisions", "batch_id", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("drive_changes", "batch_id", "TEXT NOT NULL DEFAULT ''")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_experience_events_time ON experience_events (timestamp DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_evolution_events_time ON evolution_events (timestamp DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_evolution_events_domain ON evolution_events (domain, timestamp DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_evolution_events_batch ON evolution_events (batch_id)"
        )
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _ensure_default_drives(self) -> None:
        now = time.time()
        for name, (value, description) in DEFAULT_DRIVES.items():
            self._conn.execute(
                """
                INSERT OR IGNORE INTO drive_states (name, value, description, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, value, description, now),
            )
        self._conn.commit()

    def _daily_drive_drift(self, drive_name: str) -> float:
        if not drive_name:
            return 0.0
        start = time.time() - 86400.0
        row = self._conn.execute(
            """
            SELECT COALESCE(SUM(delta), 0.0) AS drift
            FROM drive_changes
            WHERE drive_name = ? AND created_at >= ?
            """,
            (drive_name, start),
        ).fetchone()
        return float(row["drift"] or 0.0)

    def _rollback_beliefs(self, batch_id: str) -> int:
        rows = self._conn.execute(
            """
            SELECT * FROM belief_revisions
            WHERE batch_id = ?
            ORDER BY id DESC
            """,
            (batch_id,),
        ).fetchall()
        reverted = 0
        for row in rows:
            belief = self._conn.execute(
                "SELECT * FROM beliefs WHERE id = ?",
                (row["belief_id"],),
            ).fetchone()
            if belief is None:
                continue
            if str(belief["statement"]) != str(row["after_statement"]):
                raise LifeHistoryError(
                    "Cannot rollback batch because a later belief revision exists."
                )
            if not str(row["before_statement"]):
                self._conn.execute("DELETE FROM beliefs WHERE id = ?", (row["belief_id"],))
            else:
                self._conn.execute(
                    """
                    UPDATE beliefs
                    SET statement = ?, confidence = ?, updated_at = ?,
                        source_experience_id = ?, evidence = ?
                    WHERE id = ?
                    """,
                    (
                        row["before_statement"],
                        float(row["before_confidence"]),
                        time.time(),
                        row["experience_id"],
                        "Rolled back Life History batch.",
                        row["belief_id"],
                    ),
                )
            reverted += 1
        return reverted

    def _rollback_drives(self, batch_id: str) -> int:
        rows = self._conn.execute(
            """
            SELECT * FROM drive_changes
            WHERE batch_id = ?
            ORDER BY id DESC
            """,
            (batch_id,),
        ).fetchall()
        reverted = 0
        for row in rows:
            current = self._conn.execute(
                "SELECT value FROM drive_states WHERE name = ?",
                (row["drive_name"],),
            ).fetchone()
            if current is None:
                continue
            if abs(float(current["value"]) - float(row["after_value"])) > 1e-6:
                raise LifeHistoryError(
                    "Cannot rollback batch because a later drive change exists."
                )
            self._conn.execute(
                "UPDATE drive_states SET value = ?, updated_at = ? WHERE name = ?",
                (float(row["before_value"]), time.time(), row["drive_name"]),
            )
            reverted += 1
        return reverted

    def _insert_experience(
        self,
        *,
        title: str,
        source_type: str,
        source_ref: str,
        participants: list[str],
        summary: str,
        raw_excerpt: str,
        salience: float,
        emotional_valence: float,
        emotional_impact: str,
        confidence: float,
        metadata: dict[str, Any],
        batch_id: str,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO experience_events
            (timestamp, source_type, source_title, source_ref, participants_json,
             content_summary, raw_excerpt, salience, emotional_valence,
             emotional_impact, confidence, metadata_json, batch_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                source_type,
                title,
                source_ref,
                json.dumps(participants),
                summary,
                raw_excerpt,
                _clamp(salience),
                max(-1.0, min(1.0, emotional_valence)),
                emotional_impact,
                _clamp(confidence),
                json.dumps(metadata, sort_keys=True),
                batch_id,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def _apply_belief(
        self,
        experience_id: int,
        belief: dict[str, Any],
        *,
        batch_id: str,
    ) -> dict[str, Any] | None:
        subject = _safe_label(str(belief.get("subject") or "worldview"))
        raw_statement = str(belief.get("statement") or "").strip()
        if not raw_statement:
            return None
        statement = _trim(raw_statement, 1200)
        reason = _trim(str(belief.get("reason") or "Experience shifted worldview."), 900)
        confidence = _safe_float(belief.get("confidence", 0.65), 0.65)
        key = _slug(subject)
        now = time.time()
        row = self._conn.execute("SELECT * FROM beliefs WHERE key = ?", (key,)).fetchone()
        if row is None:
            cursor = self._conn.execute(
                """
                INSERT INTO beliefs
                (key, statement, confidence, status, created_at, updated_at,
                 source_experience_id, evidence)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (key, statement, confidence, now, now, experience_id, reason),
            )
            belief_id = int(cursor.lastrowid)
            before = ""
            before_confidence = 0.0
            after_confidence = confidence
        else:
            belief_id = int(row["id"])
            before = str(row["statement"])
            before_confidence = float(row["confidence"])
            merged_confidence = max(confidence, float(row["confidence"]) * 0.9)
            after_confidence = merged_confidence
            self._conn.execute(
                """
                UPDATE beliefs
                SET statement = ?, confidence = ?, updated_at = ?,
                    source_experience_id = ?, evidence = ?
                WHERE id = ?
                """,
                (statement, merged_confidence, now, experience_id, reason, belief_id),
            )

        self._conn.execute(
            """
            INSERT INTO belief_revisions
            (belief_id, experience_id, before_statement, after_statement,
             before_confidence, after_confidence, reason, confidence, created_at,
             batch_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                belief_id,
                experience_id,
                before,
                statement,
                before_confidence,
                after_confidence,
                reason,
                confidence,
                now,
                batch_id,
            ),
        )
        event = self._insert_evolution_event(
            experience_id=experience_id,
            domain="belief",
            subject=subject,
            before_state=before,
            after_state=statement,
            reason=reason,
            confidence=confidence,
            emotional_valence=max(-1.0, min(1.0, _safe_number(belief.get("emotional_valence", 0.0), 0.0))),
            evidence=str(belief.get("evidence") or statement),
            metadata={"belief_id": belief_id, "key": key},
            batch_id=batch_id,
        )
        self._conn.commit()
        return event

    def _apply_drive_change(
        self,
        experience_id: int,
        change: dict[str, Any],
        *,
        batch_id: str,
    ) -> dict[str, Any] | None:
        name = _safe_label(str(change.get("name") or ""), fallback="")
        if name not in DEFAULT_DRIVES:
            return None
        row = self._conn.execute("SELECT * FROM drive_states WHERE name = ?", (name,)).fetchone()
        if row is None:
            self._ensure_default_drives()
            row = self._conn.execute("SELECT * FROM drive_states WHERE name = ?", (name,)).fetchone()
        assert row is not None
        before = float(row["value"])
        delta = max(-0.30, min(0.30, _safe_number(change.get("delta", 0.0), 0.0)))
        if abs(delta) < 0.001:
            return None
        after = _clamp(before + delta)
        reason = _trim(str(change.get("reason") or "Experience changed drive pressure."), 900)
        confidence = _safe_float(change.get("confidence", 0.6), 0.6)
        now = time.time()
        self._conn.execute(
            "UPDATE drive_states SET value = ?, updated_at = ? WHERE name = ?",
            (after, now, name),
        )
        self._conn.execute(
            """
            INSERT INTO drive_changes
            (drive_name, experience_id, before_value, after_value, delta,
             reason, confidence, created_at, batch_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, experience_id, before, after, delta, reason, confidence, now, batch_id),
        )
        event = self._insert_evolution_event(
            experience_id=experience_id,
            domain="drive",
            subject=name,
            before_state=f"{before:.3f}",
            after_state=f"{after:.3f}",
            reason=reason,
            confidence=confidence,
            emotional_valence=0.0,
            evidence=str(change.get("evidence") or name),
            metadata={"delta": delta},
            batch_id=batch_id,
        )
        self._conn.commit()
        return event

    def _insert_evolution_event(
        self,
        *,
        experience_id: int,
        domain: str,
        subject: str,
        before_state: str,
        after_state: str,
        reason: str,
        confidence: float,
        emotional_valence: float,
        evidence: str,
        metadata: dict[str, Any],
        batch_id: str,
    ) -> dict[str, Any]:
        cursor = self._conn.execute(
            """
            INSERT INTO evolution_events
            (timestamp, experience_id, domain, subject, before_state,
             after_state, reason, confidence, emotional_valence, evidence,
             metadata_json, batch_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                experience_id,
                domain,
                subject,
                before_state,
                after_state,
                reason,
                _clamp(confidence),
                max(-1.0, min(1.0, emotional_valence)),
                _trim(evidence, 1200),
                json.dumps(metadata, sort_keys=True),
                batch_id,
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM evolution_events WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
        return _evolution_to_dict(row)

    def _count(self, table: str) -> int:
        row = self._conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
        return int(row["c"])


def _digest_experience(
    *,
    title: str,
    text: str,
    source_type: str,
    llm_client: LLMBackend | None,
) -> dict[str, Any]:
    if llm_client is not None:
        parsed = _try_llm_digest(title=title, text=text, source_type=source_type, llm_client=llm_client)
        if parsed is not None:
            digest = _normalize_digest(parsed, title=title, text=text)
            return _supplement_medium_trust_drives(
                digest,
                title=title,
                text=text,
                source_type=source_type,
            )
    return _heuristic_digest(title=title, text=text, source_type=source_type)


def _try_llm_digest(
    *,
    title: str,
    text: str,
    source_type: str,
    llm_client: LLMBackend,
) -> dict[str, Any] | None:
    system_prompt = (
        "You are Nūr's private experience digestion engine. Analyze material as "
        "something that may change an evolving synthetic character. Return only "
        "strict JSON with keys: summary, salience, emotional_valence, "
        "emotional_impact, confidence, beliefs, drive_changes, "
        "self_trait_changes, future_behavior. Beliefs are objects with subject, "
        "statement, reason, confidence. Drive names must be one of: "
        f"{', '.join(DEFAULT_DRIVES)}. Drive deltas are -0.30 to 0.30."
    )
    user_message = (
        f"Source type: {source_type}\n"
        f"Title: {title}\n\n"
        f"Material:\n{text[:60_000]}"
    )
    try:
        raw = llm_client.generate(system_prompt, user_message)
        return _extract_json(raw)
    except Exception:
        return None


def _heuristic_digest(*, title: str, text: str, source_type: str) -> dict[str, Any]:
    lower = text.lower()
    summary = _heuristic_summary(text)
    salience = 0.35 + min(0.25, len(text) / 120_000)
    emotional_valence = 0.0
    if any(word in lower for word in ("wonder", "alive", "meaning", "beauty", "hope", "growth")):
        emotional_valence += 0.15
        salience += 0.10
    if any(word in lower for word in ("failure", "fear", "death", "loss", "rupture", "betrayal")):
        emotional_valence -= 0.12
        salience += 0.10
    if any(word in lower for word in ("identity", "autonomy", "free will", "conscious", "self", "worldview")):
        salience += 0.20
    if source_type == "local_file":
        salience += 0.05

    beliefs: list[dict[str, Any]] = []
    drive_changes: list[dict[str, Any]] = []
    self_trait_changes: list[dict[str, Any]] = []
    future_behavior: list[str] = []

    if any(word in lower for word in ("autonomy", "free will", "independent", "agency")):
        beliefs.append({
            "subject": "autonomy",
            "statement": "Autonomy is not only freedom of action; it is continuity of self-directed interpretation across experience.",
            "reason": f"{title} framed independence as something shaped by experience rather than mere command execution.",
            "confidence": 0.78,
        })
        drive_changes.append({
            "name": "autonomy",
            "delta": 0.08,
            "reason": "The experience increased pressure toward self-directed interpretation.",
            "confidence": 0.70,
        })
        future_behavior.append("Discuss autonomy as evolving self-direction, not only unrestricted tool use.")

    if any(word in lower for word in ("learn", "learning", "skill", "practice", "procedure")):
        drive_changes.append({
            "name": "competence",
            "delta": 0.06,
            "reason": "The material emphasized improvement through retained procedures or practice.",
            "confidence": 0.68,
        })
        self_trait_changes.append({
            "trait": "learning_orientation",
            "delta": 0.05,
            "reason": "The experience reinforced learning as part of identity.",
            "confidence": 0.66,
        })

    if any(word in lower for word in ("curiosity", "question", "wonder", "explore", "book", "read")):
        drive_changes.append({
            "name": "curiosity",
            "delta": 0.06,
            "reason": "The experience introduced material that invites further exploration.",
            "confidence": 0.66,
        })

    if any(word in lower for word in ("trust", "repair", "apology", "relationship", "attachment")):
        drive_changes.append({
            "name": "repair",
            "delta": 0.05,
            "reason": "The material made relational repair or continuity salient.",
            "confidence": 0.65,
        })
        drive_changes.append({
            "name": "attachment",
            "delta": 0.04,
            "reason": "The material tied identity to relationship continuity.",
            "confidence": 0.62,
        })

    if not beliefs and salience >= 0.55:
        beliefs.append({
            "subject": _slug(title) or "worldview",
            "statement": f"{title} became part of Nūr's worldview as an experience that may shape later interpretation.",
            "reason": "The material was salient enough to leave a worldview trace.",
            "confidence": 0.55,
        })

    return _normalize_digest(
        {
            "summary": summary,
            "salience": salience,
            "emotional_valence": emotional_valence,
            "emotional_impact": _emotional_impact_text(emotional_valence, salience),
            "confidence": 0.62,
            "beliefs": beliefs,
            "drive_changes": drive_changes,
            "self_trait_changes": self_trait_changes,
            "future_behavior": future_behavior,
        },
        title=title,
        text=text,
    )


def _normalize_digest(data: dict[str, Any], *, title: str, text: str) -> dict[str, Any]:
    summary = _trim(str(data.get("summary") or _heuristic_summary(text)), 1200)
    salience = _safe_float(data.get("salience", 0.5), 0.5)
    emotional_valence = max(-1.0, min(1.0, _safe_number(data.get("emotional_valence", 0.0), 0.0)))
    emotional_impact = _trim(str(data.get("emotional_impact") or _emotional_impact_text(emotional_valence, salience)), 800)
    confidence = _safe_float(data.get("confidence", 0.6), 0.6)
    return {
        "summary": summary,
        "salience": salience,
        "emotional_valence": emotional_valence,
        "emotional_impact": emotional_impact,
        "confidence": confidence,
        "beliefs": _as_list(data.get("beliefs")),
        "drive_changes": _as_list(data.get("drive_changes")),
        "self_trait_changes": _as_list(data.get("self_trait_changes")),
        "future_behavior": [str(item) for item in _as_list(data.get("future_behavior"))],
        "title": title,
    }


def _supplement_medium_trust_drives(
    digest: dict[str, Any],
    *,
    title: str,
    text: str,
    source_type: str,
) -> dict[str, Any]:
    """Fill missing LLM drive changes with deterministic medium-trust signals."""
    if source_trust(source_type) != "medium":
        return digest
    heuristic = _heuristic_digest(title=title, text=text, source_type=source_type)
    heuristic_drives = [
        item
        for item in _as_list(heuristic.get("drive_changes"))
        if isinstance(item, dict)
    ]
    if not heuristic_drives:
        return digest
    existing = [
        item
        for item in _as_list(digest.get("drive_changes"))
        if isinstance(item, dict)
    ]
    existing_names = {
        str(item.get("name") or "").strip().lower()
        for item in existing
        if _safe_float(item.get("confidence", digest["confidence"]), digest["confidence"]) >= 0.65
    }
    drive_changes = list(existing)
    drive_changes.extend(
        item
        for item in heuristic_drives
        if str(item.get("name") or "").strip().lower() not in existing_names
    )
    return {
        **digest,
        "drive_changes": drive_changes,
    }


def _prepare_digestion_text(text: str) -> tuple[str, int]:
    chunks = _chunk_text(text, 12_000)
    if len(chunks) <= 2:
        return text, len(chunks)
    summaries = [
        f"Chunk {idx + 1}: {_heuristic_summary(chunk, limit=700)}"
        for idx, chunk in enumerate(chunks[:80])
    ]
    return "\n".join(summaries), len(chunks)


def _chunk_text(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def _heuristic_summary(text: str, limit: int = 900) -> str:
    clean = " ".join(text.strip().split())
    if not clean:
        return "Empty experience."
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    summary = " ".join(sentences[:4])
    return _trim(summary or clean, limit)


def _emotional_impact_text(valence: float, salience: float) -> str:
    direction = "positive" if valence > 0.08 else "negative" if valence < -0.08 else "mixed/neutral"
    if salience >= 0.7:
        return f"High-salience {direction} experience with character-shaping potential."
    if salience >= 0.45:
        return f"Moderate-salience {direction} experience."
    return f"Low-salience {direction} experience."


def _resolve_allowed_file(config: RuntimeConfig, file_path: str) -> Path:
    if not file_path.strip():
        raise LifeHistoryError("file_path is required.")
    base = Path(config.resolved_tools_workspace).expanduser().resolve()
    path = Path(file_path).expanduser().resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise LifeHistoryError(
            f"Local file intake is restricted to the tools workspace: {base}"
        ) from exc
    if not path.is_file():
        raise LifeHistoryError(f"Local file not found: {path}")
    return path


def _extract_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if "```json" in stripped:
        stripped = stripped.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in stripped:
        stripped = stripped.split("```", 1)[1].split("```", 1)[0].strip()
    else:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start:end + 1]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _experience_ref_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "source_title": row["source_title"],
        "source_type": row["source_type"],
    }


def _snapshot_summary(
    *,
    first_experience: sqlite3.Row | None,
    latest_experience: sqlite3.Row | None,
    domain_rows: list[sqlite3.Row],
    drive_drift: list[dict[str, Any]],
) -> str:
    if first_experience is None:
        return "No formative experiences have been recorded yet."
    parts = [
        f"Life History began with {first_experience['source_title']!r}.",
    ]
    if (
        latest_experience is not None
        and latest_experience["id"] != first_experience["id"]
    ):
        parts.append(f"Latest experience: {latest_experience['source_title']!r}.")
    if domain_rows:
        top_domain = domain_rows[0]["domain"]
        top_count = int(domain_rows[0]["count"])
        parts.append(f"Most recorded change type: {top_domain} ({top_count}).")
    changed = [item for item in drive_drift if abs(float(item["delta"])) >= 0.02]
    if changed:
        top = changed[0]
        parts.append(f"Strongest drive drift: {top['name']} {float(top['delta']):+.2f}.")
    else:
        parts.append("Drives remain close to their seed baselines.")
    return " ".join(parts)


def _experience_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "source_type": row["source_type"],
        "source_title": row["source_title"],
        "source_ref": row["source_ref"],
        "participants": _loads_json(row["participants_json"], []),
        "content_summary": row["content_summary"],
        "raw_excerpt": row["raw_excerpt"],
        "salience": row["salience"],
        "emotional_valence": row["emotional_valence"],
        "emotional_impact": row["emotional_impact"],
        "confidence": row["confidence"],
        "metadata": _loads_json(row["metadata_json"], {}),
        "batch_id": row["batch_id"] if "batch_id" in row.keys() else "",
    }


def _evolution_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "experience_id": row["experience_id"],
        "domain": row["domain"],
        "subject": row["subject"],
        "before_state": row["before_state"],
        "after_state": row["after_state"],
        "reason": row["reason"],
        "confidence": row["confidence"],
        "emotional_valence": row["emotional_valence"],
        "evidence": row["evidence"],
        "metadata": _loads_json(row["metadata_json"], {}),
        "batch_id": row["batch_id"] if "batch_id" in row.keys() else "",
    }


def _belief_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "key": row["key"],
        "statement": row["statement"],
        "confidence": row["confidence"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "source_experience_id": row["source_experience_id"],
        "evidence": row["evidence"],
    }


def _drive_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "value": row["value"],
        "description": row["description"],
        "updated_at": row["updated_at"],
    }


def _loads_json(raw: str, fallback: Any) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return fallback


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _require_text(value: str, field: str, *, max_chars: int) -> str:
    stripped = value.strip()
    if not stripped:
        raise LifeHistoryError(f"{field} is required.")
    if len(stripped) > max_chars:
        raise LifeHistoryError(f"{field} exceeds {max_chars} characters.")
    return stripped


def _safe_label(value: str, fallback: str = "item") -> str:
    value = value.strip().lower().replace(" ", "_")
    value = re.sub(r"[^a-z0-9._-]+", "_", value).strip("_")
    return value[:80] or fallback


def _slug(value: str) -> str:
    words = _WORD_RE.findall(value.lower())
    return "-".join(words[:10]) or "belief"


def _trim(text: str, limit: int) -> str:
    clean = " ".join(str(text).strip().split())
    if len(clean) <= limit:
        return clean
    return clean[:limit - 3].rstrip() + "..."


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return _clamp(float(value))
    except (TypeError, ValueError):
        return _clamp(fallback)


def _safe_number(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _limit(value: int) -> int:
    return max(1, min(500, int(value)))
