"""Schema version management for Nūr databases.

Each SQLite database (per-user and shared) carries an explicit schema version.
On startup, components check their version and fail loudly if the database
was written by a newer version of the code.
"""

from __future__ import annotations

import sqlite3

# Current schema version for all Nūr databases.
# Bump this when tables are added/altered and add a migration path.
SCHEMA_VERSION = 1


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
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
        return SCHEMA_VERSION

    stored = row[0] if isinstance(row, tuple) else row["version"]

    if stored > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema version {stored} is newer than this code supports "
            f"(max {SCHEMA_VERSION}). Upgrade the application or use a compatible database."
        )

    if stored < SCHEMA_VERSION:
        # Future: run migration functions here.
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        conn.commit()

    return stored
