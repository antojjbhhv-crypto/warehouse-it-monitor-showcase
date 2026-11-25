from app.database import (
    analytics_buckets, analytics_summary, init_db, list_events,
    query_check_history, record_check, sync_services,
)


def test_database_records_results_and_only_status_changes(tmp_path) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    init_db(db_path)
    sync_services(
        db_path,
        [{"name": "Demo", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2.0}],
    )

    service_id = 1
    record_check(
        db_path,
        service_id=service_id,
        checked_at="2026-11-23T10:00:00Z",
        status="UP",
        status_code=200,
        response_time_ms=12.3,
        error_message=None,
    )
    record_check(
        db_path,
        service_id=service_id,
        checked_at="2026-11-23T10:01:00Z",
        status="UP",
        status_code=200,
        response_time_ms=10.1,
        error_message=None,
    )
    record_check(
        db_path,
        service_id=service_id,
        checked_at="2026-11-23T10:02:00Z",
        status="DOWN",
        status_code=None,
        response_time_ms=2000.0,
        error_message="Connection timed out",
    )

    events = list_events(db_path)
    assert len(events) == 2
    assert events[0]["new_status"] == "DOWN"
    assert events[0]["previous_status"] == "UP"
    assert events[1]["new_status"] == "UP"
    assert events[1]["previous_status"] is None


def test_sync_services_disables_removed_config_entries(tmp_path) -> None:
    from app.database import get_enabled_services

    db_path = tmp_path / "monitor.sqlite3"
    init_db(db_path)
    first = {"name": "Service A", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2.0}
    second = {"name": "Service B", "url": "http://127.0.0.1:8002/healthz", "timeout_seconds": 2.0}

    sync_services(db_path, [first, second])
    assert len(get_enabled_services(db_path)) == 2

    sync_services(db_path, [first])
    active = get_enabled_services(db_path)
    assert len(active) == 1
    assert active[0]["name"] == "Service A"


def test_analytics_are_sample_based_and_aggregate_latency_for_up_only(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    db_path = tmp_path / "monitor.sqlite3"
    init_db(db_path)
    sync_services(db_path, [{"name": "Demo", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2.0}])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sample_data = [("UP", 200, 20.0, None), ("DOWN", None, 2000.0, "timeout"), ("UP", 200, 40.0, None)]
    for offset, (status, status_code, latency, error) in enumerate(sample_data, start=1):
        checked_at = (now - timedelta(minutes=offset)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        record_check(
            db_path, service_id=1, checked_at=checked_at, status=status,
            status_code=status_code, response_time_ms=latency, error_message=error,
        )

    start_at = (now - timedelta(hours=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    end_at = (now + timedelta(seconds=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    summary = analytics_summary(db_path, start_at=start_at, end_at=end_at)
    assert summary["total_checks"] == 3
    assert summary["up_checks"] == 2
    assert summary["down_checks"] == 1
    assert summary["availability_pct"] == 66.67
    assert summary["avg_up_response_time_ms"] == 30.0
    buckets = analytics_buckets(db_path, start_at=start_at, end_at=end_at, bucket_seconds=3600)
    assert sum(bucket["total_checks"] for bucket in buckets) == 3


def test_history_query_filters_status_and_paginates(tmp_path) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    init_db(db_path)
    sync_services(db_path, [{"name": "Demo", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2.0}])
    for idx, status in enumerate(["UP", "DOWN", "UP"]):
        record_check(
            db_path, service_id=1, checked_at=f"2026-11-23T10:0{idx}:00.000Z",
            status=status, status_code=200 if status == "UP" else None,
            response_time_ms=10.0, error_message=None if status == "UP" else "timeout",
        )
    items, total = query_check_history(db_path, status="UP", limit=1, offset=0)
    assert total == 2
    assert len(items) == 1
    assert items[0]["status"] == "UP"
    assert query_check_history(db_path, status="DOWN", limit=10, offset=0)[1] == 1


def test_init_db_adds_time_index_without_rebuilding_existing_tables(tmp_path) -> None:
    from app.database import connect

    db_path = tmp_path / "monitor.sqlite3"
    init_db(db_path)
    with connect(db_path) as connection:
        connection.execute("DROP INDEX IF EXISTS idx_check_results_checked")
    init_db(db_path)
    with connect(db_path) as connection:
        indexes = {row["name"] for row in connection.execute("PRAGMA index_list(check_results)").fetchall()}
    assert "idx_check_results_checked" in indexes
