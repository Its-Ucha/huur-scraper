import asyncio
import datetime as dt
import logging

import discord
from discord import app_commands

from src.bot.checks import is_control_user, parse_sources
from src.bot.client import CycleBusyError, HuurBot
from src.bot.embeds import (
    LISTINGS_EMPTY_TEXTS,
    LISTINGS_TITLES,
    build_listings_embed,
    listing_marker,
    build_no_profile_embed,
    build_profiles_list_embed,
    build_status_embed,
    build_summary_embed,
)
from src.bot.profile_views import CreateProfileView, panel_message
from src.notify.dispatch import is_current, recent_matches
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

    @tree.command(name="listings", description="Show listings that match your profile")
    @app_commands.describe(
        limit="How many listings to show (1-25)",
        status="Which listings to show (default: new ones you haven't marked)",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="New (not marked yet)", value="new"),
            app_commands.Choice(name="Applied", value="applied"),
            app_commands.Choice(name="Not interested", value="not_interested"),
            app_commands.Choice(name="All current matches", value="all"),
        ]
    )
    async def listings(
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 25] = 10,
        status: str = "new",
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        profile = await asyncio.to_thread(bot.store.get_profile, interaction.user.id)
        if profile is None:
            await interaction.followup.send(
                "You don't have a profile yet. Run /profile to create one.", ephemeral=True
            )
            return
        now = dt.datetime.now(tz=dt.timezone.utc)
        entries = await asyncio.to_thread(
            recent_matches, bot.store, profile, now, bot.stale_after, limit, status
        )
        embed = build_listings_embed(
            [listing.to_record() for listing, _ in entries],
            empty_text=LISTINGS_EMPTY_TEXTS[status],
            title=LISTINGS_TITLES[status],
            markers=[
                listing_marker(mark, not is_current(listing, now, bot.stale_after))
                for listing, mark in entries
            ],
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tree.command(name="status", description="Show scheduler state and the last run per source")
    async def status(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await asyncio.to_thread(bot.store.get_latest_source_runs)
        profiles = await asyncio.to_thread(bot.store.list_profiles)
        active = sum(1 for profile in profiles if not profile.paused)
        next_run = bot.scrape_loop.next_iteration if bot.scrape_loop.is_running() else None
        embed = build_status_embed(
            bot.paused, bot.cycle_running, next_run, bot.last_cycle_at, rows, (active, len(profiles))
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tree.command(name="profile", description="Create or edit your search profile")
    async def profile(interaction: discord.Interaction) -> None:
        # Acknowledge first: the DB read can be slow while a scrape cycle is writing,
        # and Discord drops interactions that aren't answered within 3 seconds.
        await interaction.response.defer(ephemeral=True, thinking=True)
        existing = await asyncio.to_thread(bot.store.get_profile, interaction.user.id)
        if existing is None:
            await interaction.followup.send(
                embed=build_no_profile_embed(),
                view=CreateProfileView(bot, interaction.user.id),
                ephemeral=True,
            )
            return
        await interaction.followup.send(ephemeral=True, **panel_message(bot, existing))

    @tree.command(name="profiles", description="List everyone's search profiles")
    async def profiles(interaction: discord.Interaction) -> None:
        if not await ensure_control(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await asyncio.to_thread(bot.store.list_profiles)
        await interaction.followup.send(embed=build_profiles_list_embed(rows), ephemeral=True)

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
