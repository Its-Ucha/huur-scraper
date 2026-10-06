from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.storage.sqlite_store import SQLiteStore
from tests.helpers import make_listing


SENT_AT = "2026-10-06T12:00:00+00:00"


class ProfileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = SQLiteStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create(self, **overrides):
        values = dict(
            owner_user_id=42,
            channel_id=100,
            max_rent_eur=1000,
            min_size_m2=40,
            preferred_bedrooms=2,
            allow_close_match=True,
            municipalities=("delft", "den-haag"),
        )
        values.update(overrides)
        return self.store.create_profile(**values)

    def test_create_and_get(self) -> None:
        profile = self.create()
        self.assertEqual(self.store.get_profile(42), profile)
        self.assertEqual(self.store.get_profile_by_id(profile.id), profile)
        self.assertEqual(profile.municipalities, ("delft", "den-haag"))
        self.assertTrue(profile.allow_close_match)
        self.assertFalse(profile.paused)
        self.assertIsNone(self.store.get_profile(7))

    def test_one_profile_per_owner(self) -> None:
        self.create()
        with self.assertRaises(sqlite3.IntegrityError):
            self.create(channel_id=200)

    def test_update_and_pause(self) -> None:
        profile = self.create()
        updated = replace(profile, max_rent_eur=1200, municipalities=("leiden",), allow_close_match=False)
        self.store.update_profile(updated)
        self.assertEqual(self.store.get_profile(42), updated)

        self.store.set_profile_paused(profile.id, True)
        self.assertTrue(self.store.get_profile(42).paused)
        self.assertEqual(self.store.list_profiles(active_only=True), [])
        self.assertEqual(len(self.store.list_profiles()), 1)

    def test_empty_municipalities_round_trip(self) -> None:
        profile = self.create(municipalities=())
        self.assertEqual(self.store.get_profile(42).municipalities, ())
        self.assertEqual(profile.municipalities, ())

    def test_delete_removes_only_its_alerts(self) -> None:
        profile = self.create()
        other = self.create(owner_user_id=43, channel_id=101)
        self.store.record_alert(profile.id, "k1", SENT_AT)
        self.store.record_alert(other.id, "k1", SENT_AT)
        self.store.delete_profile(profile.id)
        self.assertIsNone(self.store.get_profile(42))
        self.assertEqual(self.store.get_alerted_keys(profile.id), set())
        self.assertEqual(self.store.get_alerted_keys(other.id), {"k1"})

    def test_record_alert_is_idempotent(self) -> None:
        profile = self.create()
        self.store.record_alert(profile.id, "k1", SENT_AT)
        self.store.record_alert(profile.id, "k1", SENT_AT)
        self.assertEqual(self.store.get_alerted_keys(profile.id), {"k1"})

    def test_mark_all_listings_sent(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="1"))
        self.store.upsert_listing(make_listing(source_listing_id="2"))
        profile = self.create()
        self.assertEqual(self.store.mark_all_listings_sent(profile.id, SENT_AT), 2)
        self.assertEqual(len(self.store.get_alerted_keys(profile.id)), 2)

    def test_dispatch_candidates_only_recent_and_available(self) -> None:
        older = make_listing(source_listing_id="older", raw_features={"street": "A"})
        newer = make_listing(source_listing_id="newer")
        gone = make_listing(source_listing_id="gone", is_available=False)
        stale = make_listing(source_listing_id="stale")
        for listing in (older, newer, gone, stale):
            self.store.upsert_listing(listing)
        connection = sqlite3.connect(self.db_path)
        with connection:
            connection.execute(
                "UPDATE listings SET last_seen_at = ? WHERE source_listing_id = 'stale'",
                ("2020-01-01T00:00:00+00:00",),
            )
            connection.execute(
                "UPDATE listings SET first_seen_at = ? WHERE source_listing_id = 'older'",
                ("2020-01-01T00:00:00+00:00",),
            )
        connection.close()

        since = (dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
        candidates = self.store.get_dispatch_candidates(seen_since=since)
        self.assertEqual([listing.source_listing_id for _, listing in candidates], ["older", "newer"])
        key, listing = candidates[0]
        self.assertEqual(key, older.dedupe_key())
        self.assertEqual(listing.raw_features, {"street": "A"})
        self.assertTrue(listing.is_available)

    def test_reopening_existing_database_keeps_profiles(self) -> None:
        self.create()
        self.assertEqual(len(SQLiteStore(self.db_path).list_profiles()), 1)


if __name__ == "__main__":
    unittest.main()
