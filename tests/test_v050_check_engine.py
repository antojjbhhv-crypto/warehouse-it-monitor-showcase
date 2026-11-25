import socket
import ssl
from urllib.error import HTTPError, URLError

from app.v050_check_engine import check_service_v050, classify_network_exception


class FakeResponse:
    def __init__(self, status=200):
        self.status = status
        self.read_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, amount):
        self.read_calls += 1
        assert amount == 1024
        return b"ok"


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None

    def open(self, request, timeout):
        self.request = request
        assert timeout == 2.0
        if self.error:
            raise self.error
        return self.response


def _service(**extra):
    base = {
        "id": 1,
        "name": "Demo",
        "url": "http://127.0.0.1:8001/healthz",
        "timeout_seconds": 2.0,
    }
    base.update(extra)
    return base


def test_successful_get_is_up_and_records_policy_snapshot(monkeypatch):
    response = FakeResponse(200)
    opener = FakeOpener(response=response)
    monkeypatch.setattr("app.v050_check_engine.build_opener", lambda *_: opener)
    result = check_service_v050(_service())
    assert result["status"] == "UP"
    assert result["result_category"] == "OK"
    assert result["severity"] == "INFO"
    assert result["policy_snapshot"]["method"] == "GET"
    assert response.read_calls == 1


def test_head_does_not_read_response_body(monkeypatch):
    response = FakeResponse(204)
    opener = FakeOpener(response=response)
    monkeypatch.setattr("app.v050_check_engine.build_opener", lambda *_: opener)
    result = check_service_v050(_service(method="HEAD", expected_status_codes=[204]))
    assert result["status"] == "UP"
    assert opener.request.get_method() == "HEAD"
    assert response.read_calls == 0


def test_slow_success_remains_up_and_is_warning(monkeypatch):
    opener = FakeOpener(response=FakeResponse(200))
    ticks = iter([1.0, 1.250])
    monkeypatch.setattr("app.v050_check_engine.build_opener", lambda *_: opener)
    monkeypatch.setattr("app.v050_check_engine.time.perf_counter", lambda: next(ticks))
    result = check_service_v050(_service(slow_threshold_ms=100))
    assert result["status"] == "UP"
    assert result["result_category"] == "SLOW_RESPONSE"
    assert result["severity"] == "WARNING"
    assert result["response_time_ms"] == 250.0


def test_unexpected_http_status_is_down(monkeypatch):
    opener = FakeOpener(response=FakeResponse(503))
    monkeypatch.setattr("app.v050_check_engine.build_opener", lambda *_: opener)
    result = check_service_v050(_service(expected_status_codes=[200, 204]))
    assert result["status"] == "DOWN"
    assert result["result_category"] == "HTTP_STATUS"
    assert result["severity"] == "ERROR"
    assert result["status_code"] == 503


def test_http_error_uses_expected_status_policy(monkeypatch):
    opener = FakeOpener(error=HTTPError("http://local/", 503, "Unavailable", {}, None))
    monkeypatch.setattr("app.v050_check_engine.build_opener", lambda *_: opener)
    result = check_service_v050(_service(expected_status_codes=[200, 503]))
    assert result["status"] == "UP"
    assert result["result_category"] == "OK"


def test_dns_and_timeout_errors_are_classified(monkeypatch):
    opener = FakeOpener(error=URLError(socket.gaierror(-2, "name or service not known")))
    monkeypatch.setattr("app.v050_check_engine.build_opener", lambda *_: opener)
    result = check_service_v050(_service())
    assert result["status"] == "DOWN"
    assert result["result_category"] == "DNS_ERROR"

    opener = FakeOpener(error=TimeoutError("timed out"))
    monkeypatch.setattr("app.v050_check_engine.build_opener", lambda *_: opener)
    result = check_service_v050(_service())
    assert result["result_category"] == "TIMEOUT"


def test_transport_classifier_handles_refused_reset_and_tls():
    assert classify_network_exception(ConnectionRefusedError("refused"))[0] == "CONNECTION_REFUSED"
    assert classify_network_exception(ConnectionResetError("reset"))[0] == "CONNECTION_RESET"
    assert classify_network_exception(ssl.SSLError("certificate failure"))[0] == "TLS_ERROR"
