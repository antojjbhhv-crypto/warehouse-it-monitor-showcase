"""HTTP check engine with explicit policy handling and stable error categories.

Integrate by delegating the existing ``check_service`` function to
``check_service_v050``. No discovery, network scanning, or redirect following is
performed; only the configured URL is requested.
"""

from __future__ import annotations

import errno
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.v050_policy import policy_snapshot


class NoRedirectHandler(HTTPRedirectHandler):
    """Do not follow redirects to an address that was not explicitly configured."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _reason(exc: BaseException) -> BaseException:
    if isinstance(exc, URLError) and isinstance(getattr(exc, "reason", None), BaseException):
        return exc.reason
    return exc


def classify_network_exception(exc: BaseException) -> tuple[str, str]:
    """Map common transport exceptions into stable, UI/API-safe categories."""
    reason = _reason(exc)
    message = str(reason).strip() or reason.__class__.__name__
    message = message[:300]

    if isinstance(reason, (TimeoutError, socket.timeout)) or getattr(reason, "errno", None) == errno.ETIMEDOUT:
        return "TIMEOUT", "Connection timed out"
    if isinstance(reason, ssl.SSLError):
        return "TLS_ERROR", f"TLS error: {message}"
    if isinstance(reason, socket.gaierror):
        return "DNS_ERROR", f"DNS resolution failed: {message}"

    error_number = getattr(reason, "errno", None)
    if isinstance(reason, ConnectionRefusedError) or error_number == errno.ECONNREFUSED:
        return "CONNECTION_REFUSED", f"Connection refused: {message}"
    if isinstance(reason, (ConnectionResetError, BrokenPipeError)) or error_number in {
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.EPIPE,
    }:
        return "CONNECTION_RESET", f"Connection reset: {message}"
    if isinstance(reason, OSError):
        return "NETWORK_ERROR", f"Network error: {message}"
    return "UNKNOWN_ERROR", f"Unexpected check error: {message}"


def _result(
    service: dict[str, Any],
    *,
    checked_at: str,
    status: str,
    status_code: int | None,
    elapsed_ms: float,
    error_message: str | None,
    result_category: str,
    severity: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "service_id": service.get("id"),
        "service_name": service["name"],
        "url": service["url"],
        "checked_at": checked_at,
        "status": status,
        "status_code": status_code,
        "response_time_ms": elapsed_ms,
        "error_message": error_message,
        "result_category": result_category,
        "severity": severity,
        "policy_snapshot": snapshot,
    }


def check_service_v050(service: dict[str, Any]) -> dict[str, Any]:
    """Check one explicitly configured URL and return classified result metadata.

    A response is UP exactly when its status code is in the configured allow-list.
    A slow but otherwise successful response remains UP and is marked WARNING /
    SLOW_RESPONSE; this does not alter availability semantics.
    """
    snapshot = policy_snapshot(service)
    started = time.perf_counter()
    checked_at = _utc_now()
    status_code: int | None = None
    error_message: str | None = None
    category = "UNKNOWN_ERROR"
    severity = "ERROR"
    status = "DOWN"

    try:
        request = Request(
            service["url"],
            headers={
                "User-Agent": "WarehouseITMonitor/0.6.0",
                "Accept": "application/json, text/plain, */*",
            },
            method=snapshot["method"],
        )
        opener = build_opener(NoRedirectHandler())
        with opener.open(request, timeout=float(service.get("timeout_seconds", 3.0))) as response:
            status_code = int(response.status)
            # HEAD has no response body. GET reads at most 1 KiB.
            if snapshot["method"] == "GET":
                response.read(1024)

        status = "UP" if status_code in snapshot["expected_status_codes"] else "DOWN"
        if status == "UP":
            category, severity = "OK", "INFO"
        else:
            category, severity = "HTTP_STATUS", "ERROR"
            error_message = f"HTTP {status_code} is not in expected_status_codes"

    except HTTPError as exc:
        # urllib raises HTTPError for non-followed redirects and HTTP error codes.
        status_code = int(exc.code)
        status = "UP" if status_code in snapshot["expected_status_codes"] else "DOWN"
        if status == "UP":
            category, severity = "OK", "INFO"
        else:
            category, severity = "HTTP_STATUS", "ERROR"
            error_message = f"HTTP {status_code} is not in expected_status_codes"
        try:
            exc.close()
        except Exception:
            pass
    except Exception as exc:  # Keep one failed check from terminating the scheduler.
        category, error_message = classify_network_exception(exc)
        status, severity = "DOWN", "ERROR"

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    threshold = snapshot["slow_threshold_ms"]
    if status == "UP" and threshold is not None and elapsed_ms > threshold:
        category, severity = "SLOW_RESPONSE", "WARNING"

    return _result(
        service,
        checked_at=checked_at,
        status=status,
        status_code=status_code,
        elapsed_ms=elapsed_ms,
        error_message=error_message,
        result_category=category,
        severity=severity,
        snapshot=snapshot,
    )
