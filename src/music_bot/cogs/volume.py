"""/volume slash command + DB persistence. See SPEC §5.5."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..player import MusicPlayer
from ..utils import embeds
from ..utils.checks import can_control_player

if TYPE_CHECKING:
    from ..bot import MusicBotClient


class VolumeCog(commands.Cog):
    def __init__(self, bot: MusicBotClient) -> None:
        self.bot = bot

    @app_commands.command(
        name="volume",
        description="Show or change the playback volume (0-200).",
    )
    @app_commands.describe(level="Volume level 0-200 (omit to view current).")
    async def volume(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 0, 200] | None = None,
    ) -> None:
        if interaction.guild is None or self.bot.user is None:
            await interaction.response.send_message(
                embed=embeds.error("This command must be used in a server."), ephemeral=True
            )
            return

        player = interaction.guild.voice_client
        if not isinstance(player, MusicPlayer):
            stored = await self.bot.db.get_volume(interaction.guild.id, self.bot.user.id)
            current = stored if stored is not None else 100
            if level is None:
                await interaction.response.send_message(
                    embed=embeds.info(f"🎚️ Volume: **{current}%** (no active player)"),
                    ephemeral=True,
                )
                return
            await self.bot.db.set_volume(interaction.guild.id, self.bot.user.id, level)
            await interaction.response.send_message(
                embed=embeds.success(f"Saved volume: **{level}%** (no active player)"),
                ephemeral=True,
            )
            return

        allowed, reason = can_control_player(interaction.user, player.channel)
        if not allowed:
            await interaction.response.send_message(
                embed=embeds.error(reason or "Not allowed"), ephemeral=True
            )
            return

        if level is None:
            await interaction.response.send_message(
                embed=embeds.info(f"🎚️ Volume: **{player.volume}%**"), ephemeral=True
            )
            return

        await player.set_volume(level)
        await self.bot.db.set_volume(interaction.guild.id, self.bot.user.id, level)

        if player.panel is not None:
            await player.panel.refresh()

        await interaction.response.send_message(
            embed=embeds.success(f"Volume set to **{level}%**"), ephemeral=True
        )
