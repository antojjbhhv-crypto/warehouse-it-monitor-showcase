import sqlite3

from app.migrations.v050 import apply_v050_migration


def test_migration_is_additive_preserves_rows_and_is_idempotent(tmp_path):
    db = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            CREATE TABLE monitored_services (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
                timeout_seconds REAL NOT NULL DEFAULT 3.0, enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE check_results (
                id INTEGER PRIMARY KEY, service_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL, status TEXT NOT NULL,
                status_code INTEGER, response_time_ms REAL NOT NULL, error_message TEXT
            );
            INSERT INTO monitored_services(id,name,url) VALUES(1,'Demo','http://127.0.0.1/healthz');
            INSERT INTO check_results(id,service_id,checked_at,status,status_code,response_time_ms)
            VALUES(1,1,'2026-11-23T00:00:00Z','UP',200,42.5);
        """)
        first = apply_v050_migration(connection)
        second = apply_v050_migration(connection)
        row = connection.execute("SELECT * FROM check_results WHERE id=1").fetchone()
        service = connection.execute("SELECT * FROM monitored_services WHERE id=1").fetchone()
        assert len(first) == 6
        assert second == []
        assert row["response_time_ms"] == 42.5
        assert row["result_category"] is None
        assert row["policy_snapshot_json"] is None
        assert service["http_method"] == "GET"
        assert service["slow_threshold_ms"] is None
    finally:
        connection.close()
