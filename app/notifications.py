"""Durable Telegram bot notifications for service status transitions."""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.database import claim_notification, finish_notification


@dataclass(frozen=True)
class TelegramSettings:
    enabled: bool
    token: str
    chat_id: str
    timeout: float = 10.0

    @classmethod
    def from_env(cls) -> "TelegramSettings":
        enabled_raw = os.getenv("WITMON_TELEGRAM_ENABLED", "false").strip().lower()
        if enabled_raw not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("WITMON_TELEGRAM_ENABLED must be true or false")
        enabled = enabled_raw in {"true", "1", "yes"}
        if not enabled:
            return cls(enabled=False, token="", chat_id="", timeout=10.0)

        settings = cls(
            enabled=True,
            token=os.getenv("WITMON_TELEGRAM_BOT_TOKEN", "").strip(),
            chat_id=os.getenv("WITMON_TELEGRAM_CHAT_ID", "").strip(),
            timeout=float(os.getenv("WITMON_TELEGRAM_TIMEOUT", "10")),
        )
        missing = [
            name for name, value in (
                ("WITMON_TELEGRAM_BOT_TOKEN", settings.token),
                ("WITMON_TELEGRAM_CHAT_ID", settings.chat_id),
            ) if not value
        ]
        if missing:
            raise ValueError(
                "Telegram notifications enabled but required settings are missing: "
                + ", ".join(missing)
            )
        if not 1 <= settings.timeout <= 60:
            raise ValueError("WITMON_TELEGRAM_TIMEOUT must be between 1 and 60 seconds")
        return settings


def _message_text(event: dict) -> str:
    text = (
        "Warehouse IT Monitor\n"
        f"Dienst: {event['service_name']}\n"
        f"Status: {event['previous_status']} → {event['new_status']}\n"
        f"Endpunkt: {event['url']}\n"
        f"Zeitpunkt (UTC): {event['occurred_at']}\n"
        f"Details: {event['details']}"
    )
    return text[:4000]


def _send_telegram(settings: TelegramSettings, event: dict) -> None:
    url = f"https://api.telegram.org/bot{settings.token}/sendMessage"
    payload = json.dumps({
        "chat_id": settings.chat_id,
        "text": _message_text(event),
        "disable_web_page_preview": True,
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Do not include the request URL: it contains the bot token.
        raise RuntimeError(f"Telegram HTTP error {exc.code}") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        description = result.get("description", "Unknown Telegram API error") if isinstance(result, dict) else "Invalid Telegram API response"
        raise RuntimeError(f"Telegram API rejected message: {str(description)[:300]}")


def deliver_one(db_path: str | Path, settings: TelegramSettings) -> bool:
    """Deliver one due item. Errors are persisted and never escape to check execution."""
    event = claim_notification(db_path)
    if event is None:
        return False
    try:
        _send_telegram(settings, event)
    except Exception as exc:
        # Never persist a bot token if an underlying URL/network exception contains it.
        error = f"{type(exc).__name__}: {str(exc).replace(settings.token, '[redacted]')[:500]}"
        finish_notification(db_path, int(event["id"]), error=error, max_attempts=5)
    else:
        finish_notification(db_path, int(event["id"]), error=None, max_attempts=5)
    return True


async def notification_worker(db_path: str | Path, settings: TelegramSettings) -> None:
    """Poll the durable outbox without blocking FastAPI or the check scheduler."""
    while True:
        try:
            delivered_or_attempted = await asyncio.to_thread(deliver_one, db_path, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            delivered_or_attempted = False
        if not delivered_or_attempted:
            await asyncio.sleep(5)
