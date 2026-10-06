from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    id: int
    owner_user_id: int
    channel_id: int
    max_rent_eur: int
    min_size_m2: int
    preferred_bedrooms: int
    allow_close_match: bool
    municipalities: tuple[str, ...]
    paused: bool = False
