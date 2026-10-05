from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.bot.client import CycleBusyError, HuurBot
from src.scrapers.runner import RunSummary, SourceResult
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import RecordingNotifier, make_settings


class HuurBotCycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "bot.db"
        self.store = SQLiteStore(self.db_path)
        self.bot = HuurBot(make_settings(discord_guild_id=1), self.store)
        self.notifier = RecordingNotifier()
        self.bot.notifier = self.notifier

    async def asyncTearDown(self) -> None:
        # The bot never logs in, so there is no connection to close.
        self.tmp.cleanup()

    async def test_run_cycle_returns_summary_and_records_time(self) -> None:
        summary = RunSummary(results=[SourceResult(name="vbent", status="ok")])
        with patch("src.bot.client.run_all_sources", return_value=summary) as runner:
            result = await self.bot.run_cycle({"vbent"})
        self.assertIs(result, summary)
        self.assertIsNotNone(self.bot.last_cycle_at)
        kwargs = runner.call_args.kwargs
        self.assertIs(kwargs["notifier"], self.notifier)
        self.assertEqual(kwargs["selected_sources"], {"vbent"})

    async def test_second_cycle_while_running_raises_busy(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow_run(**kwargs):
            started.set()
            release.wait(timeout=5)
            return RunSummary()

        with patch("src.bot.client.run_all_sources", side_effect=slow_run):
            first = asyncio.create_task(self.bot.run_cycle())
            await asyncio.to_thread(started.wait, 5)
            self.assertTrue(self.bot.cycle_running)
            with self.assertRaises(CycleBusyError):
                await self.bot.run_cycle()
            release.set()
            self.assertIsInstance(await first, RunSummary)
        self.assertFalse(self.bot.cycle_running)

    async def test_scheduled_tick_skips_silently_when_busy(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow_run(**kwargs):
            started.set()
            release.wait(timeout=5)
            return RunSummary()

        with patch("src.bot.client.run_all_sources", side_effect=slow_run) as runner:
            first = asyncio.create_task(self.bot.run_cycle())
            await asyncio.to_thread(started.wait, 5)
            await self.bot.scheduled_tick()
            release.set()
            await first
        self.assertEqual(runner.call_count, 1)

    async def test_cycle_crash_notifies_ops_and_returns_none(self) -> None:
        with patch("src.bot.client.run_all_sources", side_effect=RuntimeError("db locked")):
            with self.assertLogs("src.bot.client", level="ERROR"):
                result = await self.bot.run_cycle()
        self.assertIsNone(result)
        self.assertEqual(self.notifier.ops, ["[CYCLE_ERROR] db locked"])
        self.assertFalse(self.bot.cycle_running)

    async def test_scheduled_tick_skips_when_paused(self) -> None:
        self.bot.set_paused(True)
        with patch("src.bot.client.run_all_sources") as runner:
            await self.bot.scheduled_tick()
        runner.assert_not_called()

    async def test_scheduled_tick_runs_when_not_paused(self) -> None:
        with patch("src.bot.client.run_all_sources", return_value=RunSummary()) as runner:
            await self.bot.scheduled_tick()
        runner.assert_called_once()

    async def test_pause_persists_across_bot_instances(self) -> None:
        self.bot.set_paused(True)
        other = HuurBot(make_settings(discord_guild_id=1), SQLiteStore(self.db_path))
        self.assertTrue(other.paused)
        other.set_paused(False)
        self.assertFalse(self.bot.paused)

    async def test_interval_comes_from_settings(self) -> None:
        bot = HuurBot(make_settings(discord_guild_id=1, scrape_interval_minutes=25), self.store)
        self.assertEqual(bot.scrape_loop.minutes, 25)


if __name__ == "__main__":
    unittest.main()
