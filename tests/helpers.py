from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.filtering.rules import MatchResult
from src.models.listing import Listing
from src.models.profile import Profile


def make_settings(**overrides) -> Settings:
    values = dict(
        database_path=Path("unused.db"),
        user_agent="test-agent",
        max_workers=1,
        request_timeout_seconds=5,
        max_rent_eur=1000,
        min_size_m2=40,
        preferred_bedrooms=2,
        allow_close_match=True,
        allowed_cities=["Den Haag", "Delft"],
        log_level="INFO",
        log_file_path=None,
        log_to_console=False,
        discord_bot_token="",
        discord_guild_id=None,
        discord_alert_channel_id=None,
        discord_ops_channel_id=None,
        discord_mention_user_ids=[],
        discord_control_user_ids=[],
        discord_control_role_id=None,
        scrape_interval_minutes=10,
        listings_clear_weekday=None,
        listings_clear_hour_utc=4,
    )
    values.update(overrides)
    return Settings(**values)


def make_listing(**overrides) -> Listing:
    values = dict(
        source_site="alpha",
        source_listing_id="1",
        source_url="https://example.com/listing/1",
        title="Teststraat 1",
        city="Delft",
        rent_price=900,
        living_area_m2=50,
        rooms_total=3,
        bedrooms=2,
        available_from=None,
        raw_features={},
    )
    values.update(overrides)
    return Listing(**values)


def make_profile(**overrides) -> Profile:
    values = dict(
        id=1,
        owner_user_id=42,
        channel_id=100,
        max_rent_eur=1000,
        min_size_m2=40,
        preferred_bedrooms=2,
        allow_close_match=True,
        municipalities=("delft", "den-haag"),
        paused=False,
    )
    values.update(overrides)
    return Profile(**values)


def make_match(hard: bool = True, score: int = 95) -> MatchResult:
    return MatchResult(is_hard_match=hard, is_close_match=not hard, score=score, reasons=[])


class RecordingNotifier:
    def __init__(self) -> None:
        self.listings: list[tuple[Profile, Listing, MatchResult]] = []
        self.ops: list[str] = []
        self.ops_mentions: list[list[int]] = []
        # channel_id -> exception raised by notify_listing, to simulate failed sends.
        self.fail_with: dict[int, Exception] = {}

    def notify_listing(self, profile: Profile, listing: Listing, match: MatchResult) -> None:
        error = self.fail_with.get(profile.channel_id)
        if error is not None:
            raise error
        self.listings.append((profile, listing, match))

    def notify_ops(self, text: str, mention_user_ids=()) -> None:
        self.ops.append(text)
        self.ops_mentions.append(list(mention_user_ids))
