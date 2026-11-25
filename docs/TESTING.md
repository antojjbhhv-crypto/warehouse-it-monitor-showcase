# Testing

## Automated suite

Run:

```bash
python -m pytest -v
```

The v0.7.0 workflow run on 23 November 2026 reported **100 passed** in each matrix job.

## CI matrix

GitHub Actions runs on:

| Operating system | Python |
|---|---|
| Ubuntu | 3.13 |
| Ubuntu | 3.15.1 |
| Windows | 3.13 |
| Windows | 3.15.1 |

Each job installs `requirements.txt`, runs pytest, and performs a SQLite `backup → verify → restore` smoke test.

## Test boundaries

Tests use temporary SQLite databases, controlled responses, and test doubles. They do not probe arbitrary external hosts or send real Telegram messages. Network-check tests exercise controlled local/test endpoints or mocked behavior.

## Manual demo

1. Start `demo_service.main:app` on `127.0.0.1:8001`.
2. Start `app.main:app` on `127.0.0.1:8000`.
3. Open the dashboard and trigger a manual check.
4. Stop the demo service and run a check to observe `DOWN`.
5. Restart the demo service and run a check to observe `UP`.

This demonstrates status transitions using only the local demo service.
