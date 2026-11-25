from __future__ import annotations

import json

import pytest

from app.main import load_scheduler_settings, load_services


def write_config(path, scheduler=None) -> None:
    payload = {"services": []}
    if scheduler is not None:
        payload["scheduler"] = scheduler
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_scheduler_settings_defaults_to_60_seconds_for_old_configs(tmp_path) -> None:
    config_path = tmp_path / "services.json"
    write_config(config_path)

    assert load_scheduler_settings(config_path) == {"enabled": True, "interval_seconds": 60}


def test_scheduler_settings_reads_custom_interval(tmp_path) -> None:
    config_path = tmp_path / "services.json"
    write_config(config_path, {"enabled": False, "interval_seconds": 300})

    assert load_scheduler_settings(config_path) == {"enabled": False, "interval_seconds": 300}


@pytest.mark.parametrize(
    "scheduler",
    [
        [],
        {"enabled": "yes", "interval_seconds": 60},
        {"enabled": True, "interval_seconds": True},
        {"enabled": True, "interval_seconds": 4},
        {"enabled": True, "interval_seconds": 86401},
        {"enabled": True, "interval_seconds": 2.5},
    ],
)
def test_scheduler_settings_rejects_invalid_values(tmp_path, scheduler) -> None:
    config_path = tmp_path / "services.json"
    write_config(config_path, scheduler)

    with pytest.raises(ValueError):
        load_scheduler_settings(config_path)


def test_scheduler_settings_accepts_utf8_bom(tmp_path) -> None:
    config_path = tmp_path / "services.json"
    config_path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"services": [], "scheduler": {"enabled": True, "interval_seconds": 5}}).encode("utf-8"))
    assert load_scheduler_settings(config_path) == {"enabled": True, "interval_seconds": 5}


def test_load_services_accepts_utf8_bom(tmp_path) -> None:
    config_path = tmp_path / "services.json"
    payload = {"services": [{"name": "Demo", "url": "http://127.0.0.1:8001/healthz"}]}
    config_path.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))
    assert load_services(config_path)[0]["name"] == "Demo"
