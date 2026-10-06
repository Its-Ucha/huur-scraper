from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.models.profile import Profile


@dataclass(frozen=True)
class SearchScope:
    """Where the active profiles are looking; scrapers with a location filter use it."""

    municipalities: frozenset[str] = frozenset()

    @classmethod
    def from_profiles(cls, profiles: Iterable[Profile]) -> SearchScope:
        keys: set[str] = set()
        for profile in profiles:
            if not profile.paused:
                keys.update(profile.municipalities)
        return cls(frozenset(keys))
