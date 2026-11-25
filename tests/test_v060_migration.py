from __future__ import annotations

import sqlite3

from app.database import SCHEMA, init_db
from app.migrations.v050 import apply_v050_migration


def test_v060_migration_is_additive_and_defaults_to_http(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SCHEMA)
        apply_v050_migration(connection)
        connection.execute(
            "INSERT INTO monitored_services (name, url, timeout_seconds) "
            "VALUES ('Old HTTP', 'http://127.0.0.1/healthz', 2.0)"
        )
        connection.execute(
            "INSERT INTO check_results "
            "(service_id, checked_at, status, status_code, response_time_ms, error_message) "
            "VALUES (1, '2026-11-20T10:00:00.000Z', 'UP', 200, 10.0, NULL)"
        )
        connection.commit()
    finally:
        connection.close()

    init_db(db_path)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        service_columns = {row["name"] for row in connection.execute("PRAGMA table_info(monitored_services)")}
        result_columns = {row["name"] for row in connection.execute("PRAGMA table_info(check_results)")}
        assert {"check_type", "host", "port"} <= service_columns
        assert "check_type" in result_columns

        service = connection.execute(
            "SELECT check_type, host, port FROM monitored_services"
        ).fetchone()
        assert service["check_type"] == "http"
        assert service["host"] is None
        assert service["port"] is None

        result = connection.execute(
            "SELECT check_type FROM check_results"
        ).fetchone()
        assert result["check_type"] == "http"

        counts_before = (
            connection.execute("SELECT COUNT(*) FROM monitored_services").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM check_results").fetchone()[0],
        )
    finally:
        connection.close()

    init_db(db_path)

    connection = sqlite3.connect(db_path)
    try:
        counts_after = (
            connection.execute("SELECT COUNT(*) FROM monitored_services").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM check_results").fetchone()[0],
        )
    finally:
        connection.close()
    assert counts_after == counts_before
