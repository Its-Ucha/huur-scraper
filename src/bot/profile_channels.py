from __future__ import annotations

import discord

from src.bot.checks import archived_channel_name, sanitize_channel_name, unique_channel_name


CHANNEL_REASON = "Huur search profile"


async def create_profile_channel(guild, member, category_name: str, control_role_id: int | None):
    """Private alerts channel: the owner reads, only the bot posts."""
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        category = await guild.create_category(category_name, reason=CHANNEL_REASON)
    name = unique_channel_name(
        sanitize_channel_name(member.display_name),
        (channel.name for channel in guild.text_channels),
    )
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=False
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            read_message_history=True,
            manage_messages=True,
        ),
    }
    role = guild.get_role(control_role_id) if control_role_id is not None else None
    if role is not None:
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
    return await guild.create_text_channel(
        name, category=category, overwrites=overwrites, reason=CHANNEL_REASON
    )


async def archive_profile_channel(channel, owner) -> None:
    overwrites = dict(channel.overwrites)
    overwrites[owner] = discord.PermissionOverwrite(
        view_channel=True, read_message_history=True, send_messages=False
    )
    await channel.edit(
        name=archived_channel_name(channel.name),
        overwrites=overwrites,
        reason="Huur profile deleted",
    )
    await channel.send("Profile deleted; this channel is archived. An admin will remove it.")
