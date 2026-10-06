from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable, Sequence

import discord

from src.filtering.rules import MatchResult
from src.models.listing import Listing
from src.models.mark import APPLIED, NOT_INTERESTED, ListingMark
from src.models.profile import Profile
from src.notify.base import ChannelUnavailableError


logger = logging.getLogger(__name__)

HARD_MATCH_COLOR = discord.Color.green()
CLOSE_MATCH_COLOR = discord.Color.gold()
APPLIED_COLOR = discord.Color.blurple()
NOT_INTERESTED_COLOR = discord.Color.dark_grey()
OPS_COLOR = discord.Color.red()
SEND_TIMEOUT_SECONDS = 30
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_VALUE_LIMIT = 1024
ALLOWED_MENTIONS = discord.AllowedMentions(users=True, roles=False, everyone=False)
ChannelResolver = Callable[[int], Awaitable[object | None]]
# Builds the action buttons for an alert; must run on the event loop.
ViewFactory = Callable[[Listing, ListingMark | None], discord.ui.View | None]


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _or_na(value: object) -> str:
    return "n/a" if value in (None, "") else str(value)


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _format_available(value: str | None) -> str:
    text = (value or "").strip()
    try:
        date = dt.date.fromisoformat(text[:10])
    except ValueError:
        return truncate(text, FIELD_VALUE_LIMIT)
    return f"{date.day} {date:%b %Y}"


def _format_address(features: dict[str, str]) -> str:
    street = f"{features.get('street', '')} {features.get('house_number', '')}".strip()
    address = ", ".join(part for part in (street, features.get("postal_code", "").strip()) if part)
    district = features.get("district", "").strip()
    if district:
        address = f"{address} · {district}" if address else district
    return truncate(address, FIELD_VALUE_LIMIT)


def _format_marked_at(value: str) -> str:
    try:
        date = dt.datetime.fromisoformat(value)
    except ValueError:
        return value
    return f"{date.day} {date:%b %Y}"


def _short_summary(listing: Listing) -> str:
    price = f"€{listing.rent_price}" if listing.rent_price is not None else "n/a"
    area = f"{listing.living_area_m2} m²" if listing.living_area_m2 is not None else "n/a"
    return f"{price} · {area} · {_or_na(listing.city)} · {_or_na(listing.source_site)}"


def build_not_interested_embed(listing: Listing, mark: ListingMark) -> discord.Embed:
    url = listing.source_url if _is_http_url(listing.source_url) else None
    embed = discord.Embed(
        title=truncate(listing.title or "(untitled)", TITLE_LIMIT),
        url=url,
        description=truncate(_short_summary(listing), DESCRIPTION_LIMIT),
        color=NOT_INTERESTED_COLOR,
    )
    embed.set_footer(text=f"Not interested · {_format_marked_at(mark.marked_at)}")
    return embed


def build_listing_embed(
    listing: Listing, match: MatchResult, mark: ListingMark | None = None
) -> discord.Embed:
    if mark is not None and mark.state == NOT_INTERESTED:
        return build_not_interested_embed(listing, mark)
    label = "Hard match" if match.is_hard_match else "Close match"
    url = listing.source_url if _is_http_url(listing.source_url) else None
    embed = discord.Embed(
        title=truncate(listing.title or "(untitled)", TITLE_LIMIT),
        url=url,
        color=HARD_MATCH_COLOR if match.is_hard_match else CLOSE_MATCH_COLOR,
    )
    features = listing.raw_features
    price = f"€{listing.rent_price}" if listing.rent_price is not None else "n/a"
    area = f"{listing.living_area_m2} m²" if listing.living_area_m2 is not None else "n/a"
    # Optional details are left out entirely when a site doesn't provide them.
    optional = {
        "Rooms": str(listing.rooms_total) if listing.rooms_total is not None else "",
        "Bedrooms": str(listing.bedrooms) if listing.bedrooms is not None else "",
        "Available": _format_available(listing.available_from),
        "Type": (features.get("asset_type") or features.get("property_type") or "").strip(),
        "Furnishing": features.get("furniture", "").strip(),
    }

    embed.add_field(name="Price", value=price)
    embed.add_field(name="Area", value=area)
    for name in ("Rooms", "Bedrooms"):
        if optional[name]:
            embed.add_field(name=name, value=optional[name])
    embed.add_field(name="City", value=_or_na(listing.city))
    for name in ("Available", "Type", "Furnishing"):
        if optional[name]:
            embed.add_field(name=name, value=truncate(optional[name], FIELD_VALUE_LIMIT))
    embed.add_field(name="Site", value=_or_na(listing.source_site))
    embed.add_field(name="Score", value=str(match.score))
    address = _format_address(features)
    if address:
        embed.add_field(name="Address", value=address, inline=False)

    image_url = features.get("image_url", "")
    if _is_http_url(image_url):
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text=label)
    if mark is not None and mark.state == APPLIED:
        embed.color = APPLIED_COLOR
        embed.description = f"✅ You applied on {_format_marked_at(mark.marked_at)}"
        embed.set_footer(text=f"{label} · Applied")
    return embed


def build_ops_embed(text: str) -> discord.Embed:
    return discord.Embed(description=truncate(text, DESCRIPTION_LIMIT), color=OPS_COLOR)


class DiscordNotifier:
    """Sends alerts to Discord from the scrape worker thread.

    Methods block until the message is sent (or fails) and must be called from a
    thread other than the bot's event loop thread.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        resolve_channel: ChannelResolver,
        ops_channel=None,
        view_factory: ViewFactory | None = None,
    ) -> None:
        self.loop = loop
        self.resolve_channel = resolve_channel
        self.ops_channel = ops_channel
        self.view_factory = view_factory

    def notify_listing(
        self, profile: Profile, listing: Listing, match: MatchResult, mark: ListingMark | None = None
    ) -> None:
        content = f"<@{profile.owner_user_id}>" if match.is_hard_match else None
        embed = build_listing_embed(listing, match, mark)
        self._run(self._send_listing(profile.channel_id, content, embed, listing, mark))

    def notify_ops(self, text: str, mention_user_ids: Sequence[int] = ()) -> None:
        if self.ops_channel is None:
            logger.warning("[OPS] %s", text)
            return
        content = " ".join(f"<@{user_id}>" for user_id in mention_user_ids) or None
        try:
            self._run(
                self.ops_channel.send(
                    content=content, embed=build_ops_embed(text), allowed_mentions=ALLOWED_MENTIONS
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("Discord ops send failed channel=%s", getattr(self.ops_channel, "id", "?"))

    async def _send_listing(
        self,
        channel_id: int,
        content: str | None,
        embed: discord.Embed,
        listing: Listing,
        mark: ListingMark | None,
    ) -> None:
        channel = await self.resolve_channel(channel_id)
        if channel is None:
            raise ChannelUnavailableError(f"channel {channel_id} not found")
        view = self.view_factory(listing, mark) if self.view_factory is not None else None
        try:
            await channel.send(content=content, embed=embed, view=view, allowed_mentions=ALLOWED_MENTIONS)
        except (discord.NotFound, discord.Forbidden) as error:
            raise ChannelUnavailableError(f"channel {channel_id}: {error}") from error

    def _run(self, coroutine) -> None:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            future.result(timeout=SEND_TIMEOUT_SECONDS)
        except BaseException:
            future.cancel()
            raise
