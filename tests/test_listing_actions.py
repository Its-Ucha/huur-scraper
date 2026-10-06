from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from src.bot.checks import owns_alert_channel
from src.bot.listing_actions import (
    UNDO,
    ListingActionButton,
    action_custom_id,
    build_listing_view,
    handle_listing_action,
)
from src.models.mark import APPLIED, NOT_INTERESTED, ListingMark
from src.notify.discord_notifier import APPLIED_COLOR, HARD_MATCH_COLOR, NOT_INTERESTED_COLOR
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import make_listing, make_profile


def custom_ids(view) -> list[str]:
    return [item.custom_id for item in view.children]


class ListingViewTests(unittest.TestCase):
    def test_unmarked_listing_gets_both_buttons(self) -> None:
        view = build_listing_view(make_listing(), None)
        self.assertIsNone(view.timeout)
        self.assertEqual(custom_ids(view), ["ls:applied:alpha:1", "ls:not_interested:alpha:1"])
        self.assertEqual([item.item.label for item in view.children], ["Applied", "Not interested"])

    def test_marked_listing_gets_undo(self) -> None:
        view = build_listing_view(make_listing(), ListingMark(APPLIED, "2026-10-06T00:00:00+00:00"))
        self.assertEqual(custom_ids(view), ["ls:undo:alpha:1"])

    def test_too_long_ref_gets_no_buttons(self) -> None:
        with self.assertLogs("src.bot.listing_actions", level="WARNING"):
            self.assertIsNone(build_listing_view(make_listing(source_listing_id="x" * 90), None))

    def test_template_matches_every_action(self) -> None:
        pattern = ListingActionButton.__discord_ui_compiled_template__
        for action in (APPLIED, NOT_INTERESTED, UNDO):
            match = pattern.fullmatch(action_custom_id(action, "ikwilhuren:some-street:12"))
            self.assertEqual((match["action"], match["ref"]), (action, "ikwilhuren:some-street:12"))
        self.assertIsNone(pattern.fullmatch("ls:maybe:alpha:1"))

    def test_owns_alert_channel(self) -> None:
        profile = make_profile(channel_id=100)
        self.assertTrue(owns_alert_channel(profile, 100))
        self.assertFalse(owns_alert_channel(profile, 101))
        self.assertFalse(owns_alert_channel(None, 100))
        self.assertFalse(owns_alert_channel(profile, None))


class HandleListingActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = SQLiteStore(Path(self.tmp.name) / "test.db")
        self.profile = self.store.create_profile(
            owner_user_id=42,
            channel_id=100,
            max_rent_eur=1000,
            min_size_m2=40,
            preferred_bedrooms=2,
            allow_close_match=True,
            municipalities=("delft",),
        )
        self.store.upsert_listing(make_listing())
        self.bot = SimpleNamespace(store=self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def interaction(self, user_id=42, channel_id=100, pinned=False, pin_error=None):
        message = SimpleNamespace(
            id=555, pinned=pinned, pin=AsyncMock(side_effect=pin_error), unpin=AsyncMock()
        )
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            channel_id=channel_id,
            message=message,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    def press(self, interaction, action):
        asyncio.run(handle_listing_action(self.bot, interaction, action, "alpha:1"))
        return interaction.edit_original_response.call_args.kwargs if interaction.edit_original_response.called else None

    def test_applied_marks_restyles_and_pins(self) -> None:
        interaction = self.interaction()
        edit = self.press(interaction, APPLIED)
        self.assertEqual(self.store.get_marks(self.profile.id)["alpha:1"].state, APPLIED)
        self.assertEqual(edit["embed"].color, APPLIED_COLOR)
        self.assertEqual(custom_ids(edit["view"]), ["ls:undo:alpha:1"])
        interaction.message.pin.assert_awaited_once()
        interaction.followup.send.assert_not_called()

    def test_not_interested_collapses_without_pin(self) -> None:
        interaction = self.interaction()
        edit = self.press(interaction, NOT_INTERESTED)
        self.assertEqual(self.store.get_marks(self.profile.id)["alpha:1"].state, NOT_INTERESTED)
        self.assertEqual(edit["embed"].color, NOT_INTERESTED_COLOR)
        interaction.message.pin.assert_not_called()

    def test_undo_clears_restores_and_unpins(self) -> None:
        self.store.set_mark(self.profile.id, "alpha:1", APPLIED, "2026-10-06T00:00:00+00:00")
        interaction = self.interaction(pinned=True)
        edit = self.press(interaction, UNDO)
        self.assertEqual(self.store.get_marks(self.profile.id), {})
        self.assertEqual(edit["embed"].color, HARD_MATCH_COLOR)
        self.assertEqual(custom_ids(edit["view"]), ["ls:applied:alpha:1", "ls:not_interested:alpha:1"])
        interaction.message.unpin.assert_awaited_once()

    def test_other_user_is_refused(self) -> None:
        for user_id, channel_id in ((99, 100), (42, 101)):
            with self.subTest(user_id=user_id, channel_id=channel_id):
                interaction = self.interaction(user_id=user_id, channel_id=channel_id)
                self.assertIsNone(self.press(interaction, APPLIED))
                self.assertEqual(self.store.get_marks(self.profile.id), {})
                self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])

    def test_pin_failure_keeps_mark_and_tells_owner(self) -> None:
        error = discord.HTTPException(SimpleNamespace(status=400, reason="Bad Request"), "Max pins reached")
        interaction = self.interaction(pin_error=error)
        with self.assertLogs("src.bot.listing_actions", level="WARNING"):
            self.press(interaction, APPLIED)
        self.assertEqual(self.store.get_marks(self.profile.id)["alpha:1"].state, APPLIED)
        self.assertIn("couldn't pin", interaction.followup.send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
