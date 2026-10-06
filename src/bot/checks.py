from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Iterable

from src.config import Settings
from src.filtering.municipalities import Municipality


def is_control_user(user_id: int, role_ids: Iterable[int], settings: Settings) -> bool:
    if user_id in settings.discord_control_user_ids:
        return True
    role_id = settings.discord_control_role_id
    return role_id is not None and role_id in set(role_ids)


def validate_bot_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    if not settings.discord_bot_token:
        errors.append("DISCORD_BOT_TOKEN is not set")
    if settings.discord_guild_id is None:
        errors.append("DISCORD_GUILD_ID is not set")
    if not settings.discord_control_user_ids and settings.discord_control_role_id is None:
        errors.append(
            "Neither DISCORD_CONTROL_USER_IDS nor DISCORD_CONTROL_ROLE_ID is set; "
            "nobody could use /scrape, /pause or /resume"
        )
    return errors


def parse_sources(text: str | None, known: Iterable[str]) -> tuple[set[str] | None, list[str]]:
    names = {item.strip() for item in (text or "").split(",") if item.strip()}
    if not names:
        return None, []
    unknown = sorted(names - set(known))
    return names, unknown


def last_scheduled_clear(now: dt.datetime, weekday: int, hour: int) -> dt.datetime:
    """Most recent weekly clear slot (weekday at hour:00, in now's timezone) at or before now."""
    slot = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    slot -= dt.timedelta(days=(now.weekday() - weekday) % 7)
    if slot > now:
        slot -= dt.timedelta(days=7)
    return slot


PROFILE_NUMBER_FIELDS = (
    ("max_rent_eur", "Max rent", 100, 10000),
    ("min_size_m2", "Min size", 0, 500),
    ("preferred_bedrooms", "Preferred bedrooms", 0, 10),
)
MAX_CITY_PAGES = 4  # A view has 5 rows; one is needed for the buttons.


def validate_profile_numbers(
    max_rent: str, min_size: str, bedrooms: str
) -> tuple[dict[str, int] | None, list[str]]:
    values: dict[str, int] = {}
    errors: list[str] = []
    for (field, label, low, high), text in zip(PROFILE_NUMBER_FIELDS, (max_rent, min_size, bedrooms)):
        cleaned = text.strip().lstrip("€").strip()
        if not cleaned.isdigit():
            errors.append(f"{label} must be a whole number, got {text.strip()!r}")
            continue
        number = int(cleaned)
        if not low <= number <= high:
            errors.append(f"{label} must be between {low} and {high}, got {number}")
            continue
        values[field] = number
    return (None, errors) if errors else (values, [])


def sanitize_channel_name(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
    return f"huur-{slug or 'user'}"[:90]


def unique_channel_name(base: str, existing: Iterable[str]) -> str:
    taken = set(existing)
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"


def archived_channel_name(name: str) -> str:
    return f"archived-{name}"[:100]


def city_select_pages(
    municipalities: Iterable[Municipality], page_size: int = 25
) -> list[list[Municipality]]:
    ordered = sorted(municipalities, key=lambda item: item.name.lower())
    if not ordered:
        return []
    page_count = math.ceil(len(ordered) / page_size)
    if page_count > MAX_CITY_PAGES:
        raise ValueError(f"{len(ordered)} municipalities need more than {MAX_CITY_PAGES} select menus")
    per_page = math.ceil(len(ordered) / page_count)
    return [ordered[index:index + per_page] for index in range(0, len(ordered), per_page)]


def owns_alert_channel(profile, channel_id: int | None) -> bool:
    """Alert buttons work only for the owner of the profile whose channel the alert is in."""
    return profile is not None and channel_id is not None and profile.channel_id == channel_id
