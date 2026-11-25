from __future__ import annotations

import sqlite3

import pytest

from app.backup import backup_database, restore_database, verify_database
from app.database import init_db, record_check, sync_services


def _make_db(path, latency: float) -> None:
    init_db(path)
    sync_services(path, [{"name": "Demo", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2.0}])
    record_check(
        path, service_id=1, checked_at="2026-11-23T10:00:00.000Z", status="UP",
        status_code=200, response_time_ms=latency, error_message=None,
    )


def _latency(path) -> float:
    with sqlite3.connect(path) as connection:
        return float(connection.execute("SELECT response_time_ms FROM check_results").fetchone()[0])


def test_backup_is_verified_and_contains_all_rows(tmp_path) -> None:
    source = tmp_path / "monitor.sqlite3"
    backup = tmp_path / "backups" / "monitor-backup.sqlite3"
    _make_db(source, 42.5)

    result = backup_database(source, backup)

    assert result["integrity"] == "ok"
    assert result["row_counts"]["check_results"] == 1
    assert _latency(backup) == 42.5
    assert verify_database(backup)["integrity"] == "ok"


def test_backup_refuses_to_overwrite_without_explicit_flag(tmp_path) -> None:
    source = tmp_path / "monitor.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    _make_db(source, 10.0)
    backup_database(source, backup)

    with pytest.raises(FileExistsError):
        backup_database(source, backup)

    backup_database(source, backup, overwrite=True)
    assert _latency(backup) == 10.0


def test_restore_replaces_database_and_keeps_safety_backup(tmp_path) -> None:
    current = tmp_path / "monitor.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    _make_db(current, 11.0)
    _make_db(replacement, 88.0)

    result = restore_database(current, replacement)

    assert _latency(current) == 88.0
    safety_path = result["safety_backup_path"]
    assert _latency(safety_path) == 11.0
    assert verify_database(current)["integrity"] == "ok"


def test_restore_invalid_backup_does_not_change_current_database(tmp_path) -> None:
    current = tmp_path / "monitor.sqlite3"
    invalid = tmp_path / "not-a-backup.sqlite3"
    _make_db(current, 11.0)
    invalid.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(ValueError):
        restore_database(current, invalid)

    assert _latency(current) == 11.0


def test_backup_and_restore_reject_same_source_and_target(tmp_path) -> None:
    database = tmp_path / "monitor.sqlite3"
    _make_db(database, 22.0)

    with pytest.raises(ValueError, match="different"):
        backup_database(database, database)
    with pytest.raises(ValueError, match="different"):
        restore_database(database, database)


def test_restore_refuses_nonempty_wal_sidecar(tmp_path) -> None:
    current = tmp_path / "monitor.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    _make_db(current, 11.0)
    _make_db(replacement, 88.0)
    (tmp_path / "monitor.sqlite3-wal").write_bytes(b"pending wal")

    with pytest.raises(RuntimeError, match="sidecar"):
        restore_database(current, replacement)

    assert _latency(current) == 11.0


def test_restore_to_new_database_path_does_not_create_safety_backup(tmp_path) -> None:
    source = tmp_path / "backup.sqlite3"
    target = tmp_path / "new" / "monitor.sqlite3"
    _make_db(source, 77.0)

    result = restore_database(target, source)

    assert _latency(target) == 77.0
    assert "safety_backup_path" not in result


def test_restore_refuses_locked_target(tmp_path) -> None:
    current = tmp_path / "monitor.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    _make_db(current, 11.0)
    _make_db(replacement, 88.0)
    connection = sqlite3.connect(current, timeout=0)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        with pytest.raises(RuntimeError, match="in use"):
            restore_database(current, replacement)
        connection.rollback()
    finally:
        connection.close()

    assert _latency(current) == 11.0
