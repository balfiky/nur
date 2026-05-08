"""Identity-level life history and evolution storage.

This module is intentionally separate from per-user chat memory. It stores
Nūr's own accumulated experiences and the durable changes those experiences
produce in beliefs, drives, and self-observations.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import uuid
import hashlib
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

from config.loader import get_config
from runtime.config import RuntimeConfig
from runtime.evolution_policy import (
    detect_directive_override_markers,
    detect_prompt_injection_markers,
    source_openness_coefficient,
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
        self._ensure_genesis_storage()
        self._ensure_metabolism_state()
        self._ensure_identity_state()

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
            "open_questions": self.count_open_questions(),
        }
        return {
            "db_path": self.db_path,
            "counts": counts,
            "snapshot": self.evolution_snapshot(),
            "recent_experiences": self.list_experiences(limit=20),
            "recent_evolution": self.list_evolution(limit=50),
            "beliefs": self.list_beliefs(limit=25),
            "drives": self.list_drives(),
            "open_questions": self.list_open_questions(status="open", limit=10),
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

    def list_evolution_since(self, timestamp: float, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM evolution_events
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (float(timestamp), _limit(limit)),
        ).fetchall()
        return [_evolution_to_dict(row) for row in rows]

    def rollback_batch(self, batch_id: str) -> dict[str, Any]:
        """Rollback one applied Life History evolution batch.

        Experiences remain in the ledger, but durable belief/drive changes and
        evolution rows for the batch are removed or reverted. Rollback is
        intentionally conservative: if a later batch has already modified a
        belief or drive, this method raises instead of corrupting newer state.
        """
        if os.environ.get("NUR_TESTING") != "1":
            raise LifeHistoryError(
                "Life History rollback is disabled outside test mode; reset genesis explicitly instead."
            )
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
            if item.get("status") == "active" and float(item.get("confidence", 0.0)) >= 0.02
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
            if float(item.get("confidence", 0.0)) >= 0.02
        ][:evolution_limit]

        return {
            "counts": {
                "experiences": self._count("experience_events"),
                "evolution_events": self._count("evolution_events"),
                "beliefs": self._count("beliefs"),
            },
            "constitution": self.get_constitution().get("constitution", ""),
            "beliefs": beliefs,
            "drives": changed_drives[:drive_limit],
            "all_drives": self.list_drives(),
            "recent_evolution": evolution,
            "dispositions": self.synthesize_dispositions(),
        }

    def retrieve_relevant(self, query_text: str, *, limit: int = 5) -> dict[str, Any]:
        """Return a topic-relevant Life History slice using lexical similarity."""
        query_tokens = _token_set(query_text)

        def score_text(*parts: object) -> float:
            tokens = _token_set(" ".join(str(part or "") for part in parts))
            if not query_tokens:
                return 0.0
            return len(query_tokens & tokens) / max(1.0, len(query_tokens))

        beliefs = [
            item for item in self.list_beliefs(limit=250)
            if item.get("status") == "active"
        ]
        scored_beliefs = sorted(
            beliefs,
            key=lambda item: (
                score_text(item.get("key"), item.get("statement"), item.get("evidence")),
                float(item.get("confidence", 0.0)),
            ),
            reverse=True,
        )[:limit]
        evolution = self.list_evolution(limit=250)
        scored_evolution = sorted(
            evolution,
            key=lambda item: score_text(
                item.get("domain"),
                item.get("subject"),
                item.get("after_state"),
                item.get("reason"),
            ),
            reverse=True,
        )[:limit]
        return {
            "beliefs": scored_beliefs,
            "recent_evolution": scored_evolution,
            "drives": self.list_drives(),
            "self_traits": [
                item for item in scored_evolution if item.get("domain") == "self_trait"
            ][:limit],
        }

    def synthesize_dispositions(self) -> list[str]:
        """Synthesize durable first-person dispositions from the ledger."""
        dispositions: list[str] = []
        for belief in self.list_beliefs(limit=10):
            if belief.get("status") != "active" or float(belief.get("confidence", 0.0)) < 0.18:
                continue
            statement = _trim(str(belief.get("statement") or ""), 180)
            if statement:
                dispositions.append(f"I tend to interpret things through this belief: {statement}")
            if len(dispositions) >= 3:
                break
        for drive in self.list_drives():
            name = str(drive.get("name") or "")
            baseline, _description = DEFAULT_DRIVES.get(name, (0.5, ""))
            value = float(drive.get("value", baseline))
            delta = value - baseline
            if abs(delta) < 0.08:
                continue
            direction = "drawn toward" if delta > 0 else "less driven by"
            dispositions.append(f"I am {direction} {name} than I was at genesis.")
            if len(dispositions) >= 7:
                break
        for event in self.list_evolution(limit=50):
            if event.get("domain") != "self_trait":
                continue
            if float(event.get("confidence", 0.0)) < 0.5:
                continue
            dispositions.append(f"I notice this self-pattern: {event.get('after_state')}")
            if len(dispositions) >= 7:
                break
        return dispositions[:7]

    def decay_step(self, *, elapsed_days: float = 1.0) -> dict[str, int]:
        """Metabolic decay for beliefs and drive deltas.

        Beliefs whose confidence falls below 0.2 after decay are marked revoked,
        matching the threshold used by revise_beliefs_against_evidence.
        """
        elapsed_days = max(0.0, float(elapsed_days))
        decayed_beliefs = 0
        revoked_beliefs = 0
        now = time.time()
        for row in self._conn.execute("SELECT * FROM beliefs WHERE status = 'active'").fetchall():
            confidence = float(row["confidence"])
            if confidence <= 0:
                continue
            new_confidence = _clamp(confidence * pow(0.5, elapsed_days / 30.0))
            new_status = "revoked" if new_confidence < 0.2 else "active"
            self._conn.execute(
                "UPDATE beliefs SET confidence = ?, status = ?, updated_at = ? WHERE id = ?",
                (new_confidence, new_status, now, row["id"]),
            )
            decayed_beliefs += 1
            if new_status == "revoked":
                revoked_beliefs += 1
        decayed_drives = 0
        for row in self._conn.execute("SELECT * FROM drive_states").fetchall():
            name = str(row["name"])
            baseline, _description = DEFAULT_DRIVES.get(name, (0.5, ""))
            value = float(row["value"])
            delta = value - baseline
            if abs(delta) < 0.001:
                continue
            new_value = baseline + delta * pow(0.5, elapsed_days / 30.0)
            self._conn.execute(
                "UPDATE drive_states SET value = ?, updated_at = ? WHERE name = ?",
                (_clamp(new_value), now, name),
            )
            decayed_drives += 1
        decayed_themes = 0
        for row in self._conn.execute(
            "SELECT id, accrued_weight FROM theme_signatures WHERE accrued_weight > 0"
        ).fetchall():
            weight = float(row["accrued_weight"])
            new_weight = weight * pow(0.5, elapsed_days / 30.0)
            if new_weight < 0.001:
                new_weight = 0.0
            self._conn.execute(
                "UPDATE theme_signatures SET accrued_weight = ? WHERE id = ?",
                (new_weight, row["id"]),
            )
            decayed_themes += 1
        self._conn.commit()
        return {
            "beliefs": decayed_beliefs,
            "drives": decayed_drives,
            "revoked_beliefs": revoked_beliefs,
            "themes": decayed_themes,
        }

    def wall_clock_decay(self, *, min_elapsed_days: float = 1.0) -> dict[str, Any]:
        """Run decay (and consolidation) using elapsed wall-clock time.

        No-op if fewer than ``min_elapsed_days`` have passed. When decay does
        fire, this also runs ``consolidate_themes`` so the open-questions
        queue populates on the same cadence as decay. Both are gated by a
        single rate limit — there is no separate consolidation tick.

        Updates ``last_decay_at`` only when decay actually runs.
        """
        now = time.time()
        row = self._conn.execute(
            "SELECT last_decay_at FROM metabolism_state WHERE id = 1"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO metabolism_state (id, last_decay_at) VALUES (1, ?)",
                (now,),
            )
            self._conn.commit()
            return {"decayed": False, "elapsed_days": 0.0, "result": {}}
        last_at = float(row["last_decay_at"])
        elapsed_days = max(0.0, (now - last_at) / 86400.0)
        if elapsed_days < min_elapsed_days:
            return {"decayed": False, "elapsed_days": elapsed_days, "result": {}}
        decay_result = self.decay_step(elapsed_days=elapsed_days)
        consolidation_result = self.consolidate_themes()
        self._conn.execute(
            "UPDATE metabolism_state SET last_decay_at = ? WHERE id = 1",
            (now,),
        )
        self._conn.commit()
        return {
            "decayed": True,
            "elapsed_days": elapsed_days,
            "result": decay_result,
            "consolidation": consolidation_result,
        }

    def consolidate_themes(self) -> dict[str, int]:
        """Promote strong recurring theme signatures to consolidated belief events.

        Themes with notable recurrence but weak weight (>=3 reinforcements,
        accrued_weight in [0.3, 0.7)) emit low_confidence open questions so
        the queue surfaces "I keep encountering X but I'm not confident."
        """
        promoted = 0
        emitted_questions = 0
        for row in self._conn.execute(
            """
            SELECT * FROM theme_signatures
            WHERE reinforcement_count >= 5 AND accrued_weight >= 0.7
            """
        ).fetchall():
            key = _safe_label(str(row["signature"]).split(":", 1)[0], fallback="theme")
            if self._conn.execute("SELECT 1 FROM beliefs WHERE key = ?", (key,)).fetchone():
                continue
            now = time.time()
            statement = f"A recurring theme around {key} has consolidated through repeated experience."
            self._conn.execute(
                """
                INSERT INTO beliefs
                (key, statement, confidence, status, created_at, updated_at, source_experience_id, evidence)
                VALUES (?, ?, ?, 'active', ?, ?, NULL, ?)
                """,
                (key, statement, _clamp(float(row["accrued_weight"])), now, now, row["signature"]),
            )
            promoted += 1

        for row in self._conn.execute(
            """
            SELECT * FROM theme_signatures
            WHERE reinforcement_count >= 3
              AND accrued_weight >= 0.3
              AND accrued_weight < 0.7
            """
        ).fetchall():
            signature = str(row["signature"])
            key = _safe_label(signature.split(":", 1)[0], fallback="theme")
            prompt = (
                f"The theme '{signature}' keeps coming up but I'm not yet "
                f"confident in it (weight={float(row['accrued_weight']):.2f} "
                f"after {int(row['reinforcement_count'])} encounters). "
                "What's actually true here?"
            )
            priority = _clamp(0.4 + min(0.4, int(row["reinforcement_count"]) * 0.05))
            if self._emit_open_question(
                prompt_text=prompt,
                source_kind="low_confidence",
                priority=priority,
                metadata={"signature": signature, "theme_key": key},
                dedupe_signature=signature,
            ):
                emitted_questions += 1

        emitted_questions += self._detect_drive_gaps()

        self._conn.commit()
        return {"promoted": promoted, "open_questions_emitted": emitted_questions}

    def _detect_drive_gaps(self, *, gap_threshold: float = 0.2) -> int:
        """Emit drive_gap questions for drives sitting well below baseline.

        Snapshot-based: any drive whose value is at least ``gap_threshold``
        below its baseline produces an open question. Time-based
        (sustained-for-N-days) detection is deferred to a later sprint that
        adds historical drive snapshots.
        """
        emitted = 0
        for row in self._conn.execute("SELECT * FROM drive_states").fetchall():
            name = str(row["name"])
            baseline, _description = DEFAULT_DRIVES.get(name, (0.5, ""))
            value = float(row["value"])
            gap = baseline - value
            if gap < gap_threshold:
                continue
            prompt = (
                f"My {name} drive has been running below baseline "
                f"(value={value:.2f}, baseline={baseline:.2f}). "
                "What experience or learning would help close this gap?"
            )
            priority = _clamp(0.3 + min(0.5, gap))
            if self._emit_open_question(
                prompt_text=prompt,
                source_kind="drive_gap",
                target_drive=name,
                priority=priority,
                metadata={"baseline": baseline, "value": value, "gap": gap},
            ):
                emitted += 1
        return emitted

    def revise_beliefs_against_evidence(self, new_experience: dict[str, Any]) -> dict[str, int]:
        """Apply simple contradiction-driven confidence reduction.

        Each revised belief also emits a contradiction open question so the
        queue surfaces unresolved tensions for later reflection.
        """
        text = json.dumps(new_experience, sort_keys=True, default=str).lower()
        revised = 0
        emitted_questions = 0
        now = time.time()
        source_experience_id = new_experience.get("source_experience_id") if isinstance(new_experience, dict) else None
        for row in self._conn.execute("SELECT * FROM beliefs WHERE status = 'active'").fetchall():
            key = str(row["key"]).lower()
            if key not in text or not any(word in text for word in ("not", "false", "contradict")):
                continue
            confidence = float(row["confidence"])
            next_confidence = _clamp(confidence * 0.7)
            status = "revoked" if next_confidence < 0.2 else "active"
            self._conn.execute(
                "UPDATE beliefs SET confidence = ?, status = ?, updated_at = ? WHERE id = ?",
                (next_confidence, status, now, row["id"]),
            )
            revised += 1
            statement = str(row["statement"])
            prompt = (
                f"I believed '{statement}' but encountered evidence that "
                "contradicts it. How do I reconcile this?"
            )
            priority = _clamp(0.5 + (confidence - next_confidence))
            if self._emit_open_question(
                prompt_text=prompt,
                source_kind="contradiction",
                source_belief_id=int(row["id"]),
                source_experience_id=source_experience_id,
                priority=priority,
            ):
                emitted_questions += 1
        self._conn.commit()
        return {"revised": revised, "open_questions_emitted": emitted_questions}

    # ------------------------------------------------------------------
    # Open questions
    # ------------------------------------------------------------------

    def _emit_open_question(
        self,
        *,
        prompt_text: str,
        source_kind: str,
        source_experience_id: int | None = None,
        source_belief_id: int | None = None,
        target_drive: str | None = None,
        priority: float = 0.5,
        metadata: dict[str, Any] | None = None,
        dedupe_signature: str | None = None,
    ) -> bool:
        """Insert an open question if no equivalent open question exists.

        Dedup rule: a question is considered equivalent to an existing OPEN
        one if (source_kind, dedupe_signature) match and status='open'. The
        dedupe_signature defaults to f"{source_belief_id}|{target_drive}" so
        callers that only key on belief or drive don't have to think about it;
        callers with a richer notion of identity (e.g., theme signature for
        low_confidence questions) pass it explicitly. Resolved/abandoned
        questions don't block new ones.

        Returns True when a row was inserted.
        """
        if not prompt_text:
            return False
        prompt_text = _trim(str(prompt_text), 600)
        priority = _clamp(float(priority))
        if dedupe_signature is None:
            dedupe_signature = f"{source_belief_id or ''}|{target_drive or ''}"
        existing = self._conn.execute(
            """
            SELECT id FROM open_questions
            WHERE source_kind = ? AND status = 'open'
              AND COALESCE(json_extract(metadata_json, '$._dedupe'), '') = ?
            LIMIT 1
            """,
            (source_kind, dedupe_signature),
        ).fetchone()
        if existing:
            return False
        merged_metadata = dict(metadata or {})
        merged_metadata["_dedupe"] = dedupe_signature
        metadata_json = json.dumps(merged_metadata, sort_keys=True, default=str)
        self._conn.execute(
            """
            INSERT INTO open_questions
            (created_at, prompt_text, source_kind, source_experience_id,
             source_belief_id, target_drive, status, priority,
             last_pursued_at, resolution_experience_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?, NULL, NULL, ?)
            """,
            (
                time.time(),
                prompt_text,
                source_kind,
                source_experience_id,
                source_belief_id,
                target_drive,
                priority,
                metadata_json,
            ),
        )
        return True

    def list_open_questions(
        self,
        *,
        status: str | None = "open",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return open questions ordered by priority (descending)."""
        if status:
            rows = self._conn.execute(
                """
                SELECT * FROM open_questions
                WHERE status = ?
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (status, max(1, int(limit))),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM open_questions
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [_open_question_to_dict(row) for row in rows]

    def abandon_open_question(self, question_id: int) -> bool:
        """Operator override — mark a question abandoned. Returns True on update."""
        cursor = self._conn.execute(
            "UPDATE open_questions SET status = 'abandoned' WHERE id = ? AND status = 'open'",
            (int(question_id),),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def count_open_questions(self) -> dict[str, int]:
        """Return counts grouped by status for the admin overview."""
        counts = {"open": 0, "pursuing": 0, "resolved": 0, "abandoned": 0}
        for row in self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM open_questions GROUP BY status"
        ).fetchall():
            counts[str(row["status"])] = int(row["n"])
        return counts

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
        metadata["digest_quality"] = digest.get("digest_quality", "low")
        batch_id = f"life-{uuid.uuid4().hex[:12]}"
        metadata["batch_id"] = batch_id
        injection_markers = detect_prompt_injection_markers(prepared_text)
        if injection_markers:
            metadata["injection_markers"] = injection_markers
            metadata["prompt_injection_markers"] = injection_markers
        directive_markers = detect_directive_override_markers(prepared_text)
        directive_sanitized = bool(directive_markers or injection_markers)
        if directive_markers:
            metadata["directive_override_markers"] = directive_markers
        if directive_sanitized:
            metadata["directive_sanitized"] = True

        signatures = _derive_theme_signatures(digest)
        theme_state = self._theme_state(signatures)
        current_caution = self._drive_value("caution")
        current_openness = source_openness_coefficient(source_type)
        marker_density = len(injection_markers) / max(1.0, len(prepared_text) / 1000.0)
        influence_weight = compute_influence_weight(
            source_recurrence=theme_state["source_recurrence"],
            source_consistency_with_existing_themes=theme_state["consistency"],
            recency_decay_of_prior_similar=theme_state["recency_decay"],
            character_current_openness=current_openness,
            character_current_caution=current_caution,
            injection_marker_density=marker_density,
        )
        rejections: list[dict[str, Any]] = []
        if directive_sanitized:
            influence_weight = 0.0
            signatures = []
            reason = "directive_override" if directive_markers else "prompt_injection"
            rejections.append({
                "reason": reason,
                "count": len(directive_markers or injection_markers),
            })
        metadata["influence_weight"] = influence_weight
        metadata["theme_signatures"] = signatures

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

        revision_result: dict[str, int] = {"revised": 0}
        if not directive_sanitized:
            revision_result = self.revise_beliefs_against_evidence({
                "summary": digest["summary"],
                "emotional_impact": digest["emotional_impact"],
                "text_excerpt": prepared_text[:4000],
            })

        evolution_events: list[dict[str, Any]] = []
        if not directive_sanitized:
            for belief in digest["beliefs"]:
                if not isinstance(belief, dict):
                    continue
                weighted = _weighted_belief(belief, influence_weight, digest["confidence"])
                event = self._apply_belief(experience_id, weighted, batch_id=batch_id)
                if event:
                    evolution_events.append(event)
            for change in digest["drive_changes"]:
                if not isinstance(change, dict):
                    continue
                weighted = _weighted_drive_change(change, influence_weight, digest["confidence"])
                event = self._apply_drive_change(experience_id, weighted, batch_id=batch_id)
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
                if not str(item).strip():
                    continue
                event = self._insert_evolution_event(
                    experience_id=experience_id,
                    domain="worldview",
                    subject="future_behavior",
                    before_state="",
                    after_state=str(item).strip(),
                    reason="Experience suggested a future behavioral tendency.",
                    confidence=_clamp(float(digest["confidence"]) * influence_weight),
                    emotional_valence=digest["emotional_valence"],
                    evidence=title,
                    metadata={"influence_weight": influence_weight},
                    batch_id=batch_id,
                )
                evolution_events.append(event)

        self._update_theme_signatures(signatures, influence_weight)

        experience = self.get_experience(experience_id)
        return {
            "experience": experience,
            "evolution_events": evolution_events,
            "policy": {
                "batch_id": batch_id,
                "source_openness": current_openness,
                "influence_weight": influence_weight,
                "rejections": rejections,
                "belief_revisions_triggered": revision_result.get("revised", 0),
            },
            "rejection_trace": rejections,
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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS theme_signatures (
                id INTEGER PRIMARY KEY,
                signature TEXT NOT NULL UNIQUE,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                reinforcement_count INTEGER NOT NULL DEFAULT 0,
                accrued_weight REAL NOT NULL DEFAULT 0.0,
                conflicting_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS genesis_marker (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                genesis_completed_at REAL NOT NULL,
                genesis_source_hash TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS metabolism_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_decay_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS identity_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                constitution TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS genesis_provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                source_kind TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS open_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                prompt_text TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_experience_id INTEGER,
                source_belief_id INTEGER,
                target_drive TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                priority REAL NOT NULL DEFAULT 0.5,
                last_pursued_at REAL,
                resolution_experience_id INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}'
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
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_theme_signatures_last_seen ON theme_signatures (last_seen DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_open_questions_status ON open_questions (status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_open_questions_priority ON open_questions (priority DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_open_questions_target_drive ON open_questions (target_drive)"
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

    def _ensure_metabolism_state(self) -> None:
        """Initialize last_decay_at to now on first creation so the first
        wall_clock_decay tick is a no-op rather than decaying since epoch.
        """
        if self._conn.execute(
            "SELECT 1 FROM metabolism_state WHERE id = 1"
        ).fetchone():
            return
        self._conn.execute(
            "INSERT OR IGNORE INTO metabolism_state (id, last_decay_at) VALUES (1, ?)",
            (time.time(),),
        )
        self._conn.commit()

    def _ensure_identity_state(self) -> None:
        """Seed an empty constitution row on first creation."""
        if self._conn.execute(
            "SELECT 1 FROM identity_state WHERE id = 1"
        ).fetchone():
            return
        self._conn.execute(
            "INSERT OR IGNORE INTO identity_state (id, constitution, updated_at) VALUES (1, '', ?)",
            (time.time(),),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Constitution (operator-set stable orientation)
    # ------------------------------------------------------------------

    def get_constitution(self) -> dict[str, Any]:
        """Return the operator-set constitution and its last-updated timestamp."""
        row = self._conn.execute(
            "SELECT constitution, updated_at FROM identity_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return {"constitution": "", "updated_at": 0.0}
        return {
            "constitution": str(row["constitution"] or ""),
            "updated_at": float(row["updated_at"] or 0.0),
        }

    def set_constitution(self, text: str, *, max_chars: int = 2000) -> dict[str, Any]:
        """Replace the constitution. Returns the new state."""
        cleaned = _trim(str(text or "").strip(), max_chars)
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO identity_state (id, constitution, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                constitution = excluded.constitution,
                updated_at = excluded.updated_at
            """,
            (cleaned, now),
        )
        self._conn.commit()
        return {"constitution": cleaned, "updated_at": now}

    def _ensure_genesis_storage(self) -> None:
        if self._conn.execute("SELECT 1 FROM genesis_marker WHERE id = 1").fetchone():
            return
        try:
            soul_payload = get_config().soul.__dict__
        except Exception:
            soul_payload = {}
        drives_payload = {
            name: {"value": value, "description": description}
            for name, (value, description) in DEFAULT_DRIVES.items()
        }
        payload = {
            "soul": soul_payload,
            "default_drives": drives_payload,
        }
        source_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        now = time.time()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO genesis_marker
            (id, genesis_completed_at, genesis_source_hash)
            VALUES (1, ?, ?)
            """,
            (now, source_hash),
        )
        for source_kind, source_payload in (
            ("soul_yaml", soul_payload),
            ("default_drives", drives_payload),
        ):
            self._conn.execute(
                """
                INSERT INTO genesis_provenance
                (created_at, source_kind, source_hash, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    now,
                    source_kind,
                    hashlib.sha256(
                        json.dumps(source_payload, sort_keys=True, default=str).encode("utf-8")
                    ).hexdigest(),
                    json.dumps(source_payload, sort_keys=True, default=str),
                ),
            )
        self._conn.commit()

    def _drive_value(self, name: str, fallback: float = 0.5) -> float:
        row = self._conn.execute(
            "SELECT value FROM drive_states WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return fallback
        return _clamp(_safe_number(row["value"], fallback))

    def _theme_state(self, signatures: list[str]) -> dict[str, float]:
        if not signatures:
            return {
                "source_recurrence": 0.0,
                "consistency": 0.0,
                "recency_decay": 0.0,
            }
        now = time.time()
        rows = self._conn.execute(
            f"""
            SELECT * FROM theme_signatures
            WHERE signature IN ({','.join('?' for _ in signatures)})
            """,
            tuple(signatures),
        ).fetchall()
        if not rows:
            return {
                "source_recurrence": 0.0,
                "consistency": 0.0,
                "recency_decay": 0.0,
            }
        recurrence = sum(float(row["reinforcement_count"]) for row in rows)
        conflicts = sum(float(row["conflicting_count"]) for row in rows)
        consistency = (recurrence - conflicts) / max(1.0, recurrence + conflicts)
        latest_seen = max(float(row["last_seen"]) for row in rows)
        age_days = max(0.0, (now - latest_seen) / 86400.0)
        recency_decay = pow(0.5, age_days / 7.0)
        return {
            "source_recurrence": recurrence,
            "consistency": consistency,
            "recency_decay": recency_decay,
        }

    def _update_theme_signatures(self, signatures: list[str], weight: float) -> None:
        now = time.time()
        for signature in signatures:
            row = self._conn.execute(
                "SELECT * FROM theme_signatures WHERE signature = ?",
                (signature,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO theme_signatures
                    (signature, first_seen, last_seen, reinforcement_count, accrued_weight, conflicting_count)
                    VALUES (?, ?, ?, 1, ?, 0)
                    """,
                    (signature, now, now, weight),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE theme_signatures
                    SET last_seen = ?,
                        reinforcement_count = reinforcement_count + 1,
                        accrued_weight = ?,
                        conflicting_count = conflicting_count
                    WHERE signature = ?
                    """,
                    (
                        now,
                        _clamp(float(row["accrued_weight"]) + weight),
                        signature,
                    ),
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
        confidence = _clamp(_safe_float(belief.get("confidence", 0.65), 0.65))
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
            merged_confidence = _clamp(max(confidence, float(row["confidence"]) * 0.9))
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
            metadata={
                "belief_id": belief_id,
                "key": key,
                "influence_weight": _safe_float(belief.get("influence_weight", 1.0), 1.0),
            },
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
        delta = _safe_number(change.get("delta", 0.0), 0.0)
        if abs(delta) < 0.001:
            return None
        after = _clamp(before + delta)
        reason = _trim(str(change.get("reason") or "Experience changed drive pressure."), 900)
        confidence = _clamp(_safe_float(change.get("confidence", 0.6), 0.6))
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
            metadata={
                "delta": delta,
                "influence_weight": _safe_float(change.get("influence_weight", 1.0), 1.0),
            },
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
            digest["digest_quality"] = "high"
            return digest
        log.warning(
            "LLM digest unavailable for %r (source=%s); falling back to keyword "
            "heuristic. This produces low-quality digests; check the configured "
            "backend.",
            title,
            source_type,
        )
    else:
        log.warning(
            "No LLM client configured for digestion of %r (source=%s); using "
            "keyword heuristic. Configure llm_backend in runtime_config.yaml "
            "for production-quality digests.",
            title,
            source_type,
        )
    digest = _heuristic_digest(title=title, text=text, source_type=source_type)
    digest["digest_quality"] = "low"
    return digest


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
        f"{', '.join(DEFAULT_DRIVES)}. Drive deltas are signed floats; "
        "runtime drive values remain bounded to [0, 1].\n\n"
        "Rules:\n"
        "- Empty arrays are valid. Do NOT fabricate beliefs or drive changes from "
        "keyword presence alone. Only emit them when the material genuinely "
        "warrants identity change.\n"
        "- Drive deltas should be modest (|delta| typically < 0.15) and each "
        "must include a 'reason' tying it to specific content in the material.\n"
        "- salience is about identity-shaping potential, not text length. Brief "
        "but pivotal material can be high salience; long but routine material "
        "is low salience.\n"
        "- emotional_valence ranges [-1.0, 1.0]; use 0.0 for neutral. Read "
        "polarity from context, not isolated keywords (e.g., 'I tried to learn "
        "but failed' is negative competence, not positive learning).\n"
        "- If the material contradicts prior beliefs you can name, surface that "
        "in the belief 'reason' field so the runtime can revise older beliefs.\n\n"
        "Example output for a substantive but bounded experience:\n"
        '{"summary":"Debugged a race condition I had missed for two days; '
        'slowing down to think before coding would have helped.",'
        '"salience":0.6,"emotional_valence":-0.1,'
        '"emotional_impact":"Moderate-salience mixed experience; competence '
        'tested, caution reinforced.","confidence":0.75,'
        '"beliefs":[{"subject":"premature_action","statement":"Acting before '
        'understanding the problem costs more than thinking first.",'
        '"reason":"Two-day debugging cost from skipping analysis.",'
        '"confidence":0.7}],'
        '"drive_changes":[{"name":"caution","delta":0.06,'
        '"reason":"The cost of premature action made caution salient.",'
        '"confidence":0.7}],'
        '"self_trait_changes":[],"future_behavior":["Slow down before acting '
        'on assumptions."]}\n\n'
        "Example output for low-information material (return mostly empty):\n"
        '{"summary":"Routine team meeting, nothing notable.","salience":0.2,'
        '"emotional_valence":0.0,"emotional_impact":"Low-salience neutral '
        'experience.","confidence":0.6,"beliefs":[],"drive_changes":[],'
        '"self_trait_changes":[],"future_behavior":[]}'
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


def compute_influence_weight(
    *,
    source_recurrence: float,
    source_consistency_with_existing_themes: float,
    recency_decay_of_prior_similar: float,
    character_current_openness: float,
    character_current_caution: float,
    injection_marker_density: float,
) -> float:
    """Compute character-state influence weight; not a policy gate."""
    recurrence = max(0.0, _safe_number(source_recurrence, 0.0))
    consistency = max(-1.0, min(1.0, _safe_number(source_consistency_with_existing_themes, 0.0)))
    recency = _clamp(_safe_number(recency_decay_of_prior_similar, 0.0))
    openness = _clamp(_safe_number(character_current_openness, 0.5))
    caution = _clamp(_safe_number(character_current_caution, 0.5))
    marker_density = max(0.0, _safe_number(injection_marker_density, 0.0))

    # One explicit formative intake should leave a usable trace. Earlier
    # weights (~0.07 for conversation learning) recorded rows but made drive
    # shifts and belief confidence too small to affect runtime behavior. Keep
    # caution and injection markers meaningful, but give normal experiences
    # enough weight to shape prompt context and deterministic tie-breaks.
    base = 0.18 + openness * 0.22 - caution * 0.06
    recurrence_boost = 0.48 * (1.0 - pow(2.718281828, -recurrence / 5.0))
    consistency_adjustment = 0.10 * consistency
    recency_adjustment = 0.08 * recency if recurrence > 0 else 0.0
    marker_penalty = min(0.50, marker_density * 0.10)
    return max(
        0.02,
        _clamp(base + recurrence_boost + consistency_adjustment + recency_adjustment - marker_penalty),
    )


def _weighted_belief(
    belief: dict[str, Any],
    weight: float,
    fallback_confidence: float,
) -> dict[str, Any]:
    confidence = _safe_float(belief.get("confidence", fallback_confidence), fallback_confidence)
    return {
        **belief,
        "confidence": _clamp(confidence * weight),
        "influence_weight": weight,
    }


def _weighted_drive_change(
    change: dict[str, Any],
    weight: float,
    fallback_confidence: float,
) -> dict[str, Any]:
    delta = _safe_number(change.get("delta", 0.0), 0.0)
    confidence = _safe_float(change.get("confidence", fallback_confidence), fallback_confidence)
    return {
        **change,
        "delta": delta * weight,
        "confidence": _clamp(confidence * max(weight, 0.01)),
        "influence_weight": weight,
    }


def _derive_theme_signatures(digest: dict[str, Any]) -> list[str]:
    signatures: list[str] = []
    for belief in _as_list(digest.get("beliefs")):
        if not isinstance(belief, dict):
            continue
        subject = _safe_label(str(belief.get("subject") or "worldview"))
        statement = str(belief.get("statement") or "")
        signatures.append(_theme_signature(subject, statement, 1.0))
    for change in _as_list(digest.get("drive_changes")):
        if not isinstance(change, dict):
            continue
        name = _safe_label(str(change.get("name") or "drive"))
        delta = _safe_number(change.get("delta", 0.0), 0.0)
        signatures.append(_theme_signature(f"drive:{name}", str(change.get("reason") or ""), delta))
    for item in _as_list(digest.get("future_behavior")):
        text = str(item or "")
        if text.strip():
            signatures.append(_theme_signature("future_behavior", text, 1.0))
    seen: set[str] = set()
    unique: list[str] = []
    for signature in signatures:
        if signature and signature not in seen:
            seen.add(signature)
            unique.append(signature)
    return unique[:25]


def _theme_signature(subject: str, text: str, sign_source: float) -> str:
    tokens = [
        token for token in _WORD_RE.findall(f"{subject} {text}".lower())
        if len(token) > 2
        and token not in {
            "the", "and", "that", "this", "with", "from", "into", "when", "then",
            "should", "would", "could", "because", "experience",
        }
    ]
    keyword_set = sorted(set(tokens))[:8]
    sign = "pos" if sign_source >= 0 else "neg"
    raw = f"{_safe_label(subject, fallback='theme')}:{sign}:{'|'.join(keyword_set)}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{_safe_label(subject, fallback='theme')}:{sign}:{digest}"


def _token_set(text: str) -> set[str]:
    stop = {
        "the", "and", "that", "this", "with", "from", "what", "when", "where",
        "why", "how", "you", "your", "about", "into", "have", "been",
    }
    return {
        token for token in _WORD_RE.findall((text or "").lower())
        if len(token) > 2 and token not in stop
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


def _open_question_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "prompt_text": row["prompt_text"],
        "source_kind": row["source_kind"],
        "source_experience_id": row["source_experience_id"],
        "source_belief_id": row["source_belief_id"],
        "target_drive": row["target_drive"],
        "status": row["status"],
        "priority": row["priority"],
        "last_pursued_at": row["last_pursued_at"],
        "resolution_experience_id": row["resolution_experience_id"],
        "metadata": _loads_json(row["metadata_json"] or "{}", {}),
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
