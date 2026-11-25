# Changelog

## 0.7.0 — 2026-11-23

- Added optional Telegram notifications for real service status transitions (UP → DOWN and DOWN → UP); notifications are disabled by default.
- Added a persistent SQLite notification outbox with bounded retries (up to five attempts) and backoff delays.
- Moved notification delivery off the scheduler's event loop so a slow Telegram API request does not block scheduled checks.
- Added Telegram Bot API response validation and token redaction in error messages.
- Added Telegram configuration through environment variables and Docker Compose, with setup instructions in the README.
- Localized the dashboard and README in German while preserving technical status values and API identifiers.
- Added tests for notification configuration, transition queueing, Telegram API responses, retries, and disabled mode.
- Confirmed end-to-end delivery for both DOWN and UP transitions; repeated unchanged status does not create duplicate notifications.

## 0.6.0 — 2026-11-23

- Added explicit TCP checks for one configured host:port per service; no port scanning, ranges, or host discovery.
- Added check_type (http/tcp), host, and port columns through an additive SQLite migration; historical rows read as http.
- Routed HTTP and TCP services through separate checkers in the shared scheduler while preserving v0.5.0 HTTP behaviour.
- Added check_type filtering to the history API and dashboard, plus Type columns in services and history.
- Added TCP result categories: TCP_CONNECTED, TIMEOUT, DNS_ERROR, CONNECTION_REFUSED, NETWORK_ERROR.
- Added tests for TCP configuration, migration, persistence, scheduler routing, API and dashboard.

## 0.5.0 — 2026-11-23

- Added per-service check policies: method (GET/HEAD), expected_status_codes, and slow_threshold_ms.
- Added stable result categories (OK, SLOW_RESPONSE, HTTP_STATUS, TIMEOUT, DNS_ERROR, CONNECTION_REFUSED, CONNECTION_RESET, TLS_ERROR, NETWORK_ERROR, UNKNOWN_ERROR) and severity (INFO, WARNING, ERROR).
- Slow but successful responses stay UP, so sample-based availability is unaffected.
- Added an additive SQLite migration; historical rows remain untouched and are read as UNCLASSIFIED.
- Added a category filter to the history API and dashboard, plus category and severity columns for services and history.
- Preserved the v0.4.x open_db() connection handling and the httpx2 test dependency.
- Added 21 tests for policies, the check engine, and the migration.

## 0.4.2 — 2026-11-23

- Added httpx2 to the test dependencies; Starlette's TestClient now uses it, so StarletteDeprecationWarning no longer appears. httpx is kept for compatibility.
- Expanded CI to a four-job matrix: Ubuntu and Windows × Python 3.13 and 3.15.1.
- Added a backup → verify → restore smoke step to every CI job, which also covers the Windows os.replace() path.
- No application logic, database schema, or API changes.

## 0.4.1 — 2026-11-23

- Fixed restore failing on Windows with PermissionError: [WinError 5] when the target database had just been used.
- SQLite connections are now closed deterministically through app.database.open_db() instead of relying on garbage collection, so Windows releases file handles before os.replace().
- Verified on Python 3.15.1: 41 tests pass and the CLI cycle backup → verify → restore works with a preserved pre-restore safety copy.
- No schema, configuration, or API changes.

## 0.4.0 — 2026-11-23

- Added a CLI for verified SQLite backup, restore, and database verification (python -m app.backup).
- Backups use SQLite's online backup API and are validated before being atomically published.
- Restore validates the source first, refuses non-empty WAL/SHM sidecars or a locked target, and creates a pre-restore safety backup by default.
- Added a Python 3.15.1 Docker image and an optional Docker Compose setup with an isolated local demo service and loopback-only dashboard publishing.
- Added a GitHub Actions test workflow for Python 3.13 and 3.15.1 on push, pull request, and manual dispatch.
- Preserved the native Windows/Python run path and the v0.3.0 database schema.
- Added backup/restore tests; all 39 tests pass locally under Python 3.13.5.

## 0.3.0 — 2026-11-23

- Added a response-time chart with averages calculated from successful (UP) checks only.
- Added a time-bucketed chart showing the share of UP and DOWN check samples.
- Added analytics periods: 24 hours, 7 days, 30 days, and 90 days.
- Added sample-based availability metrics: total completed checks, UP/DOWN counts, sample success percentage, and average/minimum/maximum response time for successful checks.
- Clearly documented that sample-based availability is not continuous uptime; periods without check results are not counted as UP or DOWN.
- Added filterable and paginated check history by service, period, and status.
- Added GET /api/analytics and GET /api/history endpoints.
- Added an index on check timestamps to support date-range analytics on existing SQLite databases.
- Preserved the v0.2.1 UTF-8 BOM-safe JSON configuration reader (utf-8-sig) and added tests for both configuration loaders.
- Kept all existing v0.2 scheduler behavior and APIs.

## 0.2.1 — 2026-11-23

- Made JSON configuration loading compatible with UTF-8 files containing a BOM, as written by some Windows editors.
- Added tests for BOM compatibility in both service and scheduler configuration loading.

## 0.2.0 — 2026-11-23

- Added a background scheduler for periodic HTTP health checks.
- Added scheduler.enabled and scheduler.interval_seconds to config/services.json (default: enabled, 60 seconds; allowed interval: 5–86400 seconds).
- Added GET /api/scheduler with next run, last automatic/manual run, status, and last error.
- Added a live next-check countdown to the dashboard and automatic dashboard refresh after scheduled checks.
- Serialized manual and scheduled checks to avoid overlapping check runs.
- Skipped missed schedule ticks after long-running checks rather than launching catch-up bursts.
- Preserved backward compatibility for v0.1 configuration files without a scheduler section.
- Added scheduler and configuration tests.

## 0.1.0

- Initial manual HTTP/HTTPS health checks, SQLite history, API, and dashboard.
