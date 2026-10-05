from __future__ import annotations

import logging
from typing import Protocol

from src.filtering.rules import MatchResult
from src.models.listing import Listing


logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def notify_listing(self, listing: Listing, match: MatchResult) -> None: ...

    def notify_ops(self, text: str) -> None: ...


class LogNotifier:
    """Notifier for CLI runs: writes alerts to the log instead of sending them."""

    def notify_listing(self, listing: Listing, match: MatchResult) -> None:
        match_type = "HARD_MATCH" if match.is_hard_match else "CLOSE_MATCH"
        logger.info(
            "[%s] %s | %s | price=%s area=%s city=%s | %s",
            match_type,
            listing.source_site,
            listing.title,
            listing.rent_price,
            listing.living_area_m2,
            listing.city,
            listing.source_url,
        )

    def notify_ops(self, text: str) -> None:
        logger.warning("[OPS] %s", text)
