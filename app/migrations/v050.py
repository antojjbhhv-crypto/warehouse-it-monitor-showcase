"""Additive SQLite migration for Warehouse IT Monitor v0.5.0.

Call ``apply_v050_migration(connection)`` from the existing ``init_db`` after the
v0.4.2 base schema has been created. This module does not open or close the DB
connection, so it preserves the project's v0.4.2 ``open_db()`` lifecycle.
"""

from __future__ import annotations

import sqlite3


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _add_column(connection: sqlite3.Connection, table: str, name: str, definition: str) -> bool:
    if name in _columns(connection, table):
        return False
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    return True


def apply_v050_migration(connection: sqlite3.Connection) -> list[str]:
    """Apply v0.5.0 columns/indexes exactly once; old rows remain NULL/unclassified."""
    changes: list[str] = []
    service_columns = (
        ("http_method", "TEXT NOT NULL DEFAULT 'GET'"),
        ("expected_status_codes_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("slow_threshold_ms", "REAL"),
    )
    result_columns = (
        ("result_category", "TEXT"),
        ("severity", "TEXT"),
        ("policy_snapshot_json", "TEXT"),
    )
    for name, definition in service_columns:
        if _add_column(connection, "monitored_services", name, definition):
            changes.append(f"monitored_services.{name}")
    for name, definition in result_columns:
        if _add_column(connection, "check_results", name, definition):
            changes.append(f"check_results.{name}")

    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_check_results_category_checked "
        "ON check_results(result_category, checked_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_check_results_severity_checked "
        "ON check_results(severity, checked_at DESC)"
    )
    return changes
