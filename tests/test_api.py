import json

from fastapi.testclient import TestClient

from app.main import create_app


def test_dashboard_and_api_routes(tmp_path) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    config_path = tmp_path / "services.json"
    config_path.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "name": "Warehouse Demo Service",
                        "url": "http://127.0.0.1:8001/healthz",
                        "timeout_seconds": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    app = create_app(db_path=db_path, services_path=config_path)
    with TestClient(app) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Warehouse IT Monitor" in dashboard.text
        assert '<html lang="de">' in dashboard.text
        assert "Konfigurierte Dienste" in dashboard.text
        assert "Statusänderungen" in dashboard.text
        assert "alle abgeschlossenen Prüfungen" in dashboard.text
        assert "Dienste-HTTP-Fehler:" in dashboard.text
        assert "all completed checks" not in dashboard.text
        assert "Services HTTP " not in dashboard.text
        assert "Warehouse Demo Service" in dashboard.text

        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        services = client.get("/api/services")
        assert services.status_code == 200
        assert len(services.json()) == 1
        assert services.json()[0]["name"] == "Warehouse Demo Service"

        events = client.get("/api/events")
        assert events.status_code == 200
        assert events.json() == []

        scheduler = client.get("/api/scheduler")
        assert scheduler.status_code == 200
        assert scheduler.json()["enabled"] is True
        assert scheduler.json()["interval_seconds"] == 60
        assert scheduler.json()["next_check_at"] is not None
        assert "Nächste Prüfung:" in dashboard.text
        assert "v0.7.0" in dashboard.text
        assert "Analysen" in dashboard.text
        assert "Prüfungsverlauf" in dashboard.text


def test_manual_check_route_updates_scheduler_status_without_resetting_schedule(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    config_path = tmp_path / "services.json"
    config_path.write_text(json.dumps({"services": [], "scheduler": {"enabled": True, "interval_seconds": 60}}), encoding="utf-8")
    monkeypatch.setattr("app.main.run_all_checks", lambda _db_path: [])

    app = create_app(db_path=db_path, services_path=config_path)
    with TestClient(app) as client:
        before = client.get("/api/scheduler").json()
        result = client.post("/api/checks/run")
        after = client.get("/api/scheduler").json()

        assert result.status_code == 200
        assert result.json()["checked"] == 0
        assert after["last_manual_check_at"] is not None
        assert after["next_check_at"] == before["next_check_at"]
        assert after["last_auto_check_at"] is None


def test_analytics_and_history_api_filters(tmp_path) -> None:
    from datetime import datetime, timedelta, timezone

    from app.database import init_db, record_check, sync_services

    db_path = tmp_path / "monitor.sqlite3"
    config_path = tmp_path / "services.json"
    config_path.write_text(
        json.dumps({"services": [{"name": "Demo", "url": "http://127.0.0.1:8001/healthz"}], "scheduler": {"enabled": False}}),
        encoding="utf-8",
    )
    init_db(db_path)
    sync_services(db_path, [{"name": "Demo", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2.0}])
    now = datetime.now(timezone.utc).replace(microsecond=0)
    samples = [("UP", 200, 20.0, None), ("DOWN", None, 2000.0, "Connection timed out"), ("UP", 200, 40.0, None)]
    for offset, (status, status_code, latency, error) in enumerate(samples, start=1):
        checked_at = (now - timedelta(minutes=offset)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        record_check(
            db_path, service_id=1, checked_at=checked_at, status=status,
            status_code=status_code, response_time_ms=latency, error_message=error,
        )

    app = create_app(db_path=db_path, services_path=config_path)
    with TestClient(app) as client:
        analytics = client.get("/api/analytics", params={"period": "24h", "service_id": 1})
        assert analytics.status_code == 200
        payload = analytics.json()
        assert payload["method"] == "sample_based"
        assert payload["summary"]["total_checks"] == 3
        assert payload["summary"]["up_checks"] == 2
        assert payload["summary"]["down_checks"] == 1
        assert payload["summary"]["availability_pct"] == 66.67
        assert payload["summary"]["avg_up_response_time_ms"] == 30.0
        assert sum(bucket["total_checks"] for bucket in payload["buckets"]) == 3

        history = client.get("/api/history", params={"period": "24h", "service_id": 1, "status": "DOWN"})
        assert history.status_code == 200
        assert history.json()["total"] == 1
        assert history.json()["items"][0]["error_message"] == "Connection timed out"

        bad_period = client.get("/api/analytics", params={"period": "year"})
        assert bad_period.status_code == 422
        bad_status = client.get("/api/history", params={"status": "FAILED"})
        assert bad_status.status_code == 422


def test_app_starts_with_utf8_bom_config_in_v04(tmp_path) -> None:
    db_path = tmp_path / "monitor.sqlite3"
    config_path = tmp_path / "services.json"
    payload = {
        "services": [],
        "scheduler": {"enabled": False, "interval_seconds": 60},
    }
    config_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))

    app = create_app(db_path=db_path, services_path=config_path)
    with TestClient(app) as client:
        dashboard = client.get("/")
        analytics = client.get("/api/analytics", params={"period": "24h"})
        assert dashboard.status_code == 200
        assert "v0.7.0" in dashboard.text
        assert analytics.status_code == 200
        assert analytics.json()["summary"]["availability_pct"] is None
