"""Schema version management for Nūr databases.

Each SQLite database (per-user and shared) carries an explicit schema version.
On startup, components check their version and fail loudly if the database
was written by a newer version of the code.
"""

from __future__ import annotations

import sqlite3

# Current schema version for all Nūr databases.
# Bump this when tables are added/altered and add a migration path.
SCHEMA_VERSION = 2


class SchemaVersionError(RuntimeError):
    """Raised when a database has a schema version newer than the code supports."""
    pass


def ensure_schema_version(conn: sqlite3.Connection) -> int:
    """Check or initialize the schema_version table.

    - If the table doesn't exist, create it with SCHEMA_VERSION.
    - If the stored version matches SCHEMA_VERSION, return it.
    - If the stored version is older, migrate (no migrations yet) and update.
    - If the stored version is newer than SCHEMA_VERSION, raise SchemaVersionError.

    Returns the (post-migration) version.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )
    """)
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()

    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        conn.commit()
        stored = 0
    else:
        stored = row[0] if isinstance(row, tuple) else row["version"]

    if stored > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema version {stored} is newer than this code supports "
            f"(max {SCHEMA_VERSION}). Upgrade the application or use a compatible database."
        )

    if stored < SCHEMA_VERSION:
        _run_migrations(conn, stored, SCHEMA_VERSION)
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        conn.commit()
        return SCHEMA_VERSION

    return stored


def _run_migrations(conn: sqlite3.Connection, stored: int, target: int) -> None:
    """Apply incremental schema migrations in order."""
    current = stored
    while current < target:
        if current == 0:
            current = 1
            continue
        if current == 1:
            _migrate_1_to_2(conn)
            current = 2
            continue
        raise SchemaVersionError(
            f"No migration path available from schema version {current} to {target}."
        )


def _migrate_1_to_2(conn: sqlite3.Connection) -> None:
    """Add relationship-memory tables for multi-session social continuity."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS relationship_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_kind TEXT NOT NULL,
            source_person TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL,
            valence REAL NOT NULL,
            intensity REAL NOT NULL,
            confidence REAL NOT NULL,
            created_at REAL NOT NULL,
            related_key TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS open_loops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            loop_kind TEXT NOT NULL,
            source_person TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL,
            intensity REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            resolved_at REAL,
            related_key TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relationship_events_person_created
        ON relationship_events (source_person, created_at DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_open_loops_person_status_updated
        ON open_loops (source_person, status, updated_at DESC)
    """)
