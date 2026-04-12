"""PostgreSQL migration helper.

This module provides a documented migration path from SQLite to PostgreSQL.
The ``migrate_to_postgres()`` function outlines the steps required; actual
execution should be performed in a maintenance window with proper backups.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def migrate_to_postgres(pg_dsn: str | None = None) -> dict:
    """Document and return the steps needed to migrate from SQLite to PostgreSQL.

    This is a **planning stub** — it does not perform the migration automatically.
    Run each step manually in a maintenance window.

    Args:
        pg_dsn: PostgreSQL connection string (e.g. ``postgresql://user:pass@host/db``)

    Returns:
        A dict describing the migration steps.
    """
    steps = [
        {
            "step": 1,
            "title": "Provision PostgreSQL",
            "description": (
                "Create a PostgreSQL 15+ instance. Recommended: "
                "managed service (AWS RDS, Cloud SQL, Azure Database)."
            ),
        },
        {
            "step": 2,
            "title": "Set DATABASE_URL",
            "description": (
                "Set DATABASE_URL=postgresql://user:pass@host:5432/teb "
                "in your environment. The app will detect the 'postgresql' "
                "scheme and use psycopg2/asyncpg instead of sqlite3."
            ),
        },
        {
            "step": 3,
            "title": "Run schema migration",
            "description": (
                "Use a migration tool (Alembic, pgloader, or manual DDL) "
                "to create all tables in PostgreSQL. Column types to adjust: "
                "INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY, "
                "TEXT timestamps → TIMESTAMPTZ, PRAGMA statements → removed."
            ),
        },
        {
            "step": 4,
            "title": "Export SQLite data",
            "description": (
                "Use `sqlite3 teb.db .dump > dump.sql` or pgloader to "
                "export all rows from the SQLite database."
            ),
        },
        {
            "step": 5,
            "title": "Import into PostgreSQL",
            "description": (
                "Load the exported data into PostgreSQL. Verify row counts "
                "match between source and target for every table."
            ),
        },
        {
            "step": 6,
            "title": "Update connection layer",
            "description": (
                "Replace sqlite3.connect() calls with psycopg2 or asyncpg "
                "connections. Update _conn() context manager and all raw SQL "
                "that uses SQLite-specific syntax (e.g. datetime functions)."
            ),
        },
        {
            "step": 7,
            "title": "Verify and switch",
            "description": (
                "Run the full test suite against PostgreSQL. Once green, "
                "update production DATABASE_URL and deploy."
            ),
        },
    ]

    return {
        "status": "migration_plan",
        "target_dsn": pg_dsn or "(not provided)",
        "steps": steps,
        "notes": (
            "Always take a full backup of the SQLite database before starting. "
            "Run migrations during a maintenance window with the app stopped."
        ),
    }
