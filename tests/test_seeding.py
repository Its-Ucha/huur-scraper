from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.bot.seeding import seed_profile_from_settings
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import make_listing, make_settings


class SeedingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = SQLiteStore(Path(self.tmp.name) / "test.db")
        self.settings = make_settings(
            discord_alert_channel_id=100,
            discord_mention_user_ids=[42, 43],
            max_rent_eur=1100,
            allowed_cities=["Den Haag", "'s-Gravenhage", "Delft", "Voorburg", "Atlantis"],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_seeds_once_from_env_and_marks_existing_listings_sent(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="1"))
        self.store.upsert_listing(make_listing(source_listing_id="2"))
        with self.assertLogs("src.bot.seeding", level="WARNING") as captured:
            profile = seed_profile_from_settings(self.store, self.settings)
        self.assertIn("Atlantis", "\n".join(captured.output))
        self.assertEqual(profile.owner_user_id, 42)
        self.assertEqual(profile.channel_id, 100)
        self.assertEqual(profile.max_rent_eur, 1100)
        self.assertEqual(profile.municipalities, ("den-haag", "delft", "leidschendam-voorburg"))
        self.assertEqual(len(self.store.get_alerted_keys(profile.id)), 2)

        self.assertIsNone(seed_profile_from_settings(self.store, self.settings))
        self.assertEqual(len(self.store.list_profiles()), 1)

    def test_no_seed_without_channel_or_owner(self) -> None:
        for overrides in ({"discord_alert_channel_id": None}, {"discord_mention_user_ids": []}):
            with self.subTest(overrides=overrides):
                settings = make_settings(**{**dict(discord_alert_channel_id=100, discord_mention_user_ids=[42]), **overrides})
                self.assertIsNone(seed_profile_from_settings(self.store, settings))
        self.assertEqual(self.store.list_profiles(), [])


if __name__ == "__main__":
    unittest.main()
