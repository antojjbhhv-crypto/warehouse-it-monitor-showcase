from __future__ import annotations

import json

import pytest

from app.main import load_services
from app.monitor import validate_service_config


def test_validate_accepts_tcp_service() -> None:
    service = validate_service_config(
        {"name": "DB", "host": "127.0.0.1", "port": 5432, "timeout_seconds": 1.5}
    )
    assert service == {
        "name": "DB",
        "url": "tcp://127.0.0.1:5432",
        "timeout_seconds": 1.5,
        "check_type": "tcp",
        "host": "127.0.0.1",
        "port": 5432,
    }


def test_validate_tcp_rejects_url_and_bad_port() -> None:
    with pytest.raises(ValueError):
        validate_service_config(
            {"name": "DB", "host": "127.0.0.1", "port": 5432, "url": "http://127.0.0.1/"}
        )
    with pytest.raises(ValueError):
        validate_service_config({"name": "DB", "host": "127.0.0.1", "port": 0})


def test_validate_rejects_unknown_check_type() -> None:
    with pytest.raises(ValueError):
        validate_service_config(
            {"name": "X", "url": "http://127.0.0.1/healthz", "check_type": "icmp"}
        )


def test_load_services_accepts_http_and_tcp(tmp_path) -> None:
    path = tmp_path / "services.json"
    path.write_text(
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

    services = load_services(path)

    assert [item["check_type"] for item in services] == ["http", "tcp"]
    assert services[1]["url"] == "tcp://127.0.0.1:5432"


def test_load_services_rejects_duplicate_tcp_endpoints(tmp_path) -> None:
    path = tmp_path / "services.json"
    path.write_text(
        json.dumps(
            {
                "services": [
                    {"name": "A", "check_type": "tcp", "host": "127.0.0.1", "port": 5432},
                    {"name": "B", "check_type": "tcp", "host": "127.0.0.1", "port": 5432},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_services(path)
