from __future__ import annotations

from app.database import (
    get_enabled_services,
    init_db,
    list_services,
    query_check_history,
    record_check,
    sync_services,
)


def _services() -> list[dict]:
    return [
        {
            "name": "Web",
            "url": "http://127.0.0.1:8001/healthz",
            "timeout_seconds": 2.0,
            "check_type": "http",
            "method": "GET",
            "expected_status_codes": [200],
            "slow_threshold_ms": None,
        },
        {
            "name": "DB",
            "url": "tcp://127.0.0.1:5432",
            "timeout_seconds": 1.0,
            "check_type": "tcp",
            "host": "127.0.0.1",
            "port": 5432,
        },
    ]


def test_sync_stores_tcp_fields_and_history_filters_by_type(tmp_path) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    init_db(db_path)
    sync_services(db_path, _services())

    enabled = {item["name"]: item for item in get_enabled_services(db_path)}
    assert enabled["Web"]["check_type"] == "http"
    assert enabled["DB"]["check_type"] == "tcp"
    assert enabled["DB"]["host"] == "127.0.0.1"
    assert enabled["DB"]["port"] == 5432
    assert enabled["DB"]["url"] == "tcp://127.0.0.1:5432"

    record_check(
        db_path,
        service_id=enabled["Web"]["id"],
        checked_at="2026-11-23T10:00:00.000Z",
        status="UP",
        status_code=200,
        response_time_ms=10.0,
        error_message=None,
        result_category="OK",
        severity="INFO",
        check_type="http",
    )
    record_check(
        db_path,
        service_id=enabled["DB"]["id"],
        checked_at="2026-11-23T10:01:00.000Z",
        status="DOWN",
        status_code=None,
        response_time_ms=500.0,
        error_message="Connection refused",
        result_category="CONNECTION_REFUSED",
        severity="ERROR",
        check_type="tcp",
    )

    items, total = query_check_history(db_path)
    assert total == 2
    assert {item["check_type"] for item in items} == {"http", "tcp"}

    tcp_items, tcp_total = query_check_history(db_path, check_type="tcp")
    assert tcp_total == 1
    assert tcp_items[0]["check_type"] == "tcp"
    assert tcp_items[0]["result_category"] == "CONNECTION_REFUSED"

    services = {item["name"]: item for item in list_services(db_path)}
    assert services["DB"]["check_type"] == "tcp"
    assert services["DB"]["port"] == 5432
    assert services["Web"]["check_type"] == "http"


def test_record_check_defaults_to_http_for_legacy_calls(tmp_path) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    init_db(db_path)
    sync_services(db_path, _services())

    record_check(
        db_path,
        service_id=1,
        checked_at="2026-11-23T10:00:00.000Z",
        status="UP",
        status_code=200,
        response_time_ms=10.0,
        error_message=None,
    )

    items, total = query_check_history(db_path, check_type="http")
    assert total == 1
    assert items[0]["check_type"] == "http"
