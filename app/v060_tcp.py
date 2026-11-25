"""Explicit, single-endpoint TCP health checks for v0.6.0.

This module performs one connection attempt to the configured host and port.
It does not scan port ranges, resolve service lists, or follow redirects.
"""

from __future__ import annotations

import socket
import time
from datetime import datetime, timezone
from typing import Any


def validate_tcp_config(service: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a TCP service definition."""
    if not isinstance(service, dict):
        raise ValueError("Each service must be a JSON object")

    name = service.get("name")
    host = service.get("host")
    port = service.get("port")
    timeout = service.get("timeout_seconds", 3.0)

    if not isinstance(name, str) or not name.strip():
        raise ValueError("Service name must be a non-empty string")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("TCP host must be a non-empty hostname or IP address")
    host = host.strip()
    if any(ch.isspace() for ch in host) or "://" in host or "/" in host:
        raise ValueError("TCP host must be a hostname or IP address, not a URL")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("TCP port must be an integer between 1 and 65535")
    if (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0.2 <= float(timeout) <= 15
    ):
        raise ValueError("timeout_seconds must be between 0.2 and 15")

    return {
        "name": name.strip(),
        "host": host,
        "port": port,
        "timeout_seconds": float(timeout),
        "check_type": "tcp",
    }


def _result(
    service: dict[str, Any],
    *,
    checked_at: str,
    status: str,
    elapsed_ms: float,
    category: str,
    error_message: str | None,
) -> dict[str, Any]:
    host = service["host"]
    port = service["port"]
    return {
        "service_id": service.get("id"),
        "service_name": service["name"],
        "check_type": "tcp",
        "endpoint": f"tcp://{host}:{port}",
        "host": host,
        "port": port,
        "checked_at": checked_at,
        "status": status,
        "status_code": None,
        "response_time_ms": elapsed_ms,
        "error_message": error_message,
        "result_category": category,
        "severity": "INFO" if status == "UP" else "ERROR",
        "policy_snapshot": {
            "check_type": "tcp",
            "host": host,
            "port": port,
            "timeout_seconds": float(service.get("timeout_seconds", 3.0)),
        },
    }


def check_tcp_service(service: dict[str, Any]) -> dict[str, Any]:
    """Attempt exactly one TCP connection to the explicitly configured endpoint."""
    normalized = validate_tcp_config(service)
    started = time.perf_counter()
    checked_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    status = "DOWN"
    category = "NETWORK_ERROR"
    error_message: str | None = None

    try:
        with socket.create_connection(
            (normalized["host"], normalized["port"]),
            timeout=normalized["timeout_seconds"],
        ):
            status = "UP"
            category = "TCP_CONNECTED"
    except (TimeoutError, socket.timeout):
        category = "TIMEOUT"
        error_message = "TCP connection timed out"
    except socket.gaierror as exc:
        category = "DNS_ERROR"
        error_message = f"DNS resolution failed: {str(exc)[:250]}"
    except ConnectionRefusedError as exc:
        category = "CONNECTION_REFUSED"
        error_message = f"Connection refused: {str(exc)[:250]}"
    except OSError as exc:
        category = "NETWORK_ERROR"
        error_message = f"Network error: {str(exc)[:250]}"

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return _result(
        normalized,
        checked_at=checked_at,
        status=status,
        elapsed_ms=elapsed_ms,
        category=category,
        error_message=error_message,
    )
