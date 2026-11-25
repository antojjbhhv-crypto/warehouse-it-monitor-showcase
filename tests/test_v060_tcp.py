import socket
from unittest.mock import MagicMock

import pytest

from app.v060_tcp import check_tcp_service, validate_tcp_config


def service(**extra):
    result = {
        "name": "Warehouse database",
        "host": "127.0.0.1",
        "port": 5432,
        "timeout_seconds": 2.0,
    }
    result.update(extra)
    return result


@pytest.mark.parametrize("payload", [
    {"host": ""},
    {"host": "https://example.com"},
    {"host": "example.com/path"},
    {"port": 0},
    {"port": 65536},
    {"port": True},
    {"port": "443"},
    {"timeout_seconds": 0.1},
    {"timeout_seconds": True},
    {"name": " "},
])
def test_invalid_tcp_config_is_rejected(payload):
    with pytest.raises(ValueError):
        validate_tcp_config(service(**payload))


def test_valid_config_is_normalized():
    assert validate_tcp_config(service(host=" example.com ", timeout_seconds=1)) == {
        "name": "Warehouse database",
        "host": "example.com",
        "port": 5432,
        "timeout_seconds": 1.0,
        "check_type": "tcp",
    }


def test_tcp_connection_success(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr("app.v060_tcp.socket.create_connection", lambda address, timeout: (
        (address == ("127.0.0.1", 5432) and timeout == 2.0) or (_ for _ in ()).throw(AssertionError()),
        connection,
    )[1])
    result = check_tcp_service(service())
    assert result["status"] == "UP"
    assert result["result_category"] == "TCP_CONNECTED"
    assert result["severity"] == "INFO"
    assert result["endpoint"] == "tcp://127.0.0.1:5432"
    connection.__enter__.assert_called_once()


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (TimeoutError("late"), "TIMEOUT"),
        (socket.gaierror(-2, "not found"), "DNS_ERROR"),
        (ConnectionRefusedError("refused"), "CONNECTION_REFUSED"),
        (OSError("network down"), "NETWORK_ERROR"),
    ],
)
def test_tcp_errors_are_classified(monkeypatch, error, category):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr("app.v060_tcp.socket.create_connection", fail)
    result = check_tcp_service(service())
    assert result["status"] == "DOWN"
    assert result["result_category"] == category
    assert result["severity"] == "ERROR"
    assert result["error_message"]
