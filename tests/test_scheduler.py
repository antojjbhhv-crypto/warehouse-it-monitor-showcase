from __future__ import annotations

import asyncio
import unittest

from app.scheduler import CheckScheduler


class CheckSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_runs_periodically_and_stops_cleanly(self) -> None:
        calls: list[str] = []

        def fake_runner(db_path: str) -> list[dict]:
            calls.append(db_path)
            return []

        scheduler = CheckScheduler(
            "test.sqlite3",
            enabled=True,
            interval_seconds=0.05,
            check_runner=fake_runner,
        )
        await scheduler.start()
        initial = scheduler.status()
        self.assertTrue(initial["enabled"])
        self.assertIsNotNone(initial["next_check_at"])

        await asyncio.sleep(0.13)
        await scheduler.stop()

        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(path == "test.sqlite3" for path in calls))
        self.assertIsNotNone(scheduler.status()["last_auto_check_at"])
        self.assertIsNone(scheduler.status()["next_check_at"])

    async def test_disabled_scheduler_does_not_run(self) -> None:
        calls: list[bool] = []
        scheduler = CheckScheduler(
            "test.sqlite3",
            enabled=False,
            interval_seconds=0.05,
            check_runner=lambda _path: calls.append(True) or [],
        )
        await scheduler.start()
        await asyncio.sleep(0.08)
        await scheduler.stop()

        self.assertEqual(calls, [])
        self.assertFalse(scheduler.status()["enabled"])
        self.assertIsNone(scheduler.status()["next_check_at"])

    async def test_manual_check_does_not_reset_periodic_deadline(self) -> None:
        scheduler = CheckScheduler(
            "test.sqlite3",
            enabled=True,
            interval_seconds=60,
            check_runner=lambda _path: [],
        )
        await scheduler.start()
        original_deadline = scheduler.status()["next_check_at"]
        await scheduler.run_manual()
        state = scheduler.status()
        await scheduler.stop()

        self.assertEqual(state["next_check_at"], original_deadline)
        self.assertIsNotNone(state["last_manual_check_at"])
        self.assertIsNone(state["last_auto_check_at"])

    async def test_slow_checks_do_not_overlap_or_create_a_burst(self) -> None:
        import threading
        import time

        active = 0
        max_active = 0
        calls = 0
        guard = threading.Lock()

        def slow_runner(_path: str) -> list[dict]:
            nonlocal active, max_active, calls
            with guard:
                active += 1
                calls += 1
                max_active = max(max_active, active)
            time.sleep(0.08)
            with guard:
                active -= 1
            return []

        scheduler = CheckScheduler(
            "test.sqlite3",
            enabled=True,
            interval_seconds=0.04,
            check_runner=slow_runner,
        )
        await scheduler.start()
        await asyncio.sleep(0.25)
        await scheduler.stop()

        self.assertEqual(max_active, 1)
        self.assertLessEqual(calls, 3)

    async def test_scheduler_records_runner_errors_and_continues(self) -> None:
        attempts = 0

        def flaky_runner(_path: str) -> list[dict]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("simulated failure")
            return []

        scheduler = CheckScheduler(
            "test.sqlite3",
            enabled=True,
            interval_seconds=0.05,
            check_runner=flaky_runner,
        )
        await scheduler.start()
        await asyncio.sleep(0.13)
        await scheduler.stop()

        self.assertGreaterEqual(attempts, 2)
        self.assertIsNotNone(scheduler.status()["last_auto_check_at"])
        self.assertIsNone(scheduler.status()["last_error"])


if __name__ == "__main__":
    unittest.main()
