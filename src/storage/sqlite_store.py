from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.models.listing import Listing
from src.models.profile import Profile


logger = logging.getLogger(__name__)

PROFILE_COLUMNS = (
    "id, owner_user_id, channel_id, max_rent_eur, min_size_m2, preferred_bedrooms, "
    "allow_close_match, municipalities, paused"
)


def _utc_now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


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
        # WAL makes the per-listing commits cheap; NORMAL is still crash-safe in WAL mode.
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _init_db(self) -> None:
        logger.info("Initializing SQLite database at %s", self.database_path)
        with self._connect() as connection:
            # WAL lets slash commands read while the scrape cycle writes. The mode is
            # stored in the database file, so this converts existing databases too.
            connection.execute("PRAGMA journal_mode = WAL")
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL UNIQUE,
                    channel_id INTEGER NOT NULL,
                    max_rent_eur INTEGER NOT NULL,
                    min_size_m2 INTEGER NOT NULL,
                    preferred_bedrooms INTEGER NOT NULL,
                    allow_close_match INTEGER NOT NULL,
                    municipalities TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_alerts (
                    profile_id INTEGER NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, dedupe_key)
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

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> Profile:
        return Profile(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            channel_id=row["channel_id"],
            max_rent_eur=row["max_rent_eur"],
            min_size_m2=row["min_size_m2"],
            preferred_bedrooms=row["preferred_bedrooms"],
            allow_close_match=bool(row["allow_close_match"]),
            municipalities=tuple(json.loads(row["municipalities"])),
            paused=bool(row["paused"]),
        )

    @staticmethod
    def _row_to_listing(row: sqlite3.Row) -> Listing:
        return Listing(
            source_site=row["source_site"],
            source_listing_id=row["source_listing_id"],
            source_url=row["source_url"],
            title=row["title"],
            city=row["city"],
            rent_price=row["rent_price"],
            living_area_m2=row["living_area_m2"],
            rooms_total=row["rooms_total"],
            bedrooms=row["bedrooms"],
            available_from=row["available_from"],
            raw_features=json.loads(row["raw_features"] or "{}"),
            is_available=bool(row["is_available"]),
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            last_changed_at=row["last_changed_at"],
            listing_status=row["listing_status"],
        )

    def create_profile(
        self,
        *,
        owner_user_id: int,
        channel_id: int,
        max_rent_eur: int,
        min_size_m2: int,
        preferred_bedrooms: int,
        allow_close_match: bool,
        municipalities: tuple[str, ...],
    ) -> Profile:
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO profiles (
                    owner_user_id, channel_id, max_rent_eur, min_size_m2, preferred_bedrooms,
                    allow_close_match, municipalities, paused, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    owner_user_id,
                    channel_id,
                    max_rent_eur,
                    min_size_m2,
                    preferred_bedrooms,
                    1 if allow_close_match else 0,
                    json.dumps(list(municipalities)),
                    now,
                    now,
                ),
            )
            profile_id = cursor.lastrowid
        profile = self.get_profile_by_id(profile_id)
        assert profile is not None
        return profile

    def get_profile(self, owner_user_id: int) -> Profile | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {PROFILE_COLUMNS} FROM profiles WHERE owner_user_id = ?", (owner_user_id,)
            ).fetchone()
        return self._row_to_profile(row) if row is not None else None

    def get_profile_by_id(self, profile_id: int) -> Profile | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {PROFILE_COLUMNS} FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        return self._row_to_profile(row) if row is not None else None

    def list_profiles(self, active_only: bool = False) -> list[Profile]:
        query = f"SELECT {PROFILE_COLUMNS} FROM profiles"
        if active_only:
            query += " WHERE paused = 0"
        with self._connect() as connection:
            rows = connection.execute(query + " ORDER BY id").fetchall()
        return [self._row_to_profile(row) for row in rows]

    def update_profile(self, profile: Profile) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE profiles
                SET channel_id = ?, max_rent_eur = ?, min_size_m2 = ?, preferred_bedrooms = ?,
                    allow_close_match = ?, municipalities = ?, paused = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    profile.channel_id,
                    profile.max_rent_eur,
                    profile.min_size_m2,
                    profile.preferred_bedrooms,
                    1 if profile.allow_close_match else 0,
                    json.dumps(list(profile.municipalities)),
                    1 if profile.paused else 0,
                    _utc_now(),
                    profile.id,
                ),
            )

    def set_profile_paused(self, profile_id: int, paused: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE profiles SET paused = ?, updated_at = ? WHERE id = ?",
                (1 if paused else 0, _utc_now(), profile_id),
            )

    def delete_profile(self, profile_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM profile_alerts WHERE profile_id = ?", (profile_id,))
            connection.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    def get_alerted_keys(self, profile_id: int) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT dedupe_key FROM profile_alerts WHERE profile_id = ?", (profile_id,)
            ).fetchall()
        return {row["dedupe_key"] for row in rows}

    def record_alert(self, profile_id: int, dedupe_key: str, sent_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO profile_alerts (profile_id, dedupe_key, sent_at) VALUES (?, ?, ?)",
                (profile_id, dedupe_key, sent_at),
            )

    def mark_all_listings_sent(self, profile_id: int, sent_at: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO profile_alerts (profile_id, dedupe_key, sent_at)
                SELECT ?, dedupe_key, ? FROM listings
                """,
                (profile_id, sent_at),
            )
            return cursor.rowcount

    def get_dispatch_candidates(self, seen_since: str) -> list[tuple[str, Listing]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM listings
                WHERE is_available = 1 AND last_seen_at >= ?
                ORDER BY first_seen_at, dedupe_key
                """,
                (seen_since,),
            ).fetchall()
        return [(row["dedupe_key"], self._row_to_listing(row)) for row in rows]
