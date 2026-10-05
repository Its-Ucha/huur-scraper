import asyncio
import logging

import discord
from discord import app_commands

from src.bot.checks import is_control_user, parse_sources
from src.bot.client import CycleBusyError, HuurBot
from src.bot.embeds import (
    build_listings_embed,
    build_profile_embed,
    build_status_embed,
    build_summary_embed,
)
from src.scrapers.factories import SOURCE_FACTORIES


# No `from __future__ import annotations` here: discord.py inspects slash-command
# parameter annotations at decoration time, and these handlers are nested functions.

logger = logging.getLogger(__name__)


def register_commands(bot: HuurBot) -> None:
    tree = bot.tree

    async def ensure_control(interaction: discord.Interaction) -> bool:
        role_ids = [role.id for role in getattr(interaction.user, "roles", [])]
        if is_control_user(interaction.user.id, role_ids, bot.settings):
            return True
        await interaction.response.send_message(
            "You're not allowed to use this command.", ephemeral=True
        )
        return False

    @tree.command(name="listings", description="Show recently stored matching listings")
    @app_commands.describe(limit="How many listings to show (1-25)")
    async def listings(
        interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 10
    ) -> None:
        rows = await asyncio.to_thread(bot.store.get_recent_listings, limit)
        await interaction.response.send_message(embed=build_listings_embed(rows), ephemeral=True)

    @tree.command(name="status", description="Show scheduler state and the last run per source")
    async def status(interaction: discord.Interaction) -> None:
        rows = await asyncio.to_thread(bot.store.get_latest_source_runs)
        next_run = bot.scrape_loop.next_iteration if bot.scrape_loop.is_running() else None
        embed = build_status_embed(bot.paused, bot.cycle_running, next_run, bot.last_cycle_at, rows)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(name="profile", description="Show the current search profile")
    async def profile(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_profile_embed(bot.settings), ephemeral=True
        )

    @tree.command(name="scrape", description="Run a scrape cycle now")
    @app_commands.describe(sources="Comma-separated source names (default: all)")
    async def scrape(interaction: discord.Interaction, sources: str = "") -> None:
        if not await ensure_control(interaction):
            return
        selected, unknown = parse_sources(sources, SOURCE_FACTORIES.keys())
        if unknown:
            known = ", ".join(sorted(SOURCE_FACTORIES))
            await interaction.response.send_message(
                f"Unknown source(s): {', '.join(unknown)}. Known: {known}", ephemeral=True
            )
            return
        if bot.notifier is None:
            await interaction.response.send_message("Bot is still starting up.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            summary = await bot.run_cycle(selected)
        except CycleBusyError:
            await interaction.followup.send("A scrape cycle is already running. Try again shortly.")
            return
        if summary is None:
            await interaction.followup.send(
                "The scrape cycle failed. Check the ops channel or container logs."
            )
            return
        await interaction.followup.send(embed=build_summary_embed(summary))

    @tree.command(name="pause", description="Pause scheduled scraping")
    async def pause(interaction: discord.Interaction) -> None:
        if not await ensure_control(interaction):
            return
        bot.set_paused(True)
        logger.info("Paused by user=%s", interaction.user.id)
        await interaction.response.send_message(
            "Scheduled scraping paused. `/scrape` still works.", ephemeral=True
        )

    @tree.command(name="resume", description="Resume scheduled scraping")
    async def resume(interaction: discord.Interaction) -> None:
        if not await ensure_control(interaction):
            return
        bot.set_paused(False)
        logger.info("Resumed by user=%s", interaction.user.id)
        await interaction.response.send_message("Scheduled scraping resumed.", ephemeral=True)
