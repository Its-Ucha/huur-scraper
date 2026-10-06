from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from src.filtering.rules import MatchResult
from src.models.listing import Listing
from src.models.profile import Profile


logger = logging.getLogger(__name__)


class ChannelUnavailableError(Exception):
    """The profile's alert channel no longer exists or the bot can't post in it."""


class Notifier(Protocol):
    def notify_listing(self, profile: Profile, listing: Listing, match: MatchResult) -> None:
        """Send one listing alert. Raises when the send fails, so the caller can retry later."""

    def notify_ops(self, text: str, mention_user_ids: Sequence[int] = ()) -> None:
        """Send an ops message. Never raises."""


class LogNotifier:
    """Notifier for CLI runs: writes alerts to the log instead of sending them."""

    def notify_listing(self, profile: Profile, listing: Listing, match: MatchResult) -> None:
        match_type = "HARD_MATCH" if match.is_hard_match else "CLOSE_MATCH"
        logger.info(
            "[%s] owner=%s | %s | %s | price=%s area=%s city=%s | %s",
            match_type,
            profile.owner_user_id,
            listing.source_site,
            listing.title,
            listing.rent_price,
            listing.living_area_m2,
            listing.city,
            listing.source_url,
        )

    def notify_ops(self, text: str, mention_user_ids: Sequence[int] = ()) -> None:
        logger.warning("[OPS] %s", text)
