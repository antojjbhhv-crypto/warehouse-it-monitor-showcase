"""HTTP checks for explicitly configured, authorised services."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from app.database import get_enabled_services, record_check
from app.v050_check_engine import check_service_v050
from app.v050_policy import validate_check_policy
from app.v060_tcp import check_tcp_service, validate_tcp_config


def validate_service_config(service: dict[str, Any]) -> dict[str, Any]:
    """Validate a configured service before it is saved or checked."""
    if not isinstance(service, dict):
        raise ValueError("Each service must be a JSON object")

    name = service.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Service name must be a non-empty string")

    check_type = service.get("check_type")
    if check_type not in (None, "http", "tcp"):
        raise ValueError(f"Service '{name}' check_type must be http or tcp")

    if check_type == "tcp" or (check_type is None and "host" in service and "port" in service):
        if "url" in service:
            raise ValueError(f"Service '{name}' TCP definition must not include url")
        tcp = validate_tcp_config(service)
        return {
            "name": tcp["name"],
            "url": f"tcp://{tcp['host']}:{tcp['port']}",
            "timeout_seconds": tcp["timeout_seconds"],
            "check_type": "tcp",
            "host": tcp["host"],
            "port": tcp["port"],
        }

    url = service.get("url")
    timeout = service.get("timeout_seconds", 3.0)

    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"Service '{name}' must have a URL")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0.2 <= timeout <= 15:
        raise ValueError(f"Service '{name}' timeout_seconds must be between 0.2 and 15")

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Service '{name}' URL must use http or https and include a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"Service '{name}' URL must not contain credentials")
    if parsed.fragment:
        raise ValueError(f"Service '{name}' URL must not contain a fragment")

    return {
        "name": name.strip(),
        "url": url.strip(),
        "timeout_seconds": float(timeout),
        "check_type": "http",
        **validate_check_policy(service),
    }


def check_service(service: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible entry point; every HTTP check uses the v0.5 HTTP engine."""
    return check_service_v050(service)


def run_all_checks(db_path: str) -> list[dict[str, Any]]:
    """Check only enabled services already stored from the local config file."""
    results: list[dict[str, Any]] = []
    for service in get_enabled_services(db_path):
        if service.get("check_type") == "tcp":
            result = check_tcp_service(service)
        else:
            result = check_service_v050(service)
        result.setdefault("check_type", "http")
        result.setdefault("endpoint", service["url"])
        result.setdefault("url", service["url"])
        record_check(
            db_path,
            service_id=service["id"],
            checked_at=result["checked_at"],
            status=result["status"],
            status_code=result["status_code"],
            response_time_ms=result["response_time_ms"],
            error_message=result["error_message"],
            result_category=result["result_category"],
            severity=result["severity"],
            policy_snapshot=result["policy_snapshot"],
            check_type=result.get("check_type", "http"),
        )
        results.append(result)
    return results
