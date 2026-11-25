"""Additive SQLite migration for Warehouse IT Monitor v0.6.0 (TCP checks)."""

from __future__ import annotations

import sqlite3


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def _add_column(connection: sqlite3.Connection, table: str, name: str, definition: str) -> bool:
    if name in _columns(connection, table):
        return False
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    return True


def apply_v060_migration(connection: sqlite3.Connection) -> list[str]:
    """Add TCP metadata columns; existing rows stay valid and read as HTTP."""
    changes: list[str] = []
    service_columns = (
        ("check_type", "TEXT NOT NULL DEFAULT 'http'"),
        ("host", "TEXT"),
        ("port", "INTEGER"),
    )
    for name, definition in service_columns:
        if _add_column(connection, "monitored_services", name, definition):
            changes.append(f"monitored_services.{name}")

    if _add_column(connection, "check_results", "check_type", "TEXT NOT NULL DEFAULT 'http'"):
        changes.append("check_results.check_type")

    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_check_results_type_checked "
        "ON check_results(check_type, checked_at DESC)"
    )
    return changes
