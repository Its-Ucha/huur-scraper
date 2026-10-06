from __future__ import annotations

from dataclasses import dataclass


APPLIED = "applied"
NOT_INTERESTED = "not_interested"
MARK_STATES = (APPLIED, NOT_INTERESTED)


@dataclass(frozen=True)
class ListingMark:
    """What a profile owner did with a listing, set with the buttons under an alert."""

    state: str
    marked_at: str
