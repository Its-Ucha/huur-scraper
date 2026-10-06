from __future__ import annotations

import datetime as dt
import logging

from src.config import Settings
from src.filtering.municipalities import municipality_for_place
from src.models.profile import Profile
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)


def seed_profile_from_settings(store: SQLiteStore, settings: Settings) -> Profile | None:
    """Turn the single .env profile into the first stored profile, once.

    Everything already stored was alerted under the old single-profile setup, so it
    is marked as sent to avoid posting it all again.
    """
    if store.list_profiles():
        return None
    channel_id = settings.discord_alert_channel_id
    if channel_id is None or not settings.discord_mention_user_ids:
        return None

    municipalities: list[str] = []
    for city in settings.allowed_cities:
        key = municipality_for_place(city)
        if key is None:
            logger.warning("ALLOWED_CITIES entry %r is not a known Zuid-Holland place; skipped", city)
        elif key not in municipalities:
            municipalities.append(key)

    profile = store.create_profile(
        owner_user_id=settings.discord_mention_user_ids[0],
        channel_id=channel_id,
        max_rent_eur=settings.max_rent_eur,
        min_size_m2=settings.min_size_m2,
        preferred_bedrooms=settings.preferred_bedrooms,
        allow_close_match=settings.allow_close_match,
        municipalities=tuple(municipalities),
    )
    marked = store.mark_all_listings_sent(profile.id, dt.datetime.now(tz=dt.timezone.utc).isoformat())
    logger.info("Seeded profile=%s for user=%s; marked %d listings as sent", profile.id, profile.owner_user_id, marked)
    return profile
