# Architecture

Warehouse IT Monitor v0.7.0 is a single-process FastAPI application with a SQLite database.

## Runtime components

- **FastAPI application (`app/main.py`)** loads the JSON configuration, initializes SQLite, exposes the dashboard and API, and manages the application lifespan.
- **Monitor (`app/monitor.py`)** validates configured endpoints and dispatches HTTP or TCP checks.
- **HTTP check engine (`app/v050_check_engine.py`)** performs one request to the configured URL and does not follow redirects.
- **TCP checker (`app/v060_tcp.py`)** attempts one connection to the configured host and port.
- **Scheduler (`app/scheduler.py`)** runs checks at a configured interval and prevents overlapping runs.
- **SQLite layer (`app/database.py`)** stores services, check results, status-change events, and the notification outbox.
- **Migrations (`app/migrations/`)** apply additive schema changes.
- **Notification worker (`app/notifications.py`)** sends optional Telegram messages asynchronously using a persistent outbox.
- **Backup CLI (`app/backup.py`)** uses SQLite's Online Backup API and verifies database integrity before restore.

## Data flow

1. The application reads explicitly configured services from JSON.
2. The scheduler or a manual API action starts a check.
3. The monitor checks each enabled configured endpoint.
4. Results are stored in SQLite.
5. A status-change event is created only when the status differs from the previous result.
6. If Telegram is enabled, a real transition is placed in the outbox for asynchronous delivery.
7. The dashboard reads the current status, event history, and sample-based analytics from SQLite.

## Process model

Run Uvicorn with one worker. The scheduler and notification worker live inside the application process. Multiple workers would create independent schedulers and workers. Do not run native and Docker instances against the same SQLite database at the same time.
