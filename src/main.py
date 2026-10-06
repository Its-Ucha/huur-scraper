from __future__ import annotations

import argparse
import datetime as dt
import logging

from src.config import load_settings
from src.logging_setup import configure_logging
from src.notify.base import LogNotifier
from src.notify.dispatch import dispatch, stale_window
from src.scrapers.factories import REGISTRY_FILE, SOURCE_FACTORIES
from src.scrapers.runner import run_all_sources
from src.storage.sqlite_store import SQLiteStore


def _fmt(value: object) -> str:
    return "-" if value in (None, "") else str(value)


def print_listings(store: SQLiteStore, limit: int) -> None:
    rows = store.get_recent_listings(limit=limit)
    if not rows:
        print("No listings found in database yet.")
        return

    headers = ["Source", "Avail", "Price", "m2", "Beds", "City", "Title", "Seen", "URL"]
    records = []
    for row in rows:
        records.append(
            [
                _fmt(row["source_site"]),
                _fmt("yes" if row["is_available"] else "no"),
                _fmt(row["rent_price"]),
                _fmt(row["living_area_m2"]),
                _fmt(row["bedrooms"]),
                _fmt(row["city"]),
                _fmt(row["title"]),
                _fmt(row["last_seen_at"]),
                _fmt(row["source_url"]),
            ]
        )

    column_widths = [len(h) for h in headers]
    for record in records:
        for index, value in enumerate(record):
            column_widths[index] = min(max(column_widths[index], len(value)), 72)

    def fit(text: str, width: int) -> str:
        if len(text) <= width:
            return text.ljust(width)
        return (text[: max(0, width - 3)] + "...").ljust(width)

    print(" | ".join(fit(headers[i], column_widths[i]) for i in range(len(headers))))
    print("-+-".join("-" * column_widths[i] for i in range(len(headers))))
    for record in records:
        print(" | ".join(fit(record[i], column_widths[i]) for i in range(len(record))))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run rental source scraping")
    parser.add_argument("--once", action="store_true", help="Run one scraping cycle")
    parser.add_argument(
        "--listings",
        action="store_true",
        help="Show recent listings stored in SQLite and exit",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Number of rows for --listings (default: 25)",
    )
    parser.add_argument(
        "--sources",
        type=str,
        default="",
        help="Comma-separated source names (optional)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    configure_logging(settings)
    logger = logging.getLogger(__name__)
    logger.info("Starting run (once=%s, sources=%s)", args.once, args.sources or "all")

    store = SQLiteStore(settings.database_path)

    if args.listings:
        print_listings(store=store, limit=args.limit)
        logger.info("Printed recent listings (limit=%s)", args.limit)
        return

    selected_sources = (
        {item.strip() for item in args.sources.split(",") if item.strip()}
        if args.sources
        else None
    )

    notifier = LogNotifier()
    summary = run_all_sources(
        settings=settings,
        store=store,
        source_factories=SOURCE_FACTORIES,
        registry_file=REGISTRY_FILE,
        notifier=notifier,
        selected_sources=selected_sources,
    )
    # Dry run: matches are logged but not recorded, so the bot still sends them.
    result = dispatch(
        store=store,
        profiles=store.list_profiles(active_only=True),
        notifier=notifier,
        now=dt.datetime.now(tz=dt.timezone.utc),
        stale_after=stale_window(settings.scrape_interval_minutes),
        record=False,
    )
    logger.info(
        "Run completed sources=%d matches=%d (dry run, not recorded)",
        len(summary.results),
        result.sent,
    )


if __name__ == "__main__":
    main()
