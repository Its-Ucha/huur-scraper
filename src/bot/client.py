from __future__ import annotations

import asyncio
import datetime as dt
import logging

import discord
from discord.ext import tasks

from src.config import Settings
from src.notify.base import Notifier
from src.notify.discord_notifier import DiscordNotifier
from src.scrapers.factories import REGISTRY_FILE, SOURCE_FACTORIES
from src.scrapers.runner import RunSummary, run_all_sources
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)

PAUSED_KEY = "paused"
FIRST_RUN_DELAY_SECONDS = 30


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
        self.scrape_loop.change_interval(minutes=settings.scrape_interval_minutes)

    @property
    def paused(self) -> bool:
        return self.store.get_state(PAUSED_KEY, "0") == "1"

    def set_paused(self, paused: bool) -> None:
        self.store.set_state(PAUSED_KEY, "1" if paused else "0")

    @property
    def cycle_running(self) -> bool:
        return self._cycle_lock.locked()

    async def setup_hook(self) -> None:
        from src.bot.commands import register_commands

        register_commands(self)
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

        alert_channel = await self._resolve_channel(self.settings.discord_alert_channel_id)
        if alert_channel is None:
            logger.error(
                "Alert channel %s not found or not accessible; shutting down",
                self.settings.discord_alert_channel_id,
            )
            self.exit_code = 1
            await self.close()
            return

        ops_channel = None
        if self.settings.discord_ops_channel_id is not None:
            ops_channel = await self._resolve_channel(self.settings.discord_ops_channel_id)
            if ops_channel is None:
                logger.warning(
                    "Ops channel %s not found; ops messages go to the alert channel",
                    self.settings.discord_ops_channel_id,
                )

        self.notifier = DiscordNotifier(
            asyncio.get_running_loop(),
            alert_channel,
            ops_channel,
            self.settings.discord_mention_user_id,
        )
        self.scrape_loop.start()
        logger.info(
            "Scheduler started (every %d min, paused=%s)",
            self.settings.scrape_interval_minutes,
            self.paused,
        )

    async def _resolve_channel(self, channel_id: int):
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
            self.last_cycle_at = dt.datetime.now(tz=dt.timezone.utc)
            try:
                summary = await asyncio.to_thread(
                    run_all_sources,
                    settings=self.settings,
                    store=self.store,
                    source_factories=SOURCE_FACTORIES,
                    registry_file=REGISTRY_FILE,
                    notifier=self.notifier,
                    selected_sources=selected_sources,
                )
            except Exception as error:  # noqa: BLE001
                logger.exception("Scrape cycle failed")
                # notify_ops blocks on the event loop, so it must run off-loop.
                await asyncio.to_thread(self.notifier.notify_ops, f"[CYCLE_ERROR] {error}")
                return None
            logger.info("Cycle finished alerted=%d", summary.alerted)
            return summary

    async def scheduled_tick(self) -> None:
        if self.paused:
            logger.info("Scheduler paused; skipping cycle")
            return
        try:
            await self.run_cycle()
        except CycleBusyError:
            logger.info("Previous cycle still running; skipping scheduled tick")

    @tasks.loop(minutes=10)
    async def scrape_loop(self) -> None:
        # The interval is replaced in __init__ with SCRAPE_INTERVAL_MINUTES.
        await self.scheduled_tick()

    @scrape_loop.before_loop
    async def _before_scrape_loop(self) -> None:
        await self.wait_until_ready()
        await asyncio.sleep(FIRST_RUN_DELAY_SECONDS)
