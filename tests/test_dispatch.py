from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.notify.base import ChannelUnavailableError
from src.notify.dispatch import dispatch, recent_matches, stale_window
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import RecordingNotifier, make_listing


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = SQLiteStore(self.db_path)
        self.notifier = RecordingNotifier()
        self.delft = self.make_profile(owner_user_id=42, channel_id=100, municipalities=("delft",))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_profile(self, **overrides):
        values = dict(
            owner_user_id=42,
            channel_id=100,
            max_rent_eur=1000,
            min_size_m2=40,
            preferred_bedrooms=2,
            allow_close_match=True,
            municipalities=("delft",),
        )
        values.update(overrides)
        return self.store.create_profile(**values)

    def run_dispatch(self, profiles=None, record=True):
        return dispatch(
            store=self.store,
            profiles=profiles if profiles is not None else self.store.list_profiles(active_only=True),
            notifier=self.notifier,
            now=dt.datetime.now(tz=dt.timezone.utc),
            stale_after=stale_window(10),
            record=record,
        )

    def sent_ids(self):
        return [listing.source_listing_id for _, listing, _ in self.notifier.listings]

    def test_backfill_sends_each_match_once(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a", city="Delft"))
        self.store.upsert_listing(make_listing(source_listing_id="b", city="Delft", rent_price=3000))
        self.store.upsert_listing(make_listing(source_listing_id="c", city="Delft", is_available=False))
        summary = self.run_dispatch()
        self.assertEqual((summary.sent, summary.failed), (1, 0))
        self.assertEqual(self.sent_ids(), ["a"])

        self.assertEqual(self.run_dispatch().sent, 0)
        self.assertEqual(self.sent_ids(), ["a"])

    def test_two_profiles_get_their_own_matches(self) -> None:
        leiden = self.make_profile(owner_user_id=43, channel_id=101, municipalities=("leiden",))
        self.store.upsert_listing(make_listing(source_listing_id="d", city="Delft"))
        self.store.upsert_listing(make_listing(source_listing_id="l", city="Leiden"))
        self.run_dispatch()
        routed = sorted((profile.id, listing.source_listing_id) for profile, listing, _ in self.notifier.listings)
        self.assertEqual(routed, [(self.delft.id, "d"), (leiden.id, "l")])

    def test_failed_send_is_not_recorded_and_retried(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a"))
        self.store.upsert_listing(make_listing(source_listing_id="b"))
        self.notifier.fail_with[100] = TimeoutError()
        with self.assertLogs("src.notify.dispatch", level="ERROR"):
            summary = self.run_dispatch()
        self.assertEqual((summary.sent, summary.failed), (0, 2))
        self.assertEqual(self.store.get_alerted_keys(self.delft.id), set())

        del self.notifier.fail_with[100]
        self.assertEqual(self.run_dispatch().sent, 2)

    def test_unavailable_channel_pauses_profile_and_others_continue(self) -> None:
        other = self.make_profile(owner_user_id=43, channel_id=101)
        self.store.upsert_listing(make_listing(source_listing_id="a"))
        self.notifier.fail_with[100] = ChannelUnavailableError("gone")
        summary = self.run_dispatch()
        self.assertEqual(summary.paused_profile_ids, [self.delft.id])
        self.assertTrue(self.store.get_profile_by_id(self.delft.id).paused)
        self.assertEqual(self.notifier.ops, ["[PROFILE_ERROR] <@42>'s channel is unavailable; profile paused"])
        self.assertEqual([profile.id for profile, _, _ in self.notifier.listings], [other.id])

    def test_paused_profile_is_skipped(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a"))
        self.store.set_profile_paused(self.delft.id, True)
        self.assertEqual(self.run_dispatch(profiles=self.store.list_profiles()).sent, 0)

    def test_stale_listings_are_not_sent(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="old"))
        connection = sqlite3.connect(self.db_path)
        with connection:
            connection.execute("UPDATE listings SET last_seen_at = '2020-01-01T00:00:00+00:00'")
        connection.close()
        self.assertEqual(self.run_dispatch().sent, 0)

    def test_price_change_alerts_again(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a", rent_price=950))
        self.run_dispatch()
        self.store.upsert_listing(make_listing(source_listing_id="a", rent_price=900))
        self.assertEqual(self.run_dispatch().sent, 1)
        self.assertEqual([listing.rent_price for _, listing, _ in self.notifier.listings], [950, 900])

    def test_dry_run_does_not_record(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a"))
        self.notifier.fail_with[100] = ChannelUnavailableError("gone")
        self.run_dispatch(record=False)
        self.assertFalse(self.store.get_profile_by_id(self.delft.id).paused)
        del self.notifier.fail_with[100]
        self.run_dispatch(record=False)
        self.run_dispatch(record=False)
        self.assertEqual(self.sent_ids(), ["a", "a"])
        self.assertEqual(self.store.get_alerted_keys(self.delft.id), set())

    def test_recent_matches_newest_first_with_limit(self) -> None:
        for listing_id in ("a", "b", "c"):
            self.store.upsert_listing(make_listing(source_listing_id=listing_id))
        connection = sqlite3.connect(self.db_path)
        with connection:
            for index, listing_id in enumerate(("a", "b", "c")):
                connection.execute(
                    "UPDATE listings SET first_seen_at = ? WHERE source_listing_id = ?",
                    (f"2026-10-0{index + 1}T00:00:00+00:00", listing_id),
                )
        connection.close()
        listings = recent_matches(
            self.store, self.delft, dt.datetime.now(tz=dt.timezone.utc), stale_window(10), limit=2
        )
        self.assertEqual([listing.source_listing_id for listing in listings], ["c", "b"])


if __name__ == "__main__":
    unittest.main()
