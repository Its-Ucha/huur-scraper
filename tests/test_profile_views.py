from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.profile_views import CitiesView, NumbersModal, ProfilePanel
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import make_settings


def fake_interaction(user_id: int = 42):
    response = SimpleNamespace(
        edit_message=AsyncMock(),
        send_message=AsyncMock(),
        defer=AsyncMock(),
        send_modal=AsyncMock(),
    )
    return SimpleNamespace(user=SimpleNamespace(id=user_id), response=response)


class ProfileViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = SQLiteStore(Path(self.tmp.name) / "test.db")
        self.bot = MagicMock()
        self.bot.store = self.store
        self.bot.settings = make_settings()
        self.profile = self.store.create_profile(
            owner_user_id=42, channel_id=100, max_rent_eur=1000, min_size_m2=40,
            preferred_bedrooms=2, allow_close_match=True, municipalities=("delft",),
        )

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_panel_labels_follow_profile(self) -> None:
        panel = ProfilePanel(self.bot, self.profile)
        self.assertEqual(panel.toggle_close.label, "Close match: on")
        self.assertEqual(panel.toggle_pause.label, "Pause")

    async def test_panel_rejects_other_users(self) -> None:
        panel = ProfilePanel(self.bot, self.profile)
        interaction = fake_interaction(user_id=7)
        self.assertFalse(await panel.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once()

    async def test_cities_view_has_two_prefilled_pages(self) -> None:
        view = CitiesView(self.bot, self.profile)
        selects = [item for item in view.children if isinstance(item, discord.ui.Select)]
        self.assertEqual([len(select.options) for select in selects], [25, 25])
        defaults = [option.value for select in selects for option in select.options if option.default]
        self.assertEqual(defaults, ["delft"])

    async def test_cities_save_stores_union_and_dispatches(self) -> None:
        view = CitiesView(self.bot, self.profile)
        view.selected = [{"delft", "den-haag"}, {"rotterdam"}]
        await view.save(fake_interaction())
        saved = self.store.get_profile(42)
        self.assertEqual(set(saved.municipalities), {"delft", "den-haag", "rotterdam"})
        self.bot.schedule_dispatch.assert_called_once_with(saved.id)

    async def test_clearing_cities_does_not_dispatch(self) -> None:
        view = CitiesView(self.bot, self.profile)
        view.selected = [set(), set()]
        await view.save(fake_interaction())
        self.assertEqual(self.store.get_profile(42).municipalities, ())
        self.bot.schedule_dispatch.assert_not_called()

    async def test_numbers_modal_saves_valid_values(self) -> None:
        modal = NumbersModal(self.bot, self.profile)
        modal.max_rent._value = "€1200"
        modal.min_size._value = "50"
        modal.bedrooms._value = "3"
        await modal.on_submit(fake_interaction())
        saved = self.store.get_profile(42)
        self.assertEqual((saved.max_rent_eur, saved.min_size_m2, saved.preferred_bedrooms), (1200, 50, 3))
        self.bot.schedule_dispatch.assert_called_once_with(saved.id)

    async def test_numbers_modal_rejects_invalid_values(self) -> None:
        modal = NumbersModal(self.bot, self.profile)
        modal.max_rent._value = "abc"
        interaction = fake_interaction()
        await modal.on_submit(interaction)
        self.assertEqual(self.store.get_profile(42).max_rent_eur, 1000)
        self.assertIn("Not saved", interaction.response.send_message.call_args.args[0])
        self.bot.schedule_dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
