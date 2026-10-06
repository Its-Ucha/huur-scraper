from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from src.config import Settings


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
