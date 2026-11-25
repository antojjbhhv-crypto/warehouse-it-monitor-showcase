# Warehouse IT Monitor

![CI](https://github.com/antojjbhhv-crypto/warehouse-it-monitor-showcase/actions/workflows/tests.yml/badge.svg)

**Version:** 0.7.0 · **Type:** local learning and portfolio project

[Deutsch](README.md) · **English**

Warehouse IT Monitor is a small Python application for checking explicitly configured HTTP/HTTPS services and individual TCP endpoints. It stores check history in SQLite and provides a local web dashboard with status changes, sample-based analytics, and optional Telegram notifications.

> **Local-use notice:** This project is not production monitoring software. It does not discover hosts or scan networks. It checks only endpoints explicitly entered in the configuration. The dashboard has no authentication and must not be exposed to the public internet.

## Features

- HTTP/HTTPS checks for configured URLs, with configurable method, expected status codes, timeout, and slow-response threshold.
- One TCP connection attempt to each explicitly configured `host:port`; no port-range scanning or host discovery.
- `UP` / `DOWN` status, response time, result category, severity, and error details.
- SQLite persistence, additive schema migrations, and status-change event history.
- FastAPI web dashboard with service overview, event timeline, SVG charts, filters, and paginated history.
- Configurable background scheduler; overlapping runs are prevented.
- Optional Telegram notifications for actual status transitions, backed by a persistent SQLite outbox and bounded retries.
- SQLite Online Backup API with integrity verification and guarded restore.
- Docker Compose demo setup bound to loopback.
- pytest suite and GitHub Actions matrix for Ubuntu and Windows with Python 3.13 and 3.15.1.

## Technology

- Python 3.11+ for native execution
- FastAPI and Uvicorn
- SQLite via Python's standard `sqlite3` module (no ORM)
- `urllib`, `socket`, `asyncio`, `dataclasses`, and `json` from the standard library
- pytest, httpx, and GitHub Actions
- Docker / Docker Compose (optional)

## Quick start (Windows PowerShell)

Create and activate a virtual environment, then install dependencies:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Start the local demo service in the first terminal:

```powershell
uvicorn demo_service.main:app --host 127.0.0.1 --port 8001
```

Start the monitor in a second terminal:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The first scheduled check runs after one complete interval (60 seconds by default). You can also trigger a manual check from the dashboard.

Stop the demo service with Ctrl+C and run a manual check to see the status change to `DOWN`. Start it again and the next check should return to `UP`.

## Configuration

Native execution reads `config/services.json`. The repository includes a local demo endpoint:

```json
{
  "scheduler": { "enabled": true, "interval_seconds": 60 },
  "services": [
    {
      "name": "Warehouse Demo Service",
      "url": "http://127.0.0.1:8001/healthz",
      "timeout_seconds": 2.0
    }
  ]
}
```

The scheduler interval must be an integer from 5 to 86400 seconds. Each HTTP service may also define `method` (`GET` or `HEAD`), `expected_status_codes`, and `slow_threshold_ms`.

For a TCP check, define exactly one host and port:

```json
{
  "name": "Local Demo TCP Port",
  "check_type": "tcp",
  "host": "127.0.0.1",
  "port": 8001,
  "timeout_seconds": 2.0
}
```

Only configure systems you own or are authorized to check. The application does not scan other ports or discover other hosts.

## Telegram notifications (optional)

Notifications are disabled by default. Copy `.env.example` to a local `.env` file and set:

```dotenv
WITMON_TELEGRAM_ENABLED=true
WITMON_TELEGRAM_BOT_TOKEN=your-bot-token
WITMON_TELEGRAM_CHAT_ID=your-chat-id
WITMON_TELEGRAM_TIMEOUT=10
```

Never commit `.env` or share bot tokens. Notifications are queued only for actual `UP → DOWN` and `DOWN → UP` transitions; the initial status is a baseline and does not generate a message.

## API

- `GET /healthz` — application status and version
- `GET /api/services` — configured services and latest checks
- `GET /api/events` — status-change events
- `GET /api/scheduler` — scheduler status
- `GET /api/analytics?period=7d&service_id=1` — sample-based analytics
- `GET /api/history?period=7d&limit=50&offset=0` — filtered and paginated check history
- `POST /api/checks/run` — trigger checks manually

## Backup and restore

Create and verify a SQLite backup:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
python -m app.backup backup --db data/monitor.sqlite3 --output "backups/monitor-$stamp.sqlite3"
python -m app.backup verify --db "backups/monitor-$stamp.sqlite3"
```

Restore is an offline operation. Stop the monitor first:

```powershell
python -m app.backup restore --db data/monitor.sqlite3 --input "backups/monitor-YYYYMMDD-HHMMSS.sqlite3"
```

The restore command verifies the source and preserves the current database by default.

## Docker Compose

Docker Compose is an optional local demo. It uses `config/services.docker.json` and binds the dashboard to `127.0.0.1:8000`.

```powershell
New-Item -ItemType Directory -Force data, backups | Out-Null
docker compose up --build -d
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop with `docker compose down`.

## Tests and CI

Run the test suite:

```powershell
python -m pytest -v
```

The v0.7.0 GitHub Actions run on 23 November 2026 completed successfully on all four matrix jobs: Ubuntu / Python 3.13, Ubuntu / Python 3.15.1, Windows / Python 3.13, and Windows / Python 3.15.1. The test step reported **100 passed** in each job, followed by a successful `backup → verify → restore` smoke test.

Tests use temporary databases and controlled test responses; they do not contact arbitrary external services.

## Project structure

```text
app/                 FastAPI app, monitor, scheduler, SQLite and backup logic
app/migrations/      Additive SQLite migrations
config/              Local demo and example service configurations
demo_service/        Local demo HTTP service
docs/                Design notes
tests/               Automated test suite
.github/workflows/   GitHub Actions CI
```

See [Architecture](docs/ARCHITECTURE.md), [Testing](docs/TESTING.md), [Design decisions](docs/DESIGN_DECISIONS.md), and the German [README](README.md).
