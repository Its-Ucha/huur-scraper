from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

import discord

from src.notify.base import LogNotifier
from src.notify.discord_notifier import (
    CLOSE_MATCH_COLOR,
    HARD_MATCH_COLOR,
    DiscordNotifier,
    build_listing_embed,
    build_ops_embed,
)
from tests.helpers import make_listing, make_match


class FakeChannel:
    def __init__(self, channel_id: int, error: Exception | None = None, hang: bool = False) -> None:
        self.id = channel_id
        self.error = error
        self.hang = hang
        self.sent: list[dict] = []

    async def send(self, **kwargs):
        if self.hang:
            await asyncio.sleep(3600)
        if self.error is not None:
            raise self.error
        self.sent.append(kwargs)


def _fields(embed: discord.Embed) -> dict[str, str]:
    return {field.name: field.value for field in embed.fields}


class ListingEmbedTests(unittest.TestCase):
    def test_hard_match_fields_and_color(self) -> None:
        embed = build_listing_embed(make_listing(), make_match(hard=True, score=95))
        self.assertEqual(embed.title, "Teststraat 1")
        self.assertEqual(embed.url, "https://example.com/listing/1")
        self.assertEqual(embed.color, HARD_MATCH_COLOR)
        fields = _fields(embed)
        self.assertEqual(fields["Price"], "€900")
        self.assertEqual(fields["Area"], "50 m²")
        self.assertEqual(fields["Bedrooms"], "2")
        self.assertEqual(fields["City"], "Delft")
        self.assertEqual(fields["Site"], "alpha")
        self.assertEqual(fields["Score"], "95")
        self.assertEqual(embed.footer.text, "Hard match")

    def test_close_match_color(self) -> None:
        embed = build_listing_embed(make_listing(), make_match(hard=False))
        self.assertEqual(embed.color, CLOSE_MATCH_COLOR)
        self.assertEqual(embed.footer.text, "Close match")

    def test_missing_values_render_na(self) -> None:
        listing = make_listing(rent_price=None, living_area_m2=None, bedrooms=None, city=None)
        fields = _fields(build_listing_embed(listing, make_match()))
        for name in ("Price", "Area", "Bedrooms", "City"):
            self.assertEqual(fields[name], "n/a")

    def test_long_title_truncated_to_discord_limit(self) -> None:
        embed = build_listing_embed(make_listing(title="x" * 400), make_match())
        self.assertLessEqual(len(embed.title), 256)
        self.assertTrue(embed.title.endswith("…"))

    def test_empty_title_and_relative_url(self) -> None:
        embed = build_listing_embed(make_listing(title="", source_url="/woning/1"), make_match())
        self.assertEqual(embed.title, "(untitled)")
        self.assertIsNone(embed.url)

    def test_ops_embed_truncates_description(self) -> None:
        embed = build_ops_embed("e" * 5000)
        self.assertLessEqual(len(embed.description), 4096)


class DiscordNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()

    def test_hard_match_mentions_user(self) -> None:
        alert = FakeChannel(1)
        notifier = DiscordNotifier(self.loop, alert, mention_user_ids=[42])
        notifier.notify_listing(make_listing(), make_match(hard=True))
        self.assertEqual(len(alert.sent), 1)
        self.assertEqual(alert.sent[0]["content"], "<@42>")
        self.assertIsInstance(alert.sent[0]["embed"], discord.Embed)

    def test_hard_match_mentions_every_user(self) -> None:
        alert = FakeChannel(1)
        notifier = DiscordNotifier(self.loop, alert, mention_user_ids=[42, 43])
        notifier.notify_listing(make_listing(), make_match(hard=True))
        self.assertEqual(alert.sent[0]["content"], "<@42> <@43>")

    def test_close_match_does_not_mention(self) -> None:
        alert = FakeChannel(1)
        notifier = DiscordNotifier(self.loop, alert, mention_user_ids=[42, 43])
        notifier.notify_listing(make_listing(), make_match(hard=False))
        self.assertIsNone(alert.sent[0]["content"])

    def test_hard_match_without_mention_user(self) -> None:
        alert = FakeChannel(1)
        DiscordNotifier(self.loop, alert).notify_listing(make_listing(), make_match(hard=True))
        self.assertIsNone(alert.sent[0]["content"])

    def test_ops_goes_to_ops_channel(self) -> None:
        alert, ops = FakeChannel(1), FakeChannel(2)
        DiscordNotifier(self.loop, alert, ops).notify_ops("[SOURCE_ERROR] x - boom")
        self.assertEqual(alert.sent, [])
        self.assertEqual(ops.sent[0]["embed"].description, "[SOURCE_ERROR] x - boom")

    def test_ops_falls_back_to_alert_channel(self) -> None:
        alert = FakeChannel(1)
        DiscordNotifier(self.loop, alert).notify_ops("hello")
        self.assertEqual(len(alert.sent), 1)

    def test_send_error_is_logged_not_raised_and_next_send_works(self) -> None:
        failing = FakeChannel(1, error=RuntimeError("missing access"))
        notifier = DiscordNotifier(self.loop, failing)
        with self.assertLogs("src.notify.discord_notifier", level="ERROR"):
            notifier.notify_listing(make_listing(), make_match())
        failing.error = None
        notifier.notify_listing(make_listing(), make_match())
        self.assertEqual(len(failing.sent), 1)

    def test_send_timeout_is_logged_not_raised(self) -> None:
        hanging = FakeChannel(1, hang=True)
        notifier = DiscordNotifier(self.loop, hanging)
        with patch("src.notify.discord_notifier.SEND_TIMEOUT_SECONDS", 0.2):
            with self.assertLogs("src.notify.discord_notifier", level="ERROR"):
                notifier.notify_ops("slow")


class LogNotifierTests(unittest.TestCase):
    def test_logs_listing_and_ops(self) -> None:
        notifier = LogNotifier()
        with self.assertLogs("src.notify.base", level="INFO") as captured:
            notifier.notify_listing(make_listing(), make_match())
            notifier.notify_ops("[SOURCE_ERROR] x")
        output = "\n".join(captured.output)
        self.assertIn("HARD_MATCH", output)
        self.assertIn("[SOURCE_ERROR] x", output)


if __name__ == "__main__":
    unittest.main()
