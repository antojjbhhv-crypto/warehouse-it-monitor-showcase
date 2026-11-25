"""Async periodic scheduler for explicitly configured service checks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CheckRunner = Callable[[str], list[dict[str, Any]]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CheckScheduler:
    """Run checks in a worker thread on a fixed, non-overlapping interval.

    The next run is scheduled from the previous deadline rather than from the
    completion time. If a check takes longer than one interval, missed runs are
    skipped instead of being queued in a burst.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        enabled: bool = True,
        interval_seconds: float = 60,
        check_runner: CheckRunner,
    ) -> None:
        if interval_seconds < 0.01:
            raise ValueError("interval_seconds must be at least 0.01")
        self.db_path = str(db_path)
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self._check_runner = check_runner

        self._stop_event = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._next_run_monotonic: float | None = None
        self._next_check_at: datetime | None = None
        self._last_auto_check_at: datetime | None = None
        self._last_manual_check_at: datetime | None = None
        self._last_error: str | None = None
        self._is_running = False

    async def start(self) -> None:
        """Start the background loop. The first automatic run is after one interval."""
        if not self.enabled or self._task is not None:
            return
        loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._next_run_monotonic = loop.time() + self.interval_seconds
        self._next_check_at = _utc_now() + timedelta(seconds=self.interval_seconds)
        self._task = asyncio.create_task(self._run_loop(), name="warehouse-it-monitor-scheduler")

    async def stop(self) -> None:
        """Stop the background loop and wait for an in-flight check to finish."""
        if self._task is None:
            return
        self._stop_event.set()
        await self._task
        self._task = None
        self._next_run_monotonic = None
        self._next_check_at = None

    async def run_manual(self) -> list[dict[str, Any]]:
        """Run a user-requested check without changing the periodic schedule."""
        return await self._execute_check(automatic=False)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "next_check_at": _as_utc_iso(self._next_check_at),
            "last_auto_check_at": _as_utc_iso(self._last_auto_check_at),
            "last_manual_check_at": _as_utc_iso(self._last_manual_check_at),
            "last_error": self._last_error,
            "is_running": self._is_running,
        }

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._stop_event.is_set():
            due = self._next_run_monotonic
            if due is None:
                return
            delay = max(0.0, due - loop.time())
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                return
            except TimeoutError:
                pass

            if self._stop_event.is_set():
                return

            try:
                await self._execute_check(automatic=True)
            except Exception:
                # _execute_check records the error. Keep the scheduler alive.
                pass

            next_due = due + self.interval_seconds
            now_monotonic = loop.time()
            while next_due <= now_monotonic:
                next_due += self.interval_seconds
            self._next_run_monotonic = next_due
            self._next_check_at = _utc_now() + timedelta(seconds=max(0.0, next_due - loop.time()))

    async def _execute_check(self, *, automatic: bool) -> list[dict[str, Any]]:
        async with self._run_lock:
            self._is_running = True
            try:
                results = await asyncio.to_thread(self._check_runner, self.db_path)
                checked_at = _utc_now()
                self._last_error = None
                if automatic:
                    self._last_auto_check_at = checked_at
                else:
                    self._last_manual_check_at = checked_at
                return results
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                self._is_running = False
