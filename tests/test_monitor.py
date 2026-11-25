from __future__ import annotations

from urllib.error import URLError

import pytest

from app.monitor import check_service, validate_service_config


def test_validate_accepts_http_service() -> None:
    service = validate_service_config(
        {"name": "Demo", "url": "http://127.0.0.1:8001/healthz", "timeout_seconds": 2}
    )
    assert service == {
        "name": "Demo",
        "url": "http://127.0.0.1:8001/healthz",
        "timeout_seconds": 2.0,
        "method": "GET",
        "expected_status_codes": list(range(200, 400)),
        "slow_threshold_ms": None,
        "check_type": "http",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "url": "http://127.0.0.1/healthz"},
        {"name": "Bad scheme", "url": "file:///etc/passwd"},
        {"name": "No host", "url": "https:///healthz"},
        {"name": "Credentials", "url": "http://user:pass@example.com/healthz"},
        {"name": "Fragment", "url": "http://127.0.0.1/healthz#fragment"},
        {"name": "Timeout", "url": "http://127.0.0.1/healthz", "timeout_seconds": 0.1},
    ],
)
def test_validate_rejects_invalid_config(payload: dict) -> None:
    with pytest.raises(ValueError):
        validate_service_config(payload)


def test_check_service_reports_up(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            return b"ok"

    class FakeOpener:
        def open(self, _request, timeout):
            assert timeout == 2.0
            return FakeResponse()

    monkeypatch.setattr("app.v050_check_engine.build_opener", lambda *_args: FakeOpener())
    result = check_service(
        {"id": 1, "name": "Demo", "url": "http://127.0.0.1/healthz", "timeout_seconds": 2.0}
    )
    assert result["status"] == "UP"
    assert result["status_code"] == 200
    assert result["error_message"] is None
    assert result["result_category"] == "OK"
    assert result["severity"] == "INFO"


def test_check_service_reports_down_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeOpener:
        def open(self, *_args, **_kwargs):
            raise URLError(ConnectionRefusedError(10061, "Connection refused"))

    monkeypatch.setattr("app.v050_check_engine.build_opener", lambda *_args: FakeOpener())
    result = check_service(
        {"id": 1, "name": "Demo", "url": "http://127.0.0.1/healthz", "timeout_seconds": 2.0}
    )
    assert result["status"] == "DOWN"
    assert result["result_category"] == "CONNECTION_REFUSED"
    assert result["severity"] == "ERROR"
    assert "refused" in result["error_message"].lower()
