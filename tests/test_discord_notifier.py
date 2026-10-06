from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

from src.notify.base import ChannelUnavailableError, LogNotifier
from src.models.mark import APPLIED, NOT_INTERESTED, ListingMark
from src.notify.discord_notifier import (
    APPLIED_COLOR,
    CLOSE_MATCH_COLOR,
    HARD_MATCH_COLOR,
    NOT_INTERESTED_COLOR,
    DiscordNotifier,
    build_listing_embed,
    build_ops_embed,
)
from tests.helpers import make_listing, make_match, make_profile

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



def resolver(*channels: FakeChannel):
    by_id = {channel.id: channel for channel in channels}

    async def resolve(channel_id: int):
        return by_id.get(channel_id)

    return resolve


def http_error(cls, status: int, reason: str):
    return cls(SimpleNamespace(status=status, reason=reason), reason)

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
        listing = make_listing(rent_price=None, living_area_m2=None, city=None)
        fields = _fields(build_listing_embed(listing, make_match()))
        for name in ("Price", "Area", "City"):
            self.assertEqual(fields[name], "n/a")

    def test_extra_details_shown(self) -> None:
        listing = make_listing(
            rooms_total=3,
            bedrooms=2,
            available_from="2026-11-01T00:00:00.000Z",
            raw_features={
                "street": "Teststraat",
                "house_number": "12A",
                "postal_code": "2611 AB",
                "district": "Centrum",
                "asset_type": "Appartement",
                "furniture": "Gestoffeerd",
                "image_url": "https://cdn.example.com/1.jpg",
            },
        )
        embed = build_listing_embed(listing, make_match())
        fields = _fields(embed)
        self.assertEqual(fields["Rooms"], "3")
        self.assertEqual(fields["Bedrooms"], "2")
        self.assertEqual(fields["Available"], "1 Nov 2026")
        self.assertEqual(fields["Address"], "Teststraat 12A, 2611 AB · Centrum")
        self.assertEqual(fields["Type"], "Appartement")
        self.assertEqual(fields["Furnishing"], "Gestoffeerd")
        self.assertEqual(embed.thumbnail.url, "https://cdn.example.com/1.jpg")

    def test_optional_details_hidden_when_missing(self) -> None:
        listing = make_listing(rooms_total=None, bedrooms=None, available_from=None, raw_features={})
        embed = build_listing_embed(listing, make_match())
        fields = _fields(embed)
        for name in ("Rooms", "Bedrooms", "Available", "Address", "Type", "Furnishing"):
            self.assertNotIn(name, fields)
        self.assertIsNone(embed.thumbnail.url)
        self.assertTrue(all(value.strip() for value in fields.values()))

    def test_whitespace_only_details_hidden(self) -> None:
        listing = make_listing(
            available_from="  ",
            raw_features={"street": " ", "house_number": "", "asset_type": "  ", "furniture": " "},
        )
        fields = _fields(build_listing_embed(listing, make_match()))
        for name in ("Available", "Address", "Type", "Furnishing"):
            self.assertNotIn(name, fields)

    def test_vbent_property_type_used_for_type(self) -> None:
        listing = make_listing(raw_features={"property_type": "apartment"})
        self.assertEqual(_fields(build_listing_embed(listing, make_match()))["Type"], "apartment")

    def test_non_date_available_shown_as_text(self) -> None:
        listing = make_listing(available_from="Per direct")
        self.assertEqual(_fields(build_listing_embed(listing, make_match()))["Available"], "Per direct")

    def test_relative_image_url_not_used(self) -> None:
        listing = make_listing(raw_features={"image_url": "/images/x"})
        self.assertIsNone(build_listing_embed(listing, make_match()).thumbnail.url)

    def test_long_title_truncated_to_discord_limit(self) -> None:
        embed = build_listing_embed(make_listing(title="x" * 400), make_match())
        self.assertLessEqual(len(embed.title), 256)
        self.assertTrue(embed.title.endswith("…"))

    def test_empty_title_and_relative_url(self) -> None:
        embed = build_listing_embed(make_listing(title="", source_url="/woning/1"), make_match())
        self.assertEqual(embed.title, "(untitled)")
        self.assertIsNone(embed.url)

    def test_applied_mark_restyles_full_embed(self) -> None:
        mark = ListingMark(APPLIED, "2026-10-06T12:00:00+00:00")
        embed = build_listing_embed(make_listing(), make_match(hard=True), mark)
        self.assertEqual(embed.color, APPLIED_COLOR)
        self.assertEqual(embed.description, "✅ You applied on 6 Oct 2026")
        self.assertEqual(embed.footer.text, "Hard match · Applied")
        self.assertEqual(_fields(embed)["Price"], "€900")

    def test_not_interested_mark_collapses_embed(self) -> None:
        mark = ListingMark(NOT_INTERESTED, "2026-10-06T12:00:00+00:00")
        embed = build_listing_embed(make_listing(), make_match(), mark)
        self.assertEqual(embed.color, NOT_INTERESTED_COLOR)
        self.assertEqual(embed.title, "Teststraat 1")
        self.assertEqual(embed.url, "https://example.com/listing/1")
        self.assertEqual(embed.description, "€900 · 50 m² · Delft · alpha")
        self.assertEqual(embed.fields, [])
        self.assertIsNone(embed.thumbnail.url)
        self.assertEqual(embed.footer.text, "Not interested · 6 Oct 2026")

    def test_ops_embed_truncates_description(self) -> None:
        embed = build_ops_embed("e" * 5000)
        self.assertLessEqual(len(embed.description), 4096)


class DiscordNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.profile = make_profile(owner_user_id=42, channel_id=100)

    def tearDown(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()

    def test_hard_match_goes_to_profile_channel_and_mentions_owner(self) -> None:
        mine, other = FakeChannel(100), FakeChannel(200)
        notifier = DiscordNotifier(self.loop, resolver(mine, other))
        notifier.notify_listing(self.profile, make_listing(), make_match(hard=True))
        self.assertEqual(len(mine.sent), 1)
        self.assertEqual(other.sent, [])
        self.assertEqual(mine.sent[0]["content"], "<@42>")
        self.assertIsInstance(mine.sent[0]["embed"], discord.Embed)

    def test_view_factory_gets_listing_and_mark(self) -> None:
        mine = FakeChannel(100)
        calls = []

        def view_factory(listing, mark):
            calls.append((listing.ref(), mark))
            return "view"

        mark = ListingMark(APPLIED, "2026-10-06T12:00:00+00:00")
        notifier = DiscordNotifier(self.loop, resolver(mine), view_factory=view_factory)
        notifier.notify_listing(self.profile, make_listing(), make_match(), mark)
        self.assertEqual(calls, [("alpha:1", mark)])
        self.assertEqual(mine.sent[0]["view"], "view")
        self.assertEqual(mine.sent[0]["embed"].color, APPLIED_COLOR)

    def test_without_view_factory_sends_no_view(self) -> None:
        mine = FakeChannel(100)
        DiscordNotifier(self.loop, resolver(mine)).notify_listing(self.profile, make_listing(), make_match())
        self.assertIsNone(mine.sent[0]["view"])

    def test_close_match_does_not_mention(self) -> None:
        mine = FakeChannel(100)
        DiscordNotifier(self.loop, resolver(mine)).notify_listing(
            self.profile, make_listing(), make_match(hard=False)
        )
        self.assertIsNone(mine.sent[0]["content"])

    def test_missing_channel_raises_unavailable(self) -> None:
        notifier = DiscordNotifier(self.loop, resolver())
        with self.assertRaises(ChannelUnavailableError):
            notifier.notify_listing(self.profile, make_listing(), make_match())

    def test_forbidden_and_not_found_raise_unavailable(self) -> None:
        for error in (
            http_error(discord.Forbidden, 403, "Missing Access"),
            http_error(discord.NotFound, 404, "Unknown Channel"),
        ):
            with self.subTest(error=type(error).__name__):
                notifier = DiscordNotifier(self.loop, resolver(FakeChannel(100, error=error)))
                with self.assertRaises(ChannelUnavailableError):
                    notifier.notify_listing(self.profile, make_listing(), make_match())

    def test_other_send_error_propagates(self) -> None:
        notifier = DiscordNotifier(self.loop, resolver(FakeChannel(100, error=RuntimeError("boom"))))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            notifier.notify_listing(self.profile, make_listing(), make_match())

    def test_send_timeout_raises(self) -> None:
        notifier = DiscordNotifier(self.loop, resolver(FakeChannel(100, hang=True)))
        with patch("src.notify.discord_notifier.SEND_TIMEOUT_SECONDS", 0.2):
            with self.assertRaises(TimeoutError):
                notifier.notify_listing(self.profile, make_listing(), make_match())

    def test_ops_goes_to_ops_channel_with_mentions(self) -> None:
        ops = FakeChannel(2)
        DiscordNotifier(self.loop, resolver(), ops).notify_ops("[PROFILE_DELETED] x", [7, 8])
        self.assertEqual(ops.sent[0]["content"], "<@7> <@8>")
        self.assertEqual(ops.sent[0]["embed"].description, "[PROFILE_DELETED] x")

    def test_ops_without_mentions_has_no_content(self) -> None:
        ops = FakeChannel(2)
        DiscordNotifier(self.loop, resolver(), ops).notify_ops("[SOURCE_ERROR] x - boom")
        self.assertIsNone(ops.sent[0]["content"])

    def test_ops_without_channel_only_logs(self) -> None:
        with self.assertLogs("src.notify.discord_notifier", level="WARNING") as captured:
            DiscordNotifier(self.loop, resolver()).notify_ops("hello")
        self.assertIn("hello", "\n".join(captured.output))

    def test_ops_send_error_is_logged_not_raised(self) -> None:
        ops = FakeChannel(2, error=RuntimeError("missing access"))
        with self.assertLogs("src.notify.discord_notifier", level="ERROR"):
            DiscordNotifier(self.loop, resolver(), ops).notify_ops("hello")


class LogNotifierTests(unittest.TestCase):
    def test_logs_listing_and_ops(self) -> None:
        notifier = LogNotifier()
        with self.assertLogs("src.notify.base", level="INFO") as captured:
            notifier.notify_listing(make_profile(owner_user_id=42), make_listing(), make_match())
            notifier.notify_ops("[SOURCE_ERROR] x")
        output = "\n".join(captured.output)
        self.assertIn("HARD_MATCH", output)
        self.assertIn("owner=42", output)
        self.assertIn("[SOURCE_ERROR] x", output)


if __name__ == "__main__":
    unittest.main()
