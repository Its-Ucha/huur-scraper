from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from src.filtering.rules import MatchResult, evaluate_listing
from src.models.listing import Listing
from src.models.profile import Profile
from src.notify.base import ChannelUnavailableError, Notifier
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)


@dataclass
class DispatchSummary:
    sent: int = 0
    failed: int = 0
    paused_profile_ids: list[int] = field(default_factory=list)


def stale_window(interval_minutes: int) -> dt.timedelta:
    # Long enough that one failed scrape doesn't drop a source's listings.
    return dt.timedelta(minutes=3 * interval_minutes)


def find_matches(
    candidates: list[tuple[str, Listing]], profile: Profile
) -> list[tuple[str, Listing, MatchResult]]:
    matches = []
    for key, listing in candidates:
        match = evaluate_listing(listing, profile)
        if match.is_hard_match or match.is_close_match:
            matches.append((key, listing, match))
    return matches


def dispatch(
    store: SQLiteStore,
    profiles: Sequence[Profile],
    notifier: Notifier,
    now: dt.datetime,
    stale_after: dt.timedelta,
    record: bool = True,
) -> DispatchSummary:
    """Send every current match a profile hasn't received yet.

    With record=False (CLI dry run) nothing is written, so the bot still sends them later.
    """
    summary = DispatchSummary()
    active = [profile for profile in profiles if not profile.paused]
    if not active:
        return summary
    candidates = store.get_dispatch_candidates(seen_since=(now - stale_after).isoformat())

    for profile in active:
        already_sent = store.get_alerted_keys(profile.id) if record else set()
        for key, listing, match in find_matches(candidates, profile):
            if key in already_sent:
                continue
            try:
                notifier.notify_listing(profile, listing, match)
            except ChannelUnavailableError as error:
                logger.warning("Profile=%s channel unavailable: %s", profile.id, error)
                if record:
                    store.set_profile_paused(profile.id, True)
                    notifier.notify_ops(
                        f"[PROFILE_ERROR] <@{profile.owner_user_id}>'s channel is unavailable; profile paused"
                    )
                summary.paused_profile_ids.append(profile.id)
                break
            except Exception:  # noqa: BLE001
                # Not recorded, so the next dispatch retries it.
                logger.exception("Sending listing=%s to profile=%s failed", key, profile.id)
                summary.failed += 1
                continue
            if record:
                store.record_alert(profile.id, key, now.isoformat())
            summary.sent += 1

    logger.info(
        "Dispatch profiles=%d sent=%d failed=%d paused=%s",
        len(active),
        summary.sent,
        summary.failed,
        summary.paused_profile_ids,
    )
    return summary


def recent_matches(
    store: SQLiteStore,
    profile: Profile,
    now: dt.datetime,
    stale_after: dt.timedelta,
    limit: int,
) -> list[Listing]:
    candidates = store.get_dispatch_candidates(seen_since=(now - stale_after).isoformat())
    matches = [listing for _, listing, _ in find_matches(candidates, profile)]
    return list(reversed(matches))[:limit]
