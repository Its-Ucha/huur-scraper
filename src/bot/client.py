from __future__ import annotations

import asyncio
import datetime as dt
import logging

import discord
from discord.ext import tasks

from src.bot.checks import last_scheduled_clear
from src.bot.listing_actions import ListingActionButton, build_listing_view
from src.bot.seeding import seed_profile_from_settings
from src.config import Settings
from src.notify.base import Notifier
from src.notify.discord_notifier import DiscordNotifier
from src.notify.dispatch import DispatchSummary, dispatch, stale_window
from src.scrapers.factories import REGISTRY_FILE, SOURCE_FACTORIES
from src.scrapers.runner import RunSummary, run_all_sources
from src.scrapers.scope import SearchScope
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)

PAUSED_KEY = "paused"
LISTINGS_CLEARED_AT_KEY = "listings_cleared_at"
CLEAR_CHECK_MINUTES = 15
FIRST_RUN_DELAY_SECONDS = 30


def _utc_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


class CycleBusyError(Exception):
    """Raised when a scrape cycle is requested while another one is running."""


class HuurBot(discord.Client):
    def __init__(self, settings: Settings, store: SQLiteStore) -> None:
        super().__init__(intents=discord.Intents.default())
        self.settings = settings
        self.store = store
        self.tree = discord.app_commands.CommandTree(self)
        self.notifier: Notifier | None = None
        self.last_cycle_at: dt.datetime | None = None
        self.exit_code = 0
        self._cycle_lock = asyncio.Lock()
        self._ready_once = False
        # Keeps fire-and-forget dispatch tasks referenced until they finish.
        self._background_tasks: set[asyncio.Task] = set()
        self.scrape_loop.change_interval(minutes=settings.scrape_interval_minutes)

    @property
    def paused(self) -> bool:
        return self.store.get_state(PAUSED_KEY, "0") == "1"

    def set_paused(self, paused: bool) -> None:
        self.store.set_state(PAUSED_KEY, "1" if paused else "0")

    @property
    def cycle_running(self) -> bool:
        return self._cycle_lock.locked()

    @property
    def stale_after(self) -> dt.timedelta:
        return stale_window(self.settings.scrape_interval_minutes)

    async def setup_hook(self) -> None:
        from src.bot.commands import register_commands

        register_commands(self)
        self.add_dynamic_items(ListingActionButton)
        guild = discord.Object(id=self.settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info("Synced %d slash commands to guild=%s", len(synced), guild.id)

    async def on_ready(self) -> None:
        if self._ready_once:
            logger.info("Reconnected as %s", self.user)
            return
        self._ready_once = True
        logger.info("Logged in as %s", self.user)

        ops_channel = await self._resolve_ops_channel()
        self.notifier = DiscordNotifier(
            asyncio.get_running_loop(), self.resolve_channel, ops_channel, view_factory=build_listing_view
        )
        await asyncio.to_thread(seed_profile_from_settings, self.store, self.settings)
        self.warn_if_ops_in_profile_channel(ops_channel)

        self.scrape_loop.start()
        logger.info(
            "Scheduler started (every %d min, paused=%s)",
            self.settings.scrape_interval_minutes,
            self.paused,
        )
        if self.settings.listings_clear_weekday is not None:
            self.clear_loop.start()

    async def _resolve_ops_channel(self):
        # Ops messages fall back to the old alert channel, then to the log only.
        for channel_id in (self.settings.discord_ops_channel_id, self.settings.discord_alert_channel_id):
            if channel_id is None:
                continue
            channel = await self.resolve_channel(channel_id)
            if channel is not None:
                return channel
            logger.warning("Ops channel candidate %s not found or not accessible", channel_id)
        logger.warning("No ops channel available; ops messages only go to the log")
        return None

    def warn_if_ops_in_profile_channel(self, ops_channel) -> None:
        # Without DISCORD_OPS_CHANNEL_ID, ops falls back to the old alert channel, which
        # becomes the seeded profile's channel; if that profile is deleted and the channel
        # removed, ops messages would only reach the log.
        if ops_channel is None:
            return
        if any(profile.channel_id == ops_channel.id for profile in self.store.list_profiles()):
            logger.warning(
                "Ops messages go to a profile's alert channel (%s); set DISCORD_OPS_CHANNEL_ID "
                "to a separate channel",
                ops_channel.id,
            )

    async def resolve_channel(self, channel_id: int):
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        if not isinstance(channel, discord.abc.Messageable):
            return None
        return channel

    async def run_cycle(self, selected_sources: set[str] | None = None) -> RunSummary | None:
        if self._cycle_lock.locked():
            raise CycleBusyError()
        async with self._cycle_lock:
            self.last_cycle_at = _utc_now()
            try:
                profiles = await asyncio.to_thread(self.store.list_profiles, active_only=True)
                summary = await asyncio.to_thread(
                    run_all_sources,
                    settings=self.settings,
                    store=self.store,
                    source_factories=SOURCE_FACTORIES,
                    registry_file=REGISTRY_FILE,
                    notifier=self.notifier,
                    selected_sources=selected_sources,
                    scope=SearchScope.from_profiles(profiles),
                )
                # Re-read so edits saved during the scrape are used.
                profiles = await asyncio.to_thread(self.store.list_profiles, active_only=True)
                result = await asyncio.to_thread(
                    dispatch,
                    store=self.store,
                    profiles=profiles,
                    notifier=self.notifier,
                    now=_utc_now(),
                    stale_after=self.stale_after,
                )
            except Exception as error:  # noqa: BLE001
                logger.exception("Scrape cycle failed")
                # notify_ops blocks on the event loop, so it must run off-loop.
                await asyncio.to_thread(self.notifier.notify_ops, f"[CYCLE_ERROR] {error}")
                return None
            summary.alerted = result.sent
            logger.info("Cycle finished alerted=%d", summary.alerted)
            return summary

    async def run_dispatch(self, profile_id: int) -> DispatchSummary | None:
        # Waits for a running cycle instead of failing: the user is waiting on their own edit.
        async with self._cycle_lock:
            try:
                profile = await asyncio.to_thread(self.store.get_profile_by_id, profile_id)
                if profile is None or profile.paused or self.notifier is None:
                    return None
                return await asyncio.to_thread(
                    dispatch,
                    store=self.store,
                    profiles=[profile],
                    notifier=self.notifier,
                    now=_utc_now(),
                    stale_after=self.stale_after,
                )
            except Exception as error:  # noqa: BLE001
                logger.exception("Dispatch for profile=%s failed", profile_id)
                if self.notifier is not None:
                    await asyncio.to_thread(self.notifier.notify_ops, f"[DISPATCH_ERROR] {error}")
                return None

    def schedule_dispatch(self, profile_id: int) -> None:
        task = asyncio.create_task(self.run_dispatch(profile_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def scheduled_tick(self) -> None:
        # Any exception escaping here stops discord.py's tasks.Loop for good, so
        # everything is caught and reported instead.
        try:
            if self.paused:
                logger.info("Scheduler paused; skipping cycle")
                return
            await self.run_cycle()
        except CycleBusyError:
            logger.info("Previous cycle still running; skipping scheduled tick")
        except Exception as error:  # noqa: BLE001
            logger.exception("Scheduled tick failed")
            if self.notifier is not None:
                await asyncio.to_thread(self.notifier.notify_ops, f"[CYCLE_ERROR] {error}")

    @tasks.loop(minutes=10)
    async def scrape_loop(self) -> None:
        # The interval is replaced in __init__ with SCRAPE_INTERVAL_MINUTES.
        await self.scheduled_tick()

    @scrape_loop.before_loop
    async def _before_scrape_loop(self) -> None:
        await self.wait_until_ready()
        await asyncio.sleep(FIRST_RUN_DELAY_SECONDS)

    async def clear_tick(self, now: dt.datetime | None = None) -> None:
        # Same rule as scheduled_tick: nothing may escape, or the loop stops for good.
        try:
            await self._clear_if_due(now or _utc_now())
        except Exception as error:  # noqa: BLE001
            logger.exception("Clearing the listings channels failed")
            if self.notifier is not None:
                await asyncio.to_thread(self.notifier.notify_ops, f"[CLEAR_ERROR] {error}")

    async def _clear_if_due(self, now: dt.datetime) -> None:
        weekday = self.settings.listings_clear_weekday
        if weekday is None:
            return
        last_cleared = self.store.get_state(LISTINGS_CLEARED_AT_KEY)
        if last_cleared is None:
            # First start with this feature: keep the existing history and begin
            # counting from the next slot instead of wiping the channels right away.
            self.store.set_state(LISTINGS_CLEARED_AT_KEY, now.isoformat())
            return
        due = last_scheduled_clear(now, weekday, self.settings.listings_clear_hour_utc)
        if dt.datetime.fromisoformat(last_cleared) >= due:
            return
        # Recorded before purging so a permission error is reported once, not every tick.
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, now.isoformat())

        deleted = 0
        failures: list[str] = []
        for profile in await asyncio.to_thread(self.store.list_profiles):
            channel = await self.resolve_channel(profile.channel_id)
            if channel is None:
                logger.warning("Channel %s of profile=%s not found; not cleared", profile.channel_id, profile.id)
                continue
            try:
                deleted += await self.clear_channel(channel, before=now)
            except Exception as error:  # noqa: BLE001
                logger.exception("Clearing channel %s failed", profile.channel_id)
                failures.append(f"<#{profile.channel_id}>: {error}")
        logger.info("Cleared %d messages from profile channels", deleted)
        if failures and self.notifier is not None:
            await asyncio.to_thread(self.notifier.notify_ops, "[CLEAR_ERROR] " + "; ".join(failures))

    async def clear_channel(self, channel, before: dt.datetime) -> int:
        # Bulk delete needs Manage Messages; without it the bot can still delete
        # its own messages one by one (slower, but fine for a week's worth).
        me = getattr(channel.guild, "me", None)
        bulk = me is not None and channel.permissions_for(me).manage_messages
        deleted = await channel.purge(
            limit=None,
            before=before,
            check=lambda message: message.author == self.user and not message.pinned,
            bulk=bulk,
            reason="Weekly listings channel cleanup",
        )
        return len(deleted)

    @tasks.loop(minutes=CLEAR_CHECK_MINUTES)
    async def clear_loop(self) -> None:
        # Polls instead of firing at a fixed time, so a slot missed while the bot
        # was down is caught up after the next start.
        await self.clear_tick()

    @clear_loop.before_loop
    async def _before_clear_loop(self) -> None:
        await self.wait_until_ready()
