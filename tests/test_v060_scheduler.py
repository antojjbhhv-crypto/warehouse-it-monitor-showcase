from __future__ import annotations

import app.monitor as monitor
from app.database import init_db, query_check_history, sync_services


def _http_result(service: dict) -> dict:
    return {
        "service_id": service["id"],
        "service_name": service["name"],
        "url": service["url"],
        "checked_at": "2026-11-23T10:00:00.000Z",
        "status": "UP",
        "status_code": 200,
        "response_time_ms": 10.0,
        "error_message": None,
        "result_category": "OK",
        "severity": "INFO",
        "policy_snapshot": {"method": "GET"},
        "check_type": "http",
    }


def _tcp_result(service: dict) -> dict:
    return {
        "service_id": service["id"],
        "service_name": service["name"],
        "check_type": "tcp",
        "endpoint": service["url"],
        "host": service["host"],
        "port": service["port"],
        "checked_at": "2026-11-23T10:01:00.000Z",
        "status": "DOWN",
        "status_code": None,
        "response_time_ms": 500.0,
        "error_message": "Connection refused",
        "result_category": "CONNECTION_REFUSED",
        "severity": "ERROR",
        "policy_snapshot": {"check_type": "tcp", "host": service["host"], "port": service["port"]},
    }


def test_run_all_checks_routes_http_and_tcp(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    init_db(db_path)
    sync_services(
        db_path,
        [
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
        ],
    )

    calls: list[tuple[str, str]] = []

    def fake_http(service: dict) -> dict:
        calls.append(("http", service["name"]))
        return _http_result(service)

    def fake_tcp(service: dict) -> dict:
        calls.append(("tcp", service["name"]))
        return _tcp_result(service)

    monkeypatch.setattr(monitor, "check_service_v050", fake_http)
    monkeypatch.setattr(monitor, "check_tcp_service", fake_tcp)

    results = monitor.run_all_checks(db_path)

    assert sorted(calls) == [("http", "Web"), ("tcp", "DB")]
    assert {item["check_type"] for item in results} == {"http", "tcp"}

    items, total = query_check_history(db_path)
    assert total == 2
    assert {item["check_type"] for item in items} == {"http", "tcp"}

    tcp_items, tcp_total = query_check_history(db_path, check_type="tcp")
    assert tcp_total == 1
    assert tcp_items[0]["status"] == "DOWN"
    assert tcp_items[0]["result_category"] == "CONNECTION_REFUSED"
