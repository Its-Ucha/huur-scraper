from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.bot.client import LISTINGS_CLEARED_AT_KEY, CycleBusyError, HuurBot
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

    async def test_scheduled_tick_survives_state_read_error(self) -> None:
        # Any exception escaping the loop body stops discord.py's tasks.Loop for good.
        with patch.object(self.store, "get_state", side_effect=sqlite3.OperationalError("database is locked")):
            with self.assertLogs("src.bot.client", level="ERROR"):
                await self.bot.scheduled_tick()
        self.assertEqual(self.notifier.ops, ["[CYCLE_ERROR] database is locked"])

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


def utc(day: int, hour: int) -> dt.datetime:
    # 2026-10-05 is a Monday.
    return dt.datetime(2026, 10, day, hour, tzinfo=dt.timezone.utc)


class HuurBotClearTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = SQLiteStore(Path(self.tmp.name) / "bot.db")
        settings = make_settings(discord_guild_id=1, listings_clear_weekday=0, listings_clear_hour_utc=4)
        self.bot = HuurBot(settings, self.store)
        self.notifier = RecordingNotifier()
        self.bot.notifier = self.notifier
        self.channel = MagicMock()
        self.channel.purge = AsyncMock(return_value=[object(), object()])
        self.channel.permissions_for.return_value = SimpleNamespace(manage_messages=False)
        self.bot.alert_channel = self.channel

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_first_start_records_time_without_clearing(self) -> None:
        await self.bot.clear_tick(utc(5, 12))
        self.channel.purge.assert_not_called()
        self.assertEqual(self.store.get_state(LISTINGS_CLEARED_AT_KEY), utc(5, 12).isoformat())

    async def test_clears_once_after_slot_passes(self) -> None:
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(4, 12).isoformat())
        await self.bot.clear_tick(utc(5, 3))
        self.channel.purge.assert_not_called()

        await self.bot.clear_tick(utc(5, 4))
        self.channel.purge.assert_awaited_once()
        kwargs = self.channel.purge.call_args.kwargs
        self.assertEqual(kwargs["before"], utc(5, 4))
        self.assertFalse(kwargs["bulk"])

        await self.bot.clear_tick(utc(5, 5))
        self.channel.purge.assert_awaited_once()

    async def test_catches_up_on_missed_slot(self) -> None:
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await self.bot.clear_tick(utc(7, 9))
        self.channel.purge.assert_awaited_once()

    async def test_only_own_unpinned_messages_are_deleted(self) -> None:
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await self.bot.clear_tick(utc(5, 12))
        check = self.channel.purge.call_args.kwargs["check"]
        me = self.bot.user
        self.assertTrue(check(SimpleNamespace(author=me, pinned=False)))
        self.assertFalse(check(SimpleNamespace(author=me, pinned=True)))
        self.assertFalse(check(SimpleNamespace(author=object(), pinned=False)))

    async def test_uses_bulk_delete_with_manage_messages(self) -> None:
        self.channel.permissions_for.return_value = SimpleNamespace(manage_messages=True)
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await self.bot.clear_tick(utc(5, 12))
        self.assertTrue(self.channel.purge.call_args.kwargs["bulk"])

    async def test_failure_reported_once(self) -> None:
        self.channel.purge.side_effect = RuntimeError("Missing Access")
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        with self.assertLogs("src.bot.client", level="ERROR"):
            await self.bot.clear_tick(utc(5, 12))
        await self.bot.clear_tick(utc(5, 13))
        self.assertEqual(self.notifier.ops, ["[CLEAR_ERROR] Missing Access"])

    async def test_disabled_does_nothing(self) -> None:
        bot = HuurBot(make_settings(discord_guild_id=1), self.store)
        bot.alert_channel = self.channel
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await bot.clear_tick(utc(5, 12))
        self.channel.purge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
