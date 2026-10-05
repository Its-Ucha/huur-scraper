from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import discord

from src.filtering.rules import MatchResult
from src.models.listing import Listing


logger = logging.getLogger(__name__)

HARD_MATCH_COLOR = discord.Color.green()
CLOSE_MATCH_COLOR = discord.Color.gold()
OPS_COLOR = discord.Color.red()
SEND_TIMEOUT_SECONDS = 30
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _or_na(value: object) -> str:
    return "n/a" if value in (None, "") else str(value)


def build_listing_embed(listing: Listing, match: MatchResult) -> discord.Embed:
    label = "Hard match" if match.is_hard_match else "Close match"
    url = listing.source_url if listing.source_url.startswith(("http://", "https://")) else None
    embed = discord.Embed(
        title=truncate(listing.title or "(untitled)", TITLE_LIMIT),
        url=url,
        color=HARD_MATCH_COLOR if match.is_hard_match else CLOSE_MATCH_COLOR,
    )
    price = f"€{listing.rent_price}" if listing.rent_price is not None else "n/a"
    area = f"{listing.living_area_m2} m²" if listing.living_area_m2 is not None else "n/a"
    embed.add_field(name="Price", value=price)
    embed.add_field(name="Area", value=area)
    embed.add_field(name="Bedrooms", value=_or_na(listing.bedrooms))
    embed.add_field(name="City", value=_or_na(listing.city))
    embed.add_field(name="Site", value=_or_na(listing.source_site))
    embed.add_field(name="Score", value=str(match.score))
    embed.set_footer(text=label)
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
        alert_channel,
        ops_channel=None,
        mention_user_ids: Sequence[int] = (),
    ) -> None:
        self.loop = loop
        self.alert_channel = alert_channel
        self.ops_channel = ops_channel
        self.mention_user_ids = list(mention_user_ids)

    def notify_listing(self, listing: Listing, match: MatchResult) -> None:
        content = None
        if match.is_hard_match and self.mention_user_ids:
            content = " ".join(f"<@{user_id}>" for user_id in self.mention_user_ids)
        self._send(self.alert_channel, content, build_listing_embed(listing, match))

    def notify_ops(self, text: str) -> None:
        self._send(self.ops_channel or self.alert_channel, None, build_ops_embed(text))

    def _send(self, channel, content: str | None, embed: discord.Embed) -> None:
        coroutine = channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            future.result(timeout=SEND_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001
            future.cancel()
            logger.exception("Discord send failed channel=%s", getattr(channel, "id", "?"))
