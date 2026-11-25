"""Verified SQLite backup and restore utilities for Warehouse IT Monitor.

Backups use SQLite's online backup API, so a backup is transactionally
consistent even when the monitor is writing check results. Restore is an
offline operation: stop the monitor before replacing its database file.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_TABLES = {"monitored_services", "check_results", "service_events"}


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


def _validate_database(path: str | Path) -> dict[str, Any]:
    """Check SQLite integrity, foreign keys, and the application's tables."""
    database = Path(path)
    if not database.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {database}")

    try:
        connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity_messages = [str(row[0]) for row in integrity_rows]
            if integrity_messages != ["ok"]:
                raise ValueError("SQLite integrity_check failed: " + "; ".join(integrity_messages))

            table_rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            tables = {str(row[0]) for row in table_rows}
            missing = REQUIRED_TABLES - tables
            if missing:
                raise ValueError("Not a Warehouse IT Monitor database; missing tables: " + ", ".join(sorted(missing)))

            foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise ValueError(f"SQLite foreign_key_check found {len(foreign_key_errors)} violation(s)")

            counts: dict[str, int] = {}
            for table in sorted(REQUIRED_TABLES):
                # Names are selected from the fixed allow-list above.
                counts[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            return {"integrity": "ok", "tables": sorted(tables), "row_counts": counts}
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Cannot read a valid SQLite database at {database}: {exc}") from exc


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    """Copy a live SQLite database consistently using Connection.backup()."""
    source = sqlite3.connect(str(source_path), timeout=30)
    destination = sqlite3.connect(str(destination_path), timeout=30)
    try:
        source.backup(destination, pages=256, sleep=0.05)
    finally:
        destination.close()
        source.close()


def _temporary_path(directory: Path, prefix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".sqlite3.tmp", dir=directory)
    os.close(descriptor)
    Path(name).unlink(missing_ok=True)
    return Path(name)


def verify_database(db_path: str | Path) -> dict[str, Any]:
    """Public verification entry point used by the CLI and tests."""
    return _validate_database(db_path)


def backup_database(
    db_path: str | Path,
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create an atomic, verified SQLite backup of the configured database."""
    source = Path(db_path)
    output = Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(f"Source database does not exist: {source}")
    if _same_path(source, output):
        raise ValueError("Backup output must be different from the source database")
    if output.exists() and not overwrite:
        raise FileExistsError(f"Backup already exists: {output}; use --overwrite to replace it")

    _validate_database(source)
    temporary = _temporary_path(output.parent, f".{output.name}.")
    try:
        _sqlite_backup(source, temporary)
        details = _validate_database(temporary)
        if output.exists() and not overwrite:
            raise FileExistsError(f"Backup already exists: {output}; use --overwrite to replace it")
        os.replace(temporary, output)
        details.update({"backup_path": str(output), "size_bytes": output.stat().st_size})
        return details
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_restore_target_is_idle(target: Path) -> None:
    """Fail fast if SQLite cannot obtain an exclusive lock on the target."""
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise RuntimeError(
                f"Restore refused because SQLite sidecar exists: {sidecar}. Stop the monitor and close other database users first."
            )
    if not target.exists():
        return
    connection = sqlite3.connect(str(target), timeout=0)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        connection.rollback()
    except sqlite3.OperationalError as exc:
        raise RuntimeError("Restore refused: the target database appears to be in use. Stop the monitor first.") from exc
    finally:
        connection.close()


def restore_database(
    db_path: str | Path,
    input_path: str | Path,
    *,
    safety_backup: bool = True,
) -> dict[str, Any]:
    """Restore from a verified backup, preserving the previous DB by default.

    The monitor must be stopped. The replacement is staged and verified in the
    target directory before os.replace() swaps it into place.
    """
    target = Path(db_path)
    source = Path(input_path)
    if not source.is_file():
        raise FileNotFoundError(f"Restore source does not exist: {source}")
    if _same_path(target, source):
        raise ValueError("Restore source and target must be different files")

    _validate_database(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    _ensure_restore_target_is_idle(target)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safety_path: Path | None = None
    if target.exists() and safety_backup:
        safety_path = target.with_name(f"{target.stem}.pre-restore-{stamp}{target.suffix or '.sqlite3'}")
        # The current database is preserved before replacement.
        backup_database(target, safety_path)

    temporary = _temporary_path(target.parent, f".{target.name}.restore.")
    try:
        _sqlite_backup(source, temporary)
        restored_details = _validate_database(temporary)
        _ensure_restore_target_is_idle(target)
        os.replace(temporary, target)
        restored_details.update({"database_path": str(target), "restored_from": str(source)})
        if safety_path is not None:
            restored_details["safety_backup_path"] = str(safety_path)
        return restored_details
    finally:
        temporary.unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.backup", description="Verified SQLite backup and restore")
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="create and verify a SQLite backup")
    backup.add_argument("--db", default="data/monitor.sqlite3", help="source database path")
    backup.add_argument("--output", required=True, help="backup file path")
    backup.add_argument("--overwrite", action="store_true", help="replace an existing backup file")

    restore = commands.add_parser("restore", help="restore a verified backup (stop the monitor first)")
    restore.add_argument("--db", default="data/monitor.sqlite3", help="target database path")
    restore.add_argument("--input", required=True, help="backup file to restore")
    restore.add_argument("--no-safety-backup", action="store_true", help="do not preserve the current database before restore")

    verify = commands.add_parser("verify", help="verify database integrity and required tables")
    verify.add_argument("--db", default="data/monitor.sqlite3", help="database path to verify")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            result = backup_database(args.db, args.output, overwrite=args.overwrite)
        elif args.command == "restore":
            result = restore_database(args.db, args.input, safety_backup=not args.no_safety_backup)
        else:
            result = verify_database(args.db)
        print("OK")
        for key, value in result.items():
            print(f"{key}: {value}")
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
