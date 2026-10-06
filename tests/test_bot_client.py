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
from src.notify.dispatch import DispatchSummary
from src.scrapers.runner import RunSummary, SourceResult
from src.scrapers.scope import SearchScope
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

    async def test_run_cycle_scrapes_then_dispatches(self) -> None:
        active = self.store.create_profile(
            owner_user_id=42, channel_id=100, max_rent_eur=1000, min_size_m2=40,
            preferred_bedrooms=2, allow_close_match=True, municipalities=("delft",),
        )
        paused = self.store.create_profile(
            owner_user_id=43, channel_id=101, max_rent_eur=1000, min_size_m2=40,
            preferred_bedrooms=2, allow_close_match=True, municipalities=("leiden",),
        )
        self.store.set_profile_paused(paused.id, True)
        summary = RunSummary(results=[SourceResult(name="vbent", status="ok")])
        with patch("src.bot.client.run_all_sources", return_value=summary) as runner, \
                patch("src.bot.client.dispatch", return_value=DispatchSummary(sent=3)) as dispatcher:
            result = await self.bot.run_cycle({"vbent"})
        self.assertIs(result, summary)
        self.assertEqual(result.alerted, 3)
        self.assertIsNotNone(self.bot.last_cycle_at)
        kwargs = runner.call_args.kwargs
        self.assertIs(kwargs["notifier"], self.notifier)
        self.assertEqual(kwargs["selected_sources"], {"vbent"})
        self.assertEqual(kwargs["scope"], SearchScope(frozenset({"delft"})))
        self.assertEqual(dispatcher.call_args.kwargs["profiles"], [active])
        self.assertEqual(dispatcher.call_args.kwargs["stale_after"], dt.timedelta(minutes=30))

    async def test_run_dispatch_waits_for_running_cycle(self) -> None:
        profile = self.store.create_profile(
            owner_user_id=42, channel_id=100, max_rent_eur=1000, min_size_m2=40,
            preferred_bedrooms=2, allow_close_match=True, municipalities=("delft",),
        )
        release = threading.Event()
        started = threading.Event()

        def slow_run(**kwargs):
            started.set()
            release.wait(timeout=5)
            return RunSummary()

        with patch("src.bot.client.run_all_sources", side_effect=slow_run), \
                patch("src.bot.client.dispatch", return_value=DispatchSummary()) as dispatcher:
            cycle = asyncio.create_task(self.bot.run_cycle())
            await asyncio.to_thread(started.wait, 5)
            single = asyncio.create_task(self.bot.run_dispatch(profile.id))
            await asyncio.sleep(0.05)
            self.assertFalse(single.done())
            release.set()
            await cycle
            self.assertIsInstance(await single, DispatchSummary)
        self.assertEqual(dispatcher.call_args.kwargs["profiles"], [profile])

    async def test_run_dispatch_skips_paused_or_missing_profile(self) -> None:
        profile = self.store.create_profile(
            owner_user_id=42, channel_id=100, max_rent_eur=1000, min_size_m2=40,
            preferred_bedrooms=2, allow_close_match=True, municipalities=("delft",),
        )
        self.store.set_profile_paused(profile.id, True)
        with patch("src.bot.client.dispatch") as dispatcher:
            self.assertIsNone(await self.bot.run_dispatch(profile.id))
            self.assertIsNone(await self.bot.run_dispatch(999))
        dispatcher.assert_not_called()

    async def test_second_cycle_while_running_raises_busy(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow_run(**kwargs):
            started.set()
            release.wait(timeout=5)
            return RunSummary()

        with patch("src.bot.client.dispatch", return_value=DispatchSummary()), patch("src.bot.client.run_all_sources", side_effect=slow_run):
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

        with patch("src.bot.client.dispatch", return_value=DispatchSummary()), patch("src.bot.client.run_all_sources", side_effect=slow_run) as runner:
            first = asyncio.create_task(self.bot.run_cycle())
            await asyncio.to_thread(started.wait, 5)
            await self.bot.scheduled_tick()
            release.set()
            await first
        self.assertEqual(runner.call_count, 1)

    async def test_cycle_crash_notifies_ops_and_returns_none(self) -> None:
        with patch("src.bot.client.dispatch", return_value=DispatchSummary()), patch("src.bot.client.run_all_sources", side_effect=RuntimeError("db locked")):
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
        with patch("src.bot.client.dispatch", return_value=DispatchSummary()), patch("src.bot.client.run_all_sources", return_value=RunSummary()) as runner:
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
        self.channels = {}
        for owner, channel_id in ((42, 100), (43, 101)):
            self.store.create_profile(
                owner_user_id=owner, channel_id=channel_id, max_rent_eur=1000, min_size_m2=40,
                preferred_bedrooms=2, allow_close_match=True, municipalities=("delft",),
            )
            channel = MagicMock()
            channel.id = channel_id
            channel.purge = AsyncMock(return_value=[object(), object()])
            channel.permissions_for.return_value = SimpleNamespace(manage_messages=False)
            self.channels[channel_id] = channel
        self.channel = self.channels[100]
        self.bot.resolve_channel = AsyncMock(side_effect=lambda channel_id: self.channels.get(channel_id))

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_first_start_records_time_without_clearing(self) -> None:
        await self.bot.clear_tick(utc(5, 12))
        self.channel.purge.assert_not_called()
        self.assertEqual(self.store.get_state(LISTINGS_CLEARED_AT_KEY), utc(5, 12).isoformat())

    async def test_clears_every_profile_channel_once_after_slot_passes(self) -> None:
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(4, 12).isoformat())
        await self.bot.clear_tick(utc(5, 3))
        self.channel.purge.assert_not_called()

        await self.bot.clear_tick(utc(5, 4))
        for channel in self.channels.values():
            channel.purge.assert_awaited_once()
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

    async def test_one_failing_channel_does_not_stop_others_and_is_reported_once(self) -> None:
        self.channel.purge.side_effect = RuntimeError("Missing Access")
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        with self.assertLogs("src.bot.client", level="ERROR"):
            await self.bot.clear_tick(utc(5, 12))
        self.channels[101].purge.assert_awaited_once()
        await self.bot.clear_tick(utc(5, 13))
        self.assertEqual(self.notifier.ops, ["[CLEAR_ERROR] <#100>: Missing Access"])

    async def test_missing_channel_is_skipped(self) -> None:
        del self.channels[100]
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await self.bot.clear_tick(utc(5, 12))
        self.channels[101].purge.assert_awaited_once()
        self.assertEqual(self.notifier.ops, [])

    async def test_disabled_does_nothing(self) -> None:
        bot = HuurBot(make_settings(discord_guild_id=1), self.store)
        bot.resolve_channel = self.bot.resolve_channel
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await bot.clear_tick(utc(5, 12))
        self.channel.purge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
