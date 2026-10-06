"""Applied / Not interested buttons under listing alerts.

The buttons are DynamicItems: their custom_id carries the action and listing ref, so
they keep working after a restart without storing a view per message.
"""

import asyncio
import datetime as dt
import logging

import discord

from src.bot.checks import owns_alert_channel
from src.filtering.rules import evaluate_listing
from src.models.listing import Listing
from src.models.mark import APPLIED, NOT_INTERESTED, ListingMark
from src.notify.discord_notifier import build_listing_embed


logger = logging.getLogger(__name__)

UNDO = "undo"
CUSTOM_ID_LIMIT = 100
BUTTONS = {
    APPLIED: ("Applied", discord.ButtonStyle.success),
    NOT_INTERESTED: ("Not interested", discord.ButtonStyle.secondary),
    UNDO: ("Undo", discord.ButtonStyle.secondary),
}


def action_custom_id(action: str, listing_ref: str) -> str:
    return f"ls:{action}:{listing_ref}"


def button_actions(mark: ListingMark | None) -> tuple[str, ...]:
    return (UNDO,) if mark is not None else (APPLIED, NOT_INTERESTED)


class ListingActionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"ls:(?P<action>applied|not_interested|undo):(?P<ref>.+)",
):
    def __init__(self, action: str, listing_ref: str) -> None:
        label, style = BUTTONS[action]
        super().__init__(
            discord.ui.Button(label=label, style=style, custom_id=action_custom_id(action, listing_ref))
        )
        self.action = action
        self.listing_ref = listing_ref

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        return cls(match["action"], match["ref"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_listing_action(interaction.client, interaction, self.action, self.listing_ref)


def build_listing_view(listing: Listing, mark: ListingMark | None) -> discord.ui.View | None:
    listing_ref = listing.ref()
    if len(action_custom_id(NOT_INTERESTED, listing_ref)) > CUSTOM_ID_LIMIT:
        logger.warning("Listing ref %r is too long for buttons; alert sent without them", listing_ref)
        return None
    view = discord.ui.View(timeout=None)
    for action in button_actions(mark):
        view.add_item(ListingActionButton(action, listing_ref))
    return view


async def handle_listing_action(bot, interaction: discord.Interaction, action: str, listing_ref: str) -> None:
    # Deferred first: the DB may be busy with a scrape cycle for longer than Discord waits.
    await interaction.response.defer()
    store = bot.store
    profile = await asyncio.to_thread(store.get_profile, interaction.user.id)
    if not owns_alert_channel(profile, interaction.channel_id):
        await interaction.followup.send("These buttons only work for the owner of this channel.", ephemeral=True)
        return
    listing = await asyncio.to_thread(store.get_latest_listing, listing_ref)
    if listing is None:
        await interaction.followup.send("This listing is no longer stored.", ephemeral=True)
        return

    if action == UNDO:
        await asyncio.to_thread(store.clear_mark, profile.id, listing_ref)
        mark = None
    else:
        marked_at = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        await asyncio.to_thread(store.set_mark, profile.id, listing_ref, action, marked_at)
        mark = ListingMark(action, marked_at)

    match = evaluate_listing(listing, profile)
    await interaction.edit_original_response(
        embed=build_listing_embed(listing, match, mark), view=build_listing_view(listing, mark)
    )
    await sync_pin(interaction, mark)


async def sync_pin(interaction: discord.Interaction, mark: ListingMark | None) -> None:
    # Applied alerts are pinned so the weekly channel clear keeps them.
    message = interaction.message
    want_pinned = mark is not None and mark.state == APPLIED
    if message is None or message.pinned == want_pinned:
        return
    try:
        if want_pinned:
            await message.pin(reason="Marked as applied")
        else:
            await message.unpin(reason="Applied mark removed")
    except discord.HTTPException as error:
        logger.warning("Pin change on message=%s failed: %s", message.id, error)
        if want_pinned:
            await interaction.followup.send(
                f"Saved as applied, but I couldn't pin the message ({error.text or error}), "
                "so the weekly clear may remove it. /listings status:Applied still shows it.",
                ephemeral=True,
            )
