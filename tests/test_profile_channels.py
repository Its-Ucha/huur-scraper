from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.profile_channels import archive_profile_channel, create_profile_channel


class ProfileChannelTests(unittest.IsolatedAsyncioTestCase):
    def make_guild(self, categories=()):
        guild = MagicMock()
        guild.categories = list(categories)
        guild.text_channels = [SimpleNamespace(name="huur-alice")]
        guild.default_role = "everyone"
        guild.me = "bot"
        guild.get_role.return_value = "admins"
        guild.create_category = AsyncMock(return_value=SimpleNamespace(name="Huur"))
        guild.create_text_channel = AsyncMock(return_value="channel")
        return guild

    async def test_creates_private_channel_in_new_category(self) -> None:
        guild = self.make_guild()
        member = MagicMock(display_name="Alice")
        channel = await create_profile_channel(guild, member, "Huur", 7)
        self.assertEqual(channel, "channel")
        guild.create_category.assert_awaited_once()
        args, kwargs = guild.create_text_channel.call_args
        self.assertEqual(args[0], "huur-alice-2")
        self.assertEqual(kwargs["category"].name, "Huur")
        overwrites = kwargs["overwrites"]
        self.assertFalse(overwrites["everyone"].view_channel)
        self.assertTrue(overwrites[member].view_channel)
        self.assertFalse(overwrites[member].send_messages)
        self.assertTrue(overwrites["bot"].send_messages)
        self.assertTrue(overwrites["admins"].view_channel)
        guild.get_role.assert_called_once_with(7)

    async def test_reuses_existing_category_and_skips_missing_role(self) -> None:
        category = MagicMock()
        category.name = "Huur"
        guild = self.make_guild(categories=[category])
        member = MagicMock(display_name="Bob")
        await create_profile_channel(guild, member, "Huur", None)
        guild.create_category.assert_not_called()
        kwargs = guild.create_text_channel.call_args.kwargs
        self.assertIs(kwargs["category"], category)
        self.assertNotIn("admins", kwargs["overwrites"])

    async def test_archive_renames_and_keeps_owner_read_only(self) -> None:
        channel = MagicMock()
        channel.name = "huur-alice"
        channel.overwrites = {"bot": discord.PermissionOverwrite(send_messages=True)}
        channel.edit = AsyncMock()
        channel.send = AsyncMock()
        owner = MagicMock(id=42)  # used as an overwrites key, so it must be hashable
        await archive_profile_channel(channel, owner)
        kwargs = channel.edit.call_args.kwargs
        self.assertEqual(kwargs["name"], "archived-huur-alice")
        self.assertFalse(kwargs["overwrites"][owner].send_messages)
        self.assertTrue(kwargs["overwrites"][owner].view_channel)
        self.assertTrue(kwargs["overwrites"]["bot"].send_messages)
        channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
