import asyncio
import logging
import sqlite3
from dataclasses import replace

import discord

from src.bot.checks import city_select_pages, validate_profile_numbers
from src.bot.embeds import build_profile_panel_embed
from src.bot.profile_channels import archive_profile_channel, create_profile_channel
from src.filtering.municipalities import all_municipalities
from src.models.profile import Profile


logger = logging.getLogger(__name__)

VIEW_TIMEOUT_SECONDS = 600
GONE_TEXT = "This profile no longer exists. Run /profile to start again."


def panel_message(bot, profile: Profile) -> dict:
    return {"embed": build_profile_panel_embed(profile), "view": ProfilePanel(bot, profile)}


class OwnerView(discord.ui.View):
    """Ephemeral views already reach only their owner; this guards against misuse anyway."""

    def __init__(self, bot, owner_id: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.bot = bot
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This panel belongs to someone else.", ephemeral=True)
        return False

    def current_profile(self):
        return self.bot.store.get_profile(self.owner_id)


class CreateProfileView(OwnerView):
    @discord.ui.button(label="Create profile", style=discord.ButtonStyle.primary)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_profile() is not None:
            await interaction.response.edit_message(
                content="You already have a profile. Run /profile again.", embed=None, view=None
            )
            return
        await interaction.response.defer()
        settings = self.bot.settings
        try:
            channel = await create_profile_channel(
                interaction.guild,
                interaction.user,
                settings.huur_category_name,
                settings.discord_control_role_id,
            )
        except discord.HTTPException as error:
            logger.exception("Creating a channel for user=%s failed", interaction.user.id)
            await interaction.edit_original_response(
                content=f"Couldn't create your channel: {error}", embed=None, view=None
            )
            return
        try:
            profile = self.bot.store.create_profile(
                owner_user_id=interaction.user.id,
                channel_id=channel.id,
                max_rent_eur=settings.max_rent_eur,
                min_size_m2=settings.min_size_m2,
                preferred_bedrooms=settings.preferred_bedrooms,
                allow_close_match=settings.allow_close_match,
                municipalities=(),
            )
        except sqlite3.IntegrityError:
            # A second click raced this one; keep the profile that won and drop this channel.
            await channel.delete(reason="Duplicate huur profile")
            await interaction.edit_original_response(
                content="You already have a profile. Run /profile again.", embed=None, view=None
            )
            return
        logger.info("Created profile=%s for user=%s channel=%s", profile.id, profile.owner_user_id, channel.id)
        await interaction.edit_original_response(
            content=f"Created {channel.mention}. Press **Cities** to pick where you're looking.",
            **panel_message(self.bot, profile),
        )
        self.stop()


class ProfilePanel(OwnerView):
    def __init__(self, bot, profile: Profile) -> None:
        super().__init__(bot, profile.owner_user_id)
        self.toggle_close.label = f"Close match: {'on' if profile.allow_close_match else 'off'}"
        self.toggle_pause.label = "Resume" if profile.paused else "Pause"

    async def _gone(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=GONE_TEXT, embed=None, view=None)

    async def _save(self, interaction: discord.Interaction, updated: Profile, content=None, dispatch=True) -> None:
        self.bot.store.update_profile(updated)
        await interaction.response.edit_message(content=content, **panel_message(self.bot, updated))
        if dispatch and not updated.paused:
            self.bot.schedule_dispatch(updated.id)

    @discord.ui.button(label="Edit numbers", style=discord.ButtonStyle.primary)
    async def edit_numbers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        await interaction.response.send_modal(NumbersModal(self.bot, profile))

    @discord.ui.button(label="Cities", style=discord.ButtonStyle.primary)
    async def cities(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        await interaction.response.edit_message(
            content=CitiesView.PROMPT, embed=None, view=CitiesView(self.bot, profile)
        )

    @discord.ui.button(label="Close match", style=discord.ButtonStyle.secondary)
    async def toggle_close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        await self._save(interaction, replace(profile, allow_close_match=not profile.allow_close_match))

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary)
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        if not profile.paused:
            await self._save(interaction, replace(profile, paused=True), dispatch=False)
            return
        if await self.bot.resolve_channel(profile.channel_id) is None:
            await interaction.response.send_message(
                "Your alert channel is gone. Delete this profile and create a new one.", ephemeral=True
            )
            return
        await self._save(interaction, replace(profile, paused=False))

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        await interaction.response.edit_message(
            content=(
                "Delete your profile? Your channel will be archived (read-only) "
                "and an admin will remove it."
            ),
            embed=None,
            view=ConfirmDeleteView(self.bot, profile),
        )


class NumbersModal(discord.ui.Modal, title="Edit numbers"):
    def __init__(self, bot, profile: Profile) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.bot = bot
        self.owner_id = profile.owner_user_id
        self.max_rent = discord.ui.TextInput(
            label="Max rent per month (€)", default=str(profile.max_rent_eur), max_length=6
        )
        self.min_size = discord.ui.TextInput(
            label="Min living area (m²)", default=str(profile.min_size_m2), max_length=4
        )
        self.bedrooms = discord.ui.TextInput(
            label="Preferred bedrooms (affects score only)",
            default=str(profile.preferred_bedrooms),
            max_length=2,
        )
        for item in (self.max_rent, self.min_size, self.bedrooms):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values, errors = validate_profile_numbers(
            self.max_rent.value, self.min_size.value, self.bedrooms.value
        )
        if errors:
            await interaction.response.send_message(
                "Not saved:\n" + "\n".join(f"• {error}" for error in errors), ephemeral=True
            )
            return
        profile = self.bot.store.get_profile(self.owner_id)
        if profile is None:
            await interaction.response.send_message(GONE_TEXT, ephemeral=True)
            return
        updated = replace(profile, **values)
        self.bot.store.update_profile(updated)
        await interaction.response.edit_message(
            content="Saved. New matches will appear in your channel shortly.",
            **panel_message(self.bot, updated),
        )
        if not updated.paused:
            self.bot.schedule_dispatch(updated.id)


class CitiesView(OwnerView):
    PROMPT = "Pick the municipalities you want alerts for, then press **Save cities**."

    def __init__(self, bot, profile: Profile) -> None:
        super().__init__(bot, profile.owner_user_id)
        self.pages = city_select_pages(all_municipalities())
        self.selected: list[set[str]] = []
        for index, page in enumerate(self.pages):
            chosen = {item.key for item in page if item.key in profile.municipalities}
            self.selected.append(chosen)
            select = discord.ui.Select(
                placeholder=f"Cities {page[0].name[0]}–{page[-1].name[0]}",
                min_values=0,
                max_values=len(page),
                options=[
                    discord.SelectOption(label=item.name, value=item.key, default=item.key in chosen)
                    for item in page
                ],
                row=index,
            )
            select.callback = self._select_callback(index, select)
            self.add_item(select)
        save = discord.ui.Button(label="Save cities", style=discord.ButtonStyle.success, row=len(self.pages))
        save.callback = self.save
        self.add_item(save)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, row=len(self.pages))
        cancel.callback = self.cancel
        self.add_item(cancel)

    def _select_callback(self, index: int, select: discord.ui.Select):
        async def callback(interaction: discord.Interaction) -> None:
            self.selected[index] = set(select.values)
            await interaction.response.defer()

        return callback

    async def save(self, interaction: discord.Interaction) -> None:
        profile = self.current_profile()
        if profile is None:
            await interaction.response.edit_message(content=GONE_TEXT, embed=None, view=None)
            return
        chosen = set().union(*self.selected)
        keys = tuple(item.key for page in self.pages for item in page if item.key in chosen)
        updated = replace(profile, municipalities=keys)
        self.bot.store.update_profile(updated)
        content = "Cities saved." if keys else "Cities cleared; you won't get alerts until you pick some."
        await interaction.response.edit_message(content=content, **panel_message(self.bot, updated))
        if keys and not updated.paused:
            self.bot.schedule_dispatch(updated.id)
        self.stop()

    async def cancel(self, interaction: discord.Interaction) -> None:
        profile = self.current_profile()
        if profile is None:
            await interaction.response.edit_message(content=GONE_TEXT, embed=None, view=None)
            return
        await interaction.response.edit_message(content=None, **panel_message(self.bot, profile))
        self.stop()


class ConfirmDeleteView(OwnerView):
    def __init__(self, bot, profile: Profile) -> None:
        super().__init__(bot, profile.owner_user_id)

    @discord.ui.button(label="Yes, delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        profile = self.current_profile()
        if profile is None:
            await interaction.edit_original_response(content=GONE_TEXT, embed=None, view=None)
            return
        self.bot.store.delete_profile(profile.id)
        logger.info("Deleted profile=%s of user=%s", profile.id, profile.owner_user_id)
        channel = await self.bot.resolve_channel(profile.channel_id)
        if channel is not None:
            try:
                await archive_profile_channel(channel, interaction.user)
            except discord.HTTPException:
                logger.exception("Archiving channel %s failed", profile.channel_id)
        if self.bot.notifier is not None:
            text = (
                f"[PROFILE_DELETED] <@{profile.owner_user_id}> deleted their profile; "
                f"<#{profile.channel_id}> can be removed."
            )
            # notify_ops blocks on the event loop, so it must run off-loop.
            await asyncio.to_thread(
                self.bot.notifier.notify_ops, text, self.bot.settings.discord_control_user_ids
            )
        await interaction.edit_original_response(
            content="Profile deleted. Your channel is archived.", embed=None, view=None
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await interaction.response.edit_message(content=GONE_TEXT, embed=None, view=None)
            return
        await interaction.response.edit_message(content=None, **panel_message(self.bot, profile))
        self.stop()
