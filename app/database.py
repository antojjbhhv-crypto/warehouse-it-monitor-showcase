"""SQLite persistence for configured services, check results and status events."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.migrations.v050 import apply_v050_migration
from app.migrations.v060 import apply_v060_migration
from app.migrations.v070 import apply_v070_migration

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS monitored_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    timeout_seconds REAL NOT NULL DEFAULT 3.0,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES monitored_services(id) ON DELETE CASCADE,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('UP', 'DOWN')),
    status_code INTEGER,
    response_time_ms REAL NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_check_results_service_checked
    ON check_results(service_id, checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_check_results_checked
    ON check_results(checked_at DESC);

CREATE TABLE IF NOT EXISTS service_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES monitored_services(id) ON DELETE CASCADE,
    occurred_at TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL CHECK (new_status IN ('UP', 'DOWN')),
    details TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_service_events_occurred
    ON service_events(occurred_at DESC);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with row access by column name."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def open_db(db_path: str | Path):
    """Commit on success and always close, so Windows releases the file handle."""
    connection = connect(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db(db_path: str | Path) -> None:
    with open_db(db_path) as connection:
        connection.executescript(SCHEMA)
        apply_v050_migration(connection)
        apply_v060_migration(connection)
        apply_v070_migration(connection)


def sync_services(db_path: str | Path, services: list[dict[str, Any]]) -> None:
    """Upsert only services supplied in the trusted local JSON configuration."""
    with open_db(db_path) as connection:
        # A removed entry in services.json must no longer be monitored.
        connection.execute("UPDATE monitored_services SET enabled = 0")
        for service in services:
            codes_json = json.dumps(
                service.get("expected_status_codes", list(range(200, 400))),
                separators=(",", ":"),
            )
            connection.execute(
                """INSERT INTO monitored_services
                   (name, url, timeout_seconds, http_method,
                    expected_status_codes_json, slow_threshold_ms,
                    check_type, host, port, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(url) DO UPDATE SET
                       name = excluded.name,
                       timeout_seconds = excluded.timeout_seconds,
                       http_method = excluded.http_method,
                       expected_status_codes_json = excluded.expected_status_codes_json,
                       slow_threshold_ms = excluded.slow_threshold_ms,
                       check_type = excluded.check_type,
                       host = excluded.host,
                       port = excluded.port,
                       enabled = 1""",
                (
                    service["name"],
                    service["url"],
                    service["timeout_seconds"],
                    service.get("method", "GET"),
                    codes_json,
                    service.get("slow_threshold_ms"),
                    service.get("check_type", "http"),
                    service.get("host"),
                    service.get("port"),
                ),
            )


def list_services(db_path: str | Path) -> list[dict[str, Any]]:
    query = """
    SELECT s.id, s.name, s.url, s.timeout_seconds, s.enabled,
           s.check_type, s.host, s.port,
           r.status AS last_status, r.checked_at AS last_checked_at,
           r.response_time_ms AS last_response_time_ms,
           r.error_message AS last_error_message,
           r.result_category AS last_result_category,
           r.severity AS last_severity
    FROM monitored_services s
    LEFT JOIN check_results r ON r.id = (
        SELECT id FROM check_results
        WHERE service_id = s.id
        ORDER BY checked_at DESC, id DESC LIMIT 1
    )
    WHERE s.enabled = 1
    ORDER BY s.id
    """
    with open_db(db_path) as connection:
        rows = connection.execute(query).fetchall()
    return [dict(row) for row in rows]


def get_enabled_services(db_path: str | Path) -> list[dict[str, Any]]:
    with open_db(db_path) as connection:
        rows = connection.execute(
            """SELECT id, name, url, timeout_seconds,
                      http_method, expected_status_codes_json, slow_threshold_ms,
                      check_type, host, port
               FROM monitored_services WHERE enabled = 1 ORDER BY id"""
        ).fetchall()
    services: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["method"] = item.pop("http_method") or "GET"
        raw_codes = item.pop("expected_status_codes_json")
        item["expected_status_codes"] = (
            json.loads(raw_codes) if raw_codes and raw_codes != "[]" else list(range(200, 400))
        )
        services.append(item)
    return services


def record_check(
    db_path: str | Path,
    *,
    service_id: int,
    checked_at: str,
    status: str,
    status_code: int | None,
    response_time_ms: float,
    error_message: str | None,
    result_category: str | None = None,
    severity: str | None = None,
    policy_snapshot: dict[str, Any] | None = None,
    check_type: str = "http",
) -> None:
    """Persist one result and add an event only when the status changes."""
    with open_db(db_path) as connection:
        previous = connection.execute(
            """SELECT status FROM check_results
               WHERE service_id = ? ORDER BY checked_at DESC, id DESC LIMIT 1""",
            (service_id,),
        ).fetchone()
        previous_status = previous["status"] if previous else None

        connection.execute(
            """INSERT INTO check_results
               (service_id, checked_at, status, status_code, response_time_ms, error_message,
                result_category, severity, policy_snapshot_json, check_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                service_id,
                checked_at,
                status,
                status_code,
                response_time_ms,
                error_message,
                result_category,
                severity,
                json.dumps(policy_snapshot, separators=(",", ":"), ensure_ascii=False)
                if policy_snapshot is not None else None,
                check_type,
            ),
        )

        if previous_status != status:
            if previous_status is None:
                details = f"Anfangsstatus: {status}"
            else:
                details = f"Status geändert: {previous_status} → {status}"
            if error_message:
                details += f" ({error_message})"
            connection.execute(
                """INSERT INTO service_events
                   (service_id, occurred_at, previous_status, new_status, details)
                   VALUES (?, ?, ?, ?, ?)""",
                (service_id, checked_at, previous_status, status, details),
            )

            # Queue only real transitions (not the initial baseline), and only when
            # notifications were explicitly enabled at check time.
            if previous_status is not None and os.getenv("WITMON_TELEGRAM_ENABLED", "false").strip().lower() in {"true", "1", "yes"}:
                event_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                connection.execute(
                    "INSERT OR IGNORE INTO notification_outbox(event_id) VALUES (?)",
                    (event_id,),
                )


def list_events(db_path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    with open_db(db_path) as connection:
        rows = connection.execute(
            """SELECT e.id, s.name AS service_name, e.occurred_at,
                      e.previous_status, e.new_status, e.details
               FROM service_events e
               JOIN monitored_services s ON s.id = e.service_id
               ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def dashboard_stats(db_path: str | Path) -> dict[str, int]:
    services = list_services(db_path)
    return {
        "total": len(services),
        "up": sum(1 for item in services if item["last_status"] == "UP"),
        "down": sum(1 for item in services if item["last_status"] == "DOWN"),
        "unchecked": sum(1 for item in services if item["last_status"] is None),
    }


def query_check_history(
    db_path: str | Path,
    *,
    service_id: int | None = None,
    status: str | None = None,
    category: str | None = None,
    check_type: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return filtered check samples and the total number matching the filters."""
    clauses: list[str] = []
    parameters: list[Any] = []
    if service_id is not None:
        clauses.append("r.service_id = ?")
        parameters.append(service_id)
    if status is not None:
        clauses.append("r.status = ?")
        parameters.append(status)
    if category == "UNCLASSIFIED":
        clauses.append("r.result_category IS NULL")
    elif category is not None:
        clauses.append("r.result_category = ?")
        parameters.append(category)
    if check_type is not None:
        clauses.append("r.check_type = ?")
        parameters.append(check_type)
    if start_at is not None:
        clauses.append("r.checked_at >= ?")
        parameters.append(start_at)
    if end_at is not None:
        clauses.append("r.checked_at < ?")
        parameters.append(end_at)
    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""

    with open_db(db_path) as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM check_results r" + where_sql, parameters
        ).fetchone()[0]
        rows = connection.execute(
            """SELECT r.id, r.service_id, s.name AS service_name, s.url,
                      r.checked_at, r.status, r.status_code,
                      r.response_time_ms, r.error_message, r.check_type,
                      COALESCE(r.result_category, 'UNCLASSIFIED') AS result_category,
                      COALESCE(r.severity, 'UNCLASSIFIED') AS severity,
                      r.policy_snapshot_json
               FROM check_results r
               JOIN monitored_services s ON s.id = r.service_id
               """
            + where_sql
            + " ORDER BY r.checked_at DESC, r.id DESC LIMIT ? OFFSET ?",
            [*parameters, limit, offset],
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        raw_snapshot = item.pop("policy_snapshot_json", None)
        item["policy_snapshot"] = json.loads(raw_snapshot) if raw_snapshot else None
        items.append(item)
    return items, int(total)


def analytics_summary(
    db_path: str | Path,
    *,
    start_at: str,
    end_at: str,
    service_id: int | None = None,
) -> dict[str, Any]:
    """Summarise completed samples; availability is sample-based, not continuous uptime."""
    clauses = ["checked_at >= ?", "checked_at < ?"]
    parameters: list[Any] = [start_at, end_at]
    if service_id is not None:
        clauses.append("service_id = ?")
        parameters.append(service_id)
    where_sql = " AND ".join(clauses)
    with open_db(db_path) as connection:
        row = connection.execute(
            f"""SELECT COUNT(*) AS total_checks,
                       COALESCE(SUM(CASE WHEN status = 'UP' THEN 1 ELSE 0 END), 0) AS up_checks,
                       COALESCE(SUM(CASE WHEN status = 'DOWN' THEN 1 ELSE 0 END), 0) AS down_checks,
                       AVG(CASE WHEN status = 'UP' THEN response_time_ms END) AS avg_up_response_time_ms,
                       MIN(CASE WHEN status = 'UP' THEN response_time_ms END) AS min_up_response_time_ms,
                       MAX(CASE WHEN status = 'UP' THEN response_time_ms END) AS max_up_response_time_ms
                FROM check_results WHERE {where_sql}""",
            parameters,
        ).fetchone()
    total = int(row["total_checks"] or 0)
    up = int(row["up_checks"] or 0)
    down = int(row["down_checks"] or 0)
    return {
        "total_checks": total,
        "up_checks": up,
        "down_checks": down,
        "availability_pct": round(up / total * 100, 2) if total else None,
        "avg_up_response_time_ms": round(row["avg_up_response_time_ms"], 2)
        if row["avg_up_response_time_ms"] is not None else None,
        "min_up_response_time_ms": round(row["min_up_response_time_ms"], 2)
        if row["min_up_response_time_ms"] is not None else None,
        "max_up_response_time_ms": round(row["max_up_response_time_ms"], 2)
        if row["max_up_response_time_ms"] is not None else None,
    }


def analytics_buckets(
    db_path: str | Path,
    *,
    start_at: str,
    end_at: str,
    bucket_seconds: int,
    service_id: int | None = None,
) -> list[dict[str, Any]]:
    """Aggregate check samples into fixed UTC time buckets."""
    if not isinstance(bucket_seconds, int) or isinstance(bucket_seconds, bool) or bucket_seconds < 1:
        raise ValueError("bucket_seconds must be a positive integer")
    clauses = ["checked_at >= ?", "checked_at < ?"]
    parameters: list[Any] = [start_at, end_at]
    if service_id is not None:
        clauses.append("service_id = ?")
        parameters.append(service_id)
    where_sql = " AND ".join(clauses)
    bucket_expression = f"(CAST(strftime('%s', checked_at) AS INTEGER) / {int(bucket_seconds)}) * {int(bucket_seconds)}"
    with open_db(db_path) as connection:
        rows = connection.execute(
            f"""SELECT {bucket_expression} AS bucket_epoch,
                       COUNT(*) AS total_checks,
                       SUM(CASE WHEN status = 'UP' THEN 1 ELSE 0 END) AS up_checks,
                       SUM(CASE WHEN status = 'DOWN' THEN 1 ELSE 0 END) AS down_checks,
                       AVG(CASE WHEN status = 'UP' THEN response_time_ms END) AS avg_up_response_time_ms
                FROM check_results WHERE {where_sql}
                GROUP BY bucket_epoch ORDER BY bucket_epoch""",
            parameters,
        ).fetchall()
    return [
        {
            "bucket_epoch": int(row["bucket_epoch"]),
            "total_checks": int(row["total_checks"] or 0),
            "up_checks": int(row["up_checks"] or 0),
            "down_checks": int(row["down_checks"] or 0),
            "avg_up_response_time_ms": round(row["avg_up_response_time_ms"], 2)
            if row["avg_up_response_time_ms"] is not None else None,
        }
        for row in rows if row["bucket_epoch"] is not None
    ]



def claim_notification(db_path: str | Path) -> dict[str, Any] | None:
    """Atomically claim the oldest due notification for the single app worker."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    stale = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with open_db(db_path) as connection:
        row = connection.execute(
            """SELECT o.id, o.attempts, e.occurred_at, e.previous_status,
                      e.new_status, e.details, s.name AS service_name, s.url
               FROM notification_outbox o
               JOIN service_events e ON e.id = o.event_id
               JOIN monitored_services s ON s.id = e.service_id
               WHERE ((o.status = 'pending' AND o.next_attempt_at <= ?)
                      OR (o.status = 'sending' AND o.created_at <= ?))
               ORDER BY o.id LIMIT 1""",
            (now, stale),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """UPDATE notification_outbox
               SET status = 'sending', attempts = attempts + 1
               WHERE id = ?""",
            (row["id"],),
        )
        result = dict(row)
        result["attempts"] = int(row["attempts"]) + 1
        return result


def finish_notification(
    db_path: str | Path,
    notification_id: int,
    *,
    error: str | None,
    max_attempts: int = 5,
) -> None:
    """Mark delivery complete or schedule bounded exponential retry."""
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    with open_db(db_path) as connection:
        row = connection.execute(
            "SELECT attempts FROM notification_outbox WHERE id = ?",
            (notification_id,),
        ).fetchone()
        if row is None:
            return
        if error is None:
            connection.execute(
                """UPDATE notification_outbox
                   SET status = 'sent', sent_at = ?, last_error = NULL
                   WHERE id = ?""",
                (now, notification_id),
            )
            return
        attempts = int(row["attempts"])
        if attempts >= max_attempts:
            connection.execute(
                """UPDATE notification_outbox
                   SET status = 'failed', last_error = ?
                   WHERE id = ?""",
                (error[:500], notification_id),
            )
            return
        delays = (60, 300, 900, 3600)
        delay = delays[min(max(attempts - 1, 0), len(delays) - 1)]
        next_attempt = (now_dt + timedelta(seconds=delay)).isoformat(timespec="seconds").replace("+00:00", "Z")
        connection.execute(
            """UPDATE notification_outbox
               SET status = 'pending', next_attempt_at = ?, last_error = ?
               WHERE id = ?""",
            (next_attempt, error[:500], notification_id),
        )
