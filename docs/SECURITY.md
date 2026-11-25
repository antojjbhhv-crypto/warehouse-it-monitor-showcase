# Security notes

- This is a local learning project, not a hardened production monitoring platform.
- The dashboard has no authentication. Keep it bound to loopback; do not expose it to the public internet.
- Configure only endpoints you own or are authorized to monitor.
- No host discovery or port-range scanning is implemented.
- Do not place credentials in monitored URLs.
- Telegram is disabled by default. Keep bot tokens and chat IDs in a local `.env` file; never commit that file.
- The repository intentionally excludes runtime databases, backup files, local data, and environment files.
- Restore is an offline operation. Stop the application before restoring a database.
