from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.models.mark import APPLIED, NOT_INTERESTED, ListingMark
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import make_listing


T1 = "2026-10-06T12:00:00+00:00"
T2 = "2026-10-07T12:00:00+00:00"


class MarkStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = SQLiteStore(self.db_path)
        self.profile = self.store.create_profile(
            owner_user_id=42,
            channel_id=100,
            max_rent_eur=1000,
            min_size_m2=40,
            preferred_bedrooms=2,
            allow_close_match=True,
            municipalities=("delft",),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_set_replace_and_clear(self) -> None:
        self.store.set_mark(self.profile.id, "alpha:1", APPLIED, T1)
        self.assertEqual(self.store.get_marks(self.profile.id), {"alpha:1": ListingMark(APPLIED, T1)})

        self.store.set_mark(self.profile.id, "alpha:1", NOT_INTERESTED, T2)
        self.assertEqual(self.store.get_marks(self.profile.id), {"alpha:1": ListingMark(NOT_INTERESTED, T2)})

        self.store.clear_mark(self.profile.id, "alpha:1")
        self.assertEqual(self.store.get_marks(self.profile.id), {})

    def test_unknown_state_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_mark(self.profile.id, "alpha:1", "maybe", T1)

    def test_marks_are_per_profile(self) -> None:
        other = self.store.create_profile(
            owner_user_id=43,
            channel_id=101,
            max_rent_eur=1000,
            min_size_m2=40,
            preferred_bedrooms=2,
            allow_close_match=True,
            municipalities=("delft",),
        )
        self.store.set_mark(self.profile.id, "alpha:1", APPLIED, T1)
        self.assertEqual(self.store.get_marks(other.id), {})

    def test_delete_profile_removes_marks(self) -> None:
        self.store.set_mark(self.profile.id, "alpha:1", APPLIED, T1)
        self.store.delete_profile(self.profile.id)
        connection = sqlite3.connect(self.db_path)
        count = connection.execute("SELECT COUNT(*) FROM listing_marks").fetchone()[0]
        connection.close()
        self.assertEqual(count, 0)

    def test_latest_listing_follows_price_change(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="1", rent_price=950))
        self.store.upsert_listing(make_listing(source_listing_id="1", rent_price=900))
        self.store.upsert_listing(make_listing(source_listing_id="2", rent_price=800))
        self.assertEqual(self.store.get_latest_listing("alpha:1").rent_price, 900)
        self.assertIsNone(self.store.get_latest_listing("alpha:missing"))

    def test_marked_listings_by_state_newest_mark_first(self) -> None:
        for listing_id in ("1", "2", "3"):
            self.store.upsert_listing(make_listing(source_listing_id=listing_id))
        self.store.upsert_listing(make_listing(source_listing_id="1", rent_price=850))
        self.store.set_mark(self.profile.id, "alpha:1", APPLIED, T1)
        self.store.set_mark(self.profile.id, "alpha:2", APPLIED, T2)
        self.store.set_mark(self.profile.id, "alpha:3", NOT_INTERESTED, T1)
        self.store.set_mark(self.profile.id, "alpha:gone", APPLIED, T2)

        applied = self.store.get_marked_listings(self.profile.id, APPLIED)
        self.assertEqual(
            [(listing.source_listing_id, listing.rent_price, mark.marked_at) for listing, mark in applied],
            [("2", 900, T2), ("1", 850, T1)],
        )
        dismissed = self.store.get_marked_listings(self.profile.id, NOT_INTERESTED)
        self.assertEqual([listing.source_listing_id for listing, _ in dismissed], ["3"])


if __name__ == "__main__":
    unittest.main()
