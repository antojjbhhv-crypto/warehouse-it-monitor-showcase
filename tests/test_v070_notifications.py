from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO

import pytest

from app.database import (
    claim_notification,
    finish_notification,
    init_db,
    record_check,
    sync_services,
)
from app.notifications import TelegramSettings, _message_text, deliver_one


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _setup_db(tmp_path):
    db = tmp_path / "monitor.sqlite3"
    init_db(db)
    sync_services(
        db,
        [{"name": "Web", "url": "https://example.test/health", "timeout_seconds": 2.0}],
    )
    return db


def _record(db, status: str) -> None:
    record_check(
        db,
        service_id=1,
        checked_at=_checked_at(),
        status=status,
        status_code=200 if status == "UP" else None,
        response_time_ms=12.0,
        error_message=None if status == "UP" else "unavailable",
        result_category="OK" if status == "UP" else "NETWORK_ERROR",
        severity="INFO" if status == "UP" else "ERROR",
    )


def _settings() -> TelegramSettings:
    return TelegramSettings(
        enabled=True, token="123456:fake-token", chat_id="123456789"
    )


def test_outbox_only_contains_real_status_transitions(tmp_path, monkeypatch):
    db = _setup_db(tmp_path)
    monkeypatch.setenv("WITMON_TELEGRAM_ENABLED", "true")

    _record(db, "UP")
    assert claim_notification(db) is None

    _record(db, "DOWN")
    first = claim_notification(db)
    assert first is not None
    assert first["previous_status"] == "UP"
    assert first["new_status"] == "DOWN"
    finish_notification(db, first["id"], error=None)

    _record(db, "DOWN")
    assert claim_notification(db) is None

    _record(db, "UP")
    recovery = claim_notification(db)
    assert recovery is not None
    assert recovery["previous_status"] == "DOWN"
    assert recovery["new_status"] == "UP"


def test_notifications_disabled_does_not_enqueue(tmp_path, monkeypatch):
    db = _setup_db(tmp_path)
    monkeypatch.setenv("WITMON_TELEGRAM_ENABLED", "false")
    _record(db, "UP")
    _record(db, "DOWN")
    assert claim_notification(db) is None


def test_delivery_success_marks_outbox_sent(tmp_path, monkeypatch):
    db = _setup_db(tmp_path)
    monkeypatch.setenv("WITMON_TELEGRAM_ENABLED", "true")
    _record(db, "UP")
    _record(db, "DOWN")
    sent = []
    monkeypatch.setattr("app.notifications._send_telegram", lambda config, event: sent.append(event))
    assert deliver_one(db, _settings()) is True
    assert len(sent) == 1
    assert claim_notification(db) is None


def test_delivery_failure_is_retried_without_raising(tmp_path, monkeypatch):
    db = _setup_db(tmp_path)
    monkeypatch.setenv("WITMON_TELEGRAM_ENABLED", "true")
    _record(db, "UP")
    _record(db, "DOWN")

    def fail(_settings, _event):
        raise OSError("Telegram unavailable")

    monkeypatch.setattr("app.notifications._send_telegram", fail)
    assert deliver_one(db, _settings()) is True
    assert claim_notification(db) is None


def test_telegram_settings_are_disabled_by_default(monkeypatch):
    for key in (
        "WITMON_TELEGRAM_ENABLED", "WITMON_TELEGRAM_BOT_TOKEN",
        "WITMON_TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    assert TelegramSettings.from_env().enabled is False


def test_enabled_telegram_requires_token_and_chat_id(monkeypatch):
    monkeypatch.setenv("WITMON_TELEGRAM_ENABLED", "true")
    monkeypatch.delenv("WITMON_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("WITMON_TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(ValueError, match="required settings"):
        TelegramSettings.from_env()


def test_telegram_delivery_posts_send_message_and_checks_api_response(monkeypatch):
    from app.notifications import _send_telegram

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"ok": True, "result": {"message_id": 1}}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("app.notifications.urllib.request.urlopen", fake_urlopen)
    event = {
        "service_name": "Warehouse API",
        "url": "https://example.test/health",
        "previous_status": "UP",
        "new_status": "DOWN",
        "occurred_at": "2026-11-23T10:00:00Z",
        "details": "Connection timed out",
    }
    _send_telegram(_settings(), event)

    assert captured["url"].endswith("/sendMessage")
    assert "123456:fake-token" in captured["url"]
    assert captured["body"]["chat_id"] == "123456789"
    assert "Warehouse API" in captured["body"]["text"]
    assert "UP → DOWN" in captured["body"]["text"]
    assert captured["timeout"] == 10.0


def test_telegram_api_error_is_reported_without_leaking_token(monkeypatch):
    from app.notifications import _send_telegram

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({"ok": False, "description": "Bad Request: chat not found"}).encode()

    monkeypatch.setattr("app.notifications.urllib.request.urlopen", lambda *_args, **_kwargs: FakeResponse())
    event = {
        "service_name": "Warehouse API", "url": "https://example.test",
        "previous_status": "UP", "new_status": "DOWN",
        "occurred_at": _checked_at(), "details": "unavailable",
    }
    with pytest.raises(RuntimeError, match="chat not found"):
        _send_telegram(_settings(), event)


def test_disabled_telegram_ignores_invalid_timeout(monkeypatch):
    monkeypatch.setenv("WITMON_TELEGRAM_ENABLED", "false")
    monkeypatch.setenv("WITMON_TELEGRAM_TIMEOUT", "not-a-timeout")
    settings = TelegramSettings.from_env()
    assert settings.enabled is False
    assert settings.timeout == 10.0



def test_telegram_notification_text_is_german(tmp_path, monkeypatch):
    db = _setup_db(tmp_path)
    monkeypatch.setenv("WITMON_TELEGRAM_ENABLED", "true")
    _record(db, "UP")
    _record(db, "DOWN")
    event = claim_notification(db)

    assert event is not None
    assert event["details"] == "Status geändert: UP → DOWN (unavailable)"
    message = _message_text(event)
    assert "Dienst: Web" in message
    assert "Status: UP → DOWN" in message
    assert "Endpunkt: https://example.test/health" in message
    assert "Zeitpunkt (UTC):" in message
    assert "Details: Status geändert: UP → DOWN (unavailable)" in message
    assert "Service:" not in message
    assert "Endpoint:" not in message
    assert "Time (UTC):" not in message
