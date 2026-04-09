"""teb database migration runner.

A lightweight migration system for teb's SQLite database.

Usage
-----
Apply all pending migrations::

    python -m migrations.migrate

Create a new migration::

    python -m migrations.migrate --new "add_foobar_column"

Migrations live as numbered ``.sql`` files in ``migrations/versions/``.
Each migration runs inside a transaction and is recorded in the
``schema_migrations`` table so it is applied exactly once.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSIONS_DIR = Path(__file__).parent / "versions"


def _ensure_meta_table(con: sqlite3.Connection) -> None:
    """Create the ``schema_migrations`` tracking table if it doesn't exist."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version  TEXT    PRIMARY KEY,
            name     TEXT    NOT NULL,
            applied  TEXT    NOT NULL
        )
    """)


def _applied_versions(con: sqlite3.Connection) -> set[str]:
    rows = con.execute("SELECT version FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def _migration_files() -> list[tuple[str, str, Path]]:
    """Return ``(version, name, path)`` tuples sorted by version number."""
    files = sorted(VERSIONS_DIR.glob("*.sql"))
    result: list[tuple[str, str, Path]] = []
    for f in files:
        m = re.match(r"^(\d+)_(.+)\.sql$", f.name)
        if m:
            result.append((m.group(1), m.group(2), f))
    return result


def apply_migrations(db_path: str) -> list[str]:
    """Apply all pending migrations and return the list of applied version ids."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        _ensure_meta_table(con)
        applied = _applied_versions(con)
        newly_applied: list[str] = []
        for version, name, path in _migration_files():
            if version in applied:
                continue
            sql = path.read_text()
            print(f"[migrate] Applying {version}_{name} …")
            con.executescript(sql)
            con.execute(
                "INSERT INTO schema_migrations (version, name, applied) VALUES (?, ?, ?)",
                (version, name, datetime.now(timezone.utc).isoformat()),
            )
            con.commit()
            newly_applied.append(version)
        if not newly_applied:
            print("[migrate] Database is up to date.")
        return newly_applied
    finally:
        con.close()


def create_migration(name: str) -> Path:
    """Create a new empty migration file and return its path."""
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    existing = _migration_files()
    next_num = max(int(v[0]) for v in existing) + 1 if existing else 1
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    filename = f"{next_num:04d}_{slug}.sql"
    path = VERSIONS_DIR / filename
    path.write_text(f"-- Migration: {name}\n-- Created: {datetime.now(timezone.utc).isoformat()}\n\n")
    print(f"[migrate] Created {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="teb database migrations")
    parser.add_argument(
        "--db",
        default=os.getenv("DATABASE_URL", "sqlite:///teb.db").replace("sqlite:///", ""),
        help="Path to SQLite database (default: from DATABASE_URL env var)",
    )
    parser.add_argument(
        "--new",
        metavar="NAME",
        help="Create a new migration with the given name",
    )
    args = parser.parse_args()

    if args.new:
        create_migration(args.new)
    else:
        apply_migrations(args.db)


if __name__ == "__main__":
    main()
