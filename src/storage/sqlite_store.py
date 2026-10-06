from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.models.listing import Listing


logger = logging.getLogger(__name__)


@dataclass
class UpsertResult:
    inserted: bool
    changed: bool


class SQLiteStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        logger.info("Initializing SQLite database at %s", self.database_path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    dedupe_key TEXT PRIMARY KEY,
                    source_site TEXT NOT NULL,
                    source_listing_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    city TEXT,
                    rent_price INTEGER,
                    living_area_m2 INTEGER,
                    rooms_total INTEGER,
                    bedrooms INTEGER,
                    available_from TEXT,
                    raw_features TEXT NOT NULL,
                    is_available INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    last_changed_at TEXT,
                    listing_status TEXT NOT NULL
                )
                """
            )

            # Migrate older DBs that were created before availability tracking.
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(listings)").fetchall()
            }
            if "is_available" not in existing_columns:
                connection.execute(
                    "ALTER TABLE listings ADD COLUMN is_available INTEGER NOT NULL DEFAULT 1"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_site TEXT NOT NULL,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def upsert_listing(self, listing: Listing) -> UpsertResult:
        listing.stamp_seen()
        key = listing.dedupe_key()
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM listings WHERE dedupe_key = ?", (key,)
            ).fetchone()

            raw_features = json.dumps(listing.raw_features, ensure_ascii=False)

            if current is None:
                connection.execute(
                    """
                    INSERT INTO listings (
                        dedupe_key, source_site, source_listing_id, source_url, title, city,
                        rent_price, living_area_m2, rooms_total, bedrooms, available_from,
                        raw_features, is_available, first_seen_at, last_seen_at, last_changed_at, listing_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        listing.source_site,
                        listing.source_listing_id,
                        listing.source_url,
                        listing.title,
                        listing.city,
                        listing.rent_price,
                        listing.living_area_m2,
                        listing.rooms_total,
                        listing.bedrooms,
                        listing.available_from,
                        raw_features,
                        1 if listing.is_available else 0,
                        listing.first_seen_at,
                        listing.last_seen_at,
                        listing.last_changed_at,
                        listing.listing_status,
                    ),
                )
                return UpsertResult(inserted=True, changed=True)

            changed = (
                current["title"] != listing.title
                or current["rent_price"] != listing.rent_price
                or current["living_area_m2"] != listing.living_area_m2
                or current["is_available"] != (1 if listing.is_available else 0)
                or current["listing_status"] != listing.listing_status
            )

            connection.execute(
                """
                UPDATE listings
                SET title = ?, source_url = ?, city = ?, rent_price = ?, living_area_m2 = ?,
                    rooms_total = ?, bedrooms = ?, available_from = ?, raw_features = ?,
                    is_available = ?, last_seen_at = ?, last_changed_at = ?, listing_status = ?
                WHERE dedupe_key = ?
                """,
                (
                    listing.title,
                    listing.source_url,
                    listing.city,
                    listing.rent_price,
                    listing.living_area_m2,
                    listing.rooms_total,
                    listing.bedrooms,
                    listing.available_from,
                    raw_features,
                    1 if listing.is_available else 0,
                    listing.last_seen_at,
                    listing.last_seen_at if changed else current["last_changed_at"],
                    listing.listing_status,
                    key,
                ),
            )
            return UpsertResult(inserted=False, changed=changed)

    def write_source_run(self, source_site: str, run_at: str, status: str, details: str) -> None:
        logger.info(
            "Recording source run source=%s status=%s details=%s",
            source_site,
            status,
            details,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO source_runs (source_site, run_at, status, details) VALUES (?, ?, ?, ?)",
                (source_site, run_at, status, details),
            )

    def get_recent_listings(self, limit: int = 25) -> list[sqlite3.Row]:
        safe_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT
                    source_site,
                    title,
                    city,
                    rent_price,
                    living_area_m2,
                    bedrooms,
                    source_url,
                    is_available,
                    first_seen_at,
                    last_seen_at,
                    listing_status
                FROM listings
                ORDER BY COALESCE(last_seen_at, first_seen_at) DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()

    def get_all_listings_for_prune(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT
                    dedupe_key,
                    source_site,
                    source_listing_id,
                    source_url,
                    title,
                    city,
                    rent_price,
                    living_area_m2,
                    rooms_total,
                    bedrooms,
                    available_from,
                    is_available,
                    listing_status
                FROM listings
                """
            ).fetchall()

    def delete_listings_by_keys(self, dedupe_keys: list[str]) -> int:
        if not dedupe_keys:
            return 0

        deleted = 0
        with self._connect() as connection:
            for key in dedupe_keys:
                cursor = connection.execute("DELETE FROM listings WHERE dedupe_key = ?", (key,))
                deleted += cursor.rowcount
        return deleted

    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM bot_state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row is not None else default

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bot_state (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_recent_source_statuses(self, per_source: int = 2) -> dict[str, list[str]]:
        """Return each source's last statuses, newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT source_site, status
                FROM (
                    SELECT source_site, status, id,
                        ROW_NUMBER() OVER (PARTITION BY source_site ORDER BY id DESC) AS rank
                    FROM source_runs
                )
                WHERE rank <= ?
                ORDER BY source_site, id DESC
                """,
                (per_source,),
            ).fetchall()
        statuses: dict[str, list[str]] = {}
        for row in rows:
            statuses.setdefault(row["source_site"], []).append(row["status"])
        return statuses

    def get_latest_source_runs(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT runs.source_site, runs.run_at, runs.status, runs.details
                FROM source_runs AS runs
                JOIN (
                    SELECT source_site, MAX(id) AS max_id
                    FROM source_runs
                    GROUP BY source_site
                ) AS latest ON runs.id = latest.max_id
                ORDER BY runs.source_site
                """
            ).fetchall()
