"""Policy validation and snapshots for Warehouse IT Monitor v0.5.0.

This module is intentionally independent from the application's database layer so
it can be integrated into v0.4.2 without replacing its SQLite connection helper.
"""

from __future__ import annotations

from typing import Any

DEFAULT_EXPECTED_STATUS_CODES = tuple(range(200, 400))
ALLOWED_HTTP_METHODS = frozenset({"GET", "HEAD"})
MAX_EXPECTED_STATUS_CODES = 500
MAX_SLOW_THRESHOLD_MS = 60_000.0


def validate_check_policy(service: dict[str, Any]) -> dict[str, Any]:
    """Return normalized policy values while preserving v0.4.x defaults.

    Backward compatibility:
    - missing ``method`` means GET;
    - missing ``expected_status_codes`` means every 2xx/3xx status, matching
      the old monitor's 200 <= status < 400 behaviour;
    - missing ``slow_threshold_ms`` disables the warning threshold.
    """
    method = service.get("method", "GET")
    if not isinstance(method, str):
        raise ValueError("method must be GET or HEAD")
    method = method.strip().upper()
    if method not in ALLOWED_HTTP_METHODS:
        raise ValueError("method must be GET or HEAD")

    raw_codes = service.get("expected_status_codes")
    if raw_codes is None:
        codes = list(DEFAULT_EXPECTED_STATUS_CODES)
    else:
        if not isinstance(raw_codes, list) or not raw_codes:
            raise ValueError("expected_status_codes must be a non-empty array of HTTP status codes")
        if len(raw_codes) > MAX_EXPECTED_STATUS_CODES:
            raise ValueError(f"expected_status_codes cannot contain more than {MAX_EXPECTED_STATUS_CODES} entries")
        if any(
            not isinstance(code, int)
            or isinstance(code, bool)
            or not 100 <= code <= 599
            for code in raw_codes
        ):
            raise ValueError("expected_status_codes entries must be integers between 100 and 599")
        if len(set(raw_codes)) != len(raw_codes):
            raise ValueError("expected_status_codes must not contain duplicates")
        codes = sorted(raw_codes)

    threshold = service.get("slow_threshold_ms")
    if threshold is not None:
        if (
            not isinstance(threshold, (int, float))
            or isinstance(threshold, bool)
            or not 1 <= float(threshold) <= MAX_SLOW_THRESHOLD_MS
        ):
            raise ValueError(f"slow_threshold_ms must be null or a number between 1 and {int(MAX_SLOW_THRESHOLD_MS)}")
        threshold = float(threshold)

    return {
        "method": method,
        "expected_status_codes": codes,
        "slow_threshold_ms": threshold,
    }


def policy_snapshot(service: dict[str, Any]) -> dict[str, Any]:
    """Return the exact effective policy used for one check."""
    policy = validate_check_policy(service)
    return {
        "method": policy["method"],
        "expected_status_codes": list(policy["expected_status_codes"]),
        "slow_threshold_ms": policy["slow_threshold_ms"],
    }
