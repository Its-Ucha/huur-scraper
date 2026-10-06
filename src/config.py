from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_path: Path
    user_agent: str
    max_workers: int
    request_timeout_seconds: int
    max_rent_eur: int
    min_size_m2: int
    preferred_bedrooms: int
    allow_close_match: bool
    allowed_cities: list[str]
    log_level: str
    log_file_path: Path | None
    log_to_console: bool
    discord_bot_token: str
    discord_guild_id: int | None
    discord_alert_channel_id: int | None
    discord_ops_channel_id: int | None
    discord_mention_user_ids: list[int]
    discord_control_user_ids: list[int]
    discord_control_role_id: int | None
    scrape_interval_minutes: int
    listings_clear_weekday: int | None
    listings_clear_hour_utc: int
    huur_category_name: str


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_cities(value: str) -> list[str]:
    if not value:
        return [
            "Den Haag",
            "Delft",
            "Rijswijk",
            "Voorburg",
            "Leidschendam",
            "Nootdorp",
            "Ypenburg",
            "'s-Gravenhage",
        ]
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_optional_id(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a numeric Discord ID, got {raw!r}") from error


def _parse_id_list(name: str) -> list[int]:
    ids: list[int] = []
    for item in os.getenv(name, "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.append(int(item))
        except ValueError as error:
            raise ValueError(
                f"{name} must be comma-separated numeric Discord IDs, got {item!r}"
            ) from error
    return ids


def _parse_log_file_path() -> Path | None:
    raw = os.getenv("LOG_FILE_PATH", "logs/huur_scraper.log").strip()
    return Path(raw) if raw else None


def _parse_interval_minutes() -> int:
    value = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "10"))
    if value < 1:
        raise ValueError(f"SCRAPE_INTERVAL_MINUTES must be at least 1, got {value}")
    return value


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
CLEAR_DISABLED_VALUES = {"off", "none", "never", "false", "0"}


def _parse_clear_weekday() -> int | None:
    raw = os.getenv("LISTINGS_CLEAR_DAY", "").strip().lower() or "monday"
    if raw in CLEAR_DISABLED_VALUES:
        return None
    for index, name in enumerate(WEEKDAYS):
        if raw in (name, name[:3]):
            return index
    raise ValueError(f"LISTINGS_CLEAR_DAY must be a weekday name or 'off', got {raw!r}")


def _parse_clear_hour() -> int:
    value = int(os.getenv("LISTINGS_CLEAR_HOUR_UTC", "").strip() or "4")
    if not 0 <= value <= 23:
        raise ValueError(f"LISTINGS_CLEAR_HOUR_UTC must be between 0 and 23, got {value}")
    return value


def load_settings() -> Settings:
    load_dotenv()

    database_path = Path(os.getenv("DATABASE_PATH", "data/huur_scraper.db"))
    return Settings(
        database_path=database_path,
        user_agent=os.getenv("USER_AGENT", "huur-scraper/0.1"),
        max_workers=int(os.getenv("MAX_WORKERS", "2")),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20")),
        max_rent_eur=int(os.getenv("MAX_RENT_EUR", "1000")),
        min_size_m2=int(os.getenv("MIN_SIZE_M2", "40")),
        preferred_bedrooms=int(os.getenv("PREFERRED_BEDROOMS", "2")),
        allow_close_match=_parse_bool(os.getenv("ALLOW_CLOSE_MATCH", "true"), True),
        allowed_cities=_parse_cities(
            os.getenv(
                "ALLOWED_CITIES",
                "Den Haag,Delft,Rijswijk,Voorburg,Leidschendam,Nootdorp,Ypenburg,'s-Gravenhage",
            )
        ),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_file_path=_parse_log_file_path(),
        log_to_console=_parse_bool(os.getenv("LOG_TO_CONSOLE", "true"), True),
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", "").strip(),
        discord_guild_id=_parse_optional_id("DISCORD_GUILD_ID"),
        discord_alert_channel_id=_parse_optional_id("DISCORD_ALERT_CHANNEL_ID"),
        discord_ops_channel_id=_parse_optional_id("DISCORD_OPS_CHANNEL_ID"),
        discord_mention_user_ids=_parse_id_list("DISCORD_MENTION_USER_ID"),
        discord_control_user_ids=_parse_id_list("DISCORD_CONTROL_USER_IDS"),
        discord_control_role_id=_parse_optional_id("DISCORD_CONTROL_ROLE_ID"),
        scrape_interval_minutes=_parse_interval_minutes(),
        listings_clear_weekday=_parse_clear_weekday(),
        listings_clear_hour_utc=_parse_clear_hour(),
        huur_category_name=os.getenv("HUUR_CATEGORY_NAME", "").strip() or "Huur",
    )
