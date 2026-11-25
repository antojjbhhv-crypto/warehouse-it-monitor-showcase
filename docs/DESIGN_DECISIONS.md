# Design decisions

## Python for the complete application

Python keeps the API, scheduler, database access, backup CLI, and tests in one language. Its standard library provides SQLite access, sockets, JSON handling, asynchronous coordination, and the Telegram HTTP request. FastAPI and Uvicorn provide the web layer.

## SQLite instead of a database server

The project is a small, local, single-process learning application. SQLite avoids a separate database service and stores the data in one file. Transactions, foreign keys, constraints, and indexes are sufficient for the current schema. The Online Backup API supports consistent backups while the application is running.

## No ORM

The project uses the standard `sqlite3` module and explicit SQL. This keeps the persistence layer visible and makes schema migrations and queries easier to inspect for a learning project.

## Persistent notification outbox

Telegram delivery is optional and must not block monitoring. Status transitions are stored in SQLite, then delivered by a background worker. The outbox provides bounded retries and avoids queuing the initial baseline or repeated identical statuses.

## Explicit endpoints only

The monitor checks only configured HTTP URLs or individual TCP `host:port` entries. It does not discover hosts, scan port ranges, or follow HTTP redirects to unconfigured destinations.

## Local-only dashboard

The dashboard has no authentication. Native and Compose examples bind it to loopback. It is intended for local demonstration, not public deployment.
