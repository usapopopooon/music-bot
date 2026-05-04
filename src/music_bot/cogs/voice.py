"""Voice connection management (`/join`, `/disconnect`) and auto-disconnect.

See SPEC §7.1.
"""

from __future__ import annotations

import asyncio
import gc
import logging
from typing import TYPE_CHECKING

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from ..player import MusicPlayer
from ..utils import embeds
from ..utils.checks import can_control_player, get_user_voice_channel, humans_in_channel

if TYPE_CHECKING:
    from ..bot import MusicBotClient

logger = logging.getLogger(__name__)

_AUTO_DISCONNECT_SEC = 5 * 60


class VoiceCog(commands.Cog):
    def __init__(self, bot: MusicBotClient) -> None:
        self.bot = bot
        self._auto_disconnect_tasks: dict[int, asyncio.Task[None]] = {}

    @app_commands.command(name="join", description="Have the bot join your voice channel.")
    async def join(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=embeds.error("This command must be used in a server."), ephemeral=True
            )
            return
        vc = get_user_voice_channel(interaction.user)
        if vc is None:
            await interaction.response.send_message(
                embed=embeds.error("Join a voice channel first."), ephemeral=True
            )
            return
        if interaction.guild.voice_client is not None:
            await interaction.response.send_message(
                embed=embeds.error("Already connected."), ephemeral=True
            )
            return

        if self.bot.at_player_capacity():
            await interaction.response.send_message(
                embed=embeds.error(
                    f"🛑 This bot has reached its concurrent-playback limit "
                    f"({self.bot.max_players})."
                ),
                ephemeral=True,
            )
            return

        player = await vc.connect(cls=MusicPlayer)
        player.autoplay = wavelink.AutoPlayMode.partial
        self.bot.increment_players()
        if self.bot.user is not None:
            stored = await self.bot.db.get_volume(interaction.guild.id, self.bot.user.id)
            if stored is not None:
                await player.set_volume(stored)
        await interaction.response.send_message(
            embed=embeds.success(f"Joined {vc.mention}"), ephemeral=True
        )

    @app_commands.command(name="disconnect", description="Disconnect the bot from voice.")
    async def disconnect(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=embeds.error("This command must be used in a server."), ephemeral=True
            )
            return
        player = interaction.guild.voice_client
        if not isinstance(player, MusicPlayer):
            await interaction.response.send_message(
                embed=embeds.error("Not connected."), ephemeral=True
            )
            return
        allowed, reason = can_control_player(interaction.user, player.channel)
        if not allowed:
            await interaction.response.send_message(
                embed=embeds.error(reason or "Not allowed"), ephemeral=True
            )
            return
        await self._teardown_player(player)
        await interaction.response.send_message(
            embed=embeds.success("Disconnected."), ephemeral=True
        )

    async def _teardown_player(self, player: MusicPlayer) -> None:
        """Hard cleanup. SPEC §7.9.4: collect, drop refs, then collect again."""
        guild_id = player.guild.id if player.guild else None
        if player.panel is not None:
            await player.panel.terminate()
        try:
            await player.disconnect()
        except Exception:
            logger.exception("disconnect failed", extra={"bot_name": self.bot.bot_name})
        try:
            player.queue.clear()
        except Exception:
            pass
        # Help GC: drop references that could form cycles.
        player.requesters = {}
        player.last_track = None
        player.text_channel = None
        player.panel = None

        self.bot.decrement_players()
        gc.collect(generation=2)

        if guild_id is not None:
            task = self._auto_disconnect_tasks.pop(guild_id, None)
            if task is not None and not task.done():
                task.cancel()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Schedule a 5-min auto-disconnect when the bot's VC becomes empty."""
        if member.bot:
            return
        guild = member.guild
        player = guild.voice_client
        if not isinstance(player, MusicPlayer) or player.channel is None:
            return
        # Affected only if the user left or was moved away from the bot's VC.
        if (before.channel and before.channel.id == player.channel.id) and (
            after.channel is None or after.channel.id != player.channel.id
        ):
            self._maybe_schedule_auto_disconnect(player)
        # Cancel scheduled disconnect if a human just joined the bot's VC.
        if after.channel and after.channel.id == player.channel.id:
            task = self._auto_disconnect_tasks.pop(guild.id, None)
            if task is not None and not task.done():
                task.cancel()

    def _maybe_schedule_auto_disconnect(self, player: MusicPlayer) -> None:
        """SPEC §7.1: schedule only when (no humans) AND (queue empty) AND (not playing)."""
        if player.channel is None or player.guild is None:
            return
        if humans_in_channel(player.channel) > 0:
            return
        if player.playing or not player.queue.is_empty:
            return
        guild_id = player.guild.id
        if guild_id in self._auto_disconnect_tasks and not self._auto_disconnect_tasks[guild_id].done():
            return
        self._auto_disconnect_tasks[guild_id] = asyncio.create_task(
            self._auto_disconnect_after_idle(player),
            name=f"auto-disconnect-{guild_id}",
        )

    async def _auto_disconnect_after_idle(self, player: MusicPlayer) -> None:
        try:
            await asyncio.sleep(_AUTO_DISCONNECT_SEC)
        except asyncio.CancelledError:
            return
        # Re-check conditions: still empty + still nothing playing/queued.
        if player.channel is None:
            return
        if humans_in_channel(player.channel) > 0:
            return
        if player.playing or not player.queue.is_empty:
            return
        logger.info(
            "Auto-disconnect after %ds idle", _AUTO_DISCONNECT_SEC,
            extra={"bot_name": self.bot.bot_name},
        )
        await self._teardown_player(player)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload) -> None:
        player = payload.player
        if not isinstance(player, MusicPlayer):
            return
        # Save 1-track history (SPEC §7.4) and let the queue advance via wavelink itself.
        player.last_track = payload.track
        # Drop the requester entry for the just-ended track unless it is being requeued
        # (loop=track or loop=queue keeps it alive). SPEC §7.9.2: bound requester memory.
        if (
            payload.track.identifier
            and player.queue.mode == wavelink.QueueMode.normal
        ):
            player.requesters.pop(payload.track.identifier, None)
        if player.channel is None or player.guild is None:
            return
        if humans_in_channel(player.channel) == 0 and player.queue.is_empty:
            self._maybe_schedule_auto_disconnect(player)
