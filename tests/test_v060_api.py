from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.database import record_check
from app.main import create_app


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def test_history_filters_by_check_type(tmp_path) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    config_path = tmp_path / "services.json"
    config_path.write_text(
        json.dumps(
            {
                "services": [
                    {"name": "Web", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2.0},
                    {
                        "name": "DB",
                        "check_type": "tcp",
                        "host": "127.0.0.1",
                        "port": 5432,
                        "timeout_seconds": 1.0,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    application = create_app(db_path=db_path, services_path=config_path)
    with TestClient(application) as client:
        services = {item["name"]: item for item in client.get("/api/services").json()}
        assert services["DB"]["check_type"] == "tcp"
        assert services["DB"]["url"] == "tcp://127.0.0.1:5432"
        assert services["Web"]["check_type"] == "http"

        checked_at = _utc_now()
        record_check(
            db_path,
            service_id=services["Web"]["id"],
            checked_at=checked_at,
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
            service_id=services["DB"]["id"],
            checked_at=checked_at,
            status="DOWN",
            status_code=None,
            response_time_ms=500.0,
            error_message="Connection refused",
            result_category="CONNECTION_REFUSED",
            severity="ERROR",
            check_type="tcp",
        )

        all_items = client.get("/api/history?period=24h").json()
        assert all_items["total"] == 2
        assert {item["check_type"] for item in all_items["items"]} == {"http", "tcp"}

        tcp_items = client.get("/api/history?period=24h&check_type=tcp").json()
        assert tcp_items["total"] == 1
        assert tcp_items["items"][0]["check_type"] == "tcp"
        assert tcp_items["items"][0]["result_category"] == "CONNECTION_REFUSED"

        invalid = client.get("/api/history?period=24h&check_type=icmp")
        assert invalid.status_code == 422


def test_dashboard_marks_tcp_services(tmp_path) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    config_path = tmp_path / "services.json"
    config_path.write_text(
        json.dumps(
            {
                "services": [
                    {"name": "Web", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2.0},
                    {
                        "name": "DB",
                        "check_type": "tcp",
                        "host": "127.0.0.1",
                        "port": 5432,
                        "timeout_seconds": 1.0,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    application = create_app(db_path=db_path, services_path=config_path)
    with TestClient(application) as client:
        html = client.get("/").text

    assert "<th>Typ</th>" in html
    assert "<td>TCP</td>" in html
    assert "tcp://127.0.0.1:5432" in html
    assert 'id="history-type"' in html


def test_checks_run_response_normalizes_fields(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    config_path = tmp_path / "services.json"
    config_path.write_text(
        json.dumps(
            {
                "scheduler": {"enabled": False, "interval_seconds": 60},
                "services": [
                    {"name": "Web", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2.0},
                    {
                        "name": "DB",
                        "check_type": "tcp",
                        "host": "127.0.0.1",
                        "port": 5432,
                        "timeout_seconds": 1.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    import app.monitor as monitor

    checked_at = _utc_now()

    def fake_http(service: dict) -> dict:
        # Mirrors the v0.5 engine: url only, no check_type/endpoint.
        return {
            "service_id": service.get("id"),
            "service_name": service["name"],
            "url": service["url"],
            "checked_at": checked_at,
            "status": "UP",
            "status_code": 200,
            "response_time_ms": 10.0,
            "error_message": None,
            "result_category": "OK",
            "severity": "INFO",
            "policy_snapshot": {"method": "GET"},
        }

    def fake_tcp(service: dict) -> dict:
        # Mirrors the v0.6 TCP engine: endpoint only, no url.
        return {
            "service_id": service.get("id"),
            "service_name": service["name"],
            "check_type": "tcp",
            "endpoint": service["url"],
            "host": service["host"],
            "port": service["port"],
            "checked_at": checked_at,
            "status": "UP",
            "status_code": None,
            "response_time_ms": 5.0,
            "error_message": None,
            "result_category": "TCP_CONNECTED",
            "severity": "INFO",
            "policy_snapshot": {"check_type": "tcp"},
        }

    monkeypatch.setattr(monitor, "check_service_v050", fake_http)
    monkeypatch.setattr(monitor, "check_tcp_service", fake_tcp)

    application = create_app(db_path=db_path, services_path=config_path)
    with TestClient(application) as client:
        response = client.post("/api/checks/run")
        assert response.status_code == 200
        results = {item["service_name"]: item for item in response.json()["results"]}

    http_item = results["Web"]
    tcp_item = results["DB"]
    for item in (http_item, tcp_item):
        assert item["check_type"] in {"http", "tcp"}
        assert item["endpoint"]
        assert item["url"]

    assert http_item["check_type"] == "http"
    assert http_item["endpoint"] == http_item["url"] == "http://127.0.0.1:8001/healthz"
    assert tcp_item["check_type"] == "tcp"
    assert tcp_item["endpoint"] == tcp_item["url"] == "tcp://127.0.0.1:5432"


