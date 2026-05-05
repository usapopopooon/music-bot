"""Voice connection management (`/join`, `/disconnect`, `/stayalone`) and auto-disconnect.

See SPEC §7.1.
"""

from __future__ import annotations

import gc
import logging
from typing import TYPE_CHECKING

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from ..player import DEFAULT_DISPLAY_VOLUME, MusicPlayer
from ..utils import embeds
from ..utils.checks import can_control_player, get_user_voice_channel, humans_in_channel

if TYPE_CHECKING:
    from ..bot import MusicBotClient

logger = logging.getLogger(__name__)


class VoiceCog(commands.Cog):
    def __init__(self, bot: MusicBotClient) -> None:
        self.bot = bot

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

        # See `_ensure_player` in playback.py for the rationale on self_deaf.
        player = await vc.connect(cls=MusicPlayer, self_deaf=True)
        player.autoplay = wavelink.AutoPlayMode.partial
        self.bot.increment_players()
        if self.bot.user is not None:
            stored = await self.bot.db.get_volume(interaction.guild.id, self.bot.user.id)
            await player.set_display_volume(
                stored if stored is not None else DEFAULT_DISPLAY_VOLUME
            )
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

    @app_commands.command(
        name="stayalone",
        description="Toggle whether the bot stays connected when the VC has no humans.",
    )
    async def stayalone(self, interaction: discord.Interaction) -> None:
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
        player.stay_alone = not player.stay_alone
        state = "ON" if player.stay_alone else "OFF"
        await interaction.response.send_message(
            embed=embeds.success(f"stay-alone: **{state}**"), ephemeral=True
        )
        # If the user just turned it OFF in an already-empty VC, honor the new policy
        # immediately rather than waiting for the next voice-state event.
        if (
            not player.stay_alone
            and player.channel is not None
            and humans_in_channel(player.channel) == 0
        ):
            await self._teardown_player(player)

    async def _teardown_player(self, player: MusicPlayer) -> None:
        """Hard cleanup. SPEC §7.9.4: collect, drop refs, then collect again."""
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

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Disconnect immediately when the last human leaves the bot's VC.

        Skipped when ``player.stay_alone`` is set via ``/stayalone``.
        """
        if member.bot:
            return
        guild = member.guild
        player = guild.voice_client
        if not isinstance(player, MusicPlayer) or player.channel is None:
            return
        left_bots_vc = (before.channel and before.channel.id == player.channel.id) and (
            after.channel is None or after.channel.id != player.channel.id
        )
        if not left_bots_vc:
            return
        if humans_in_channel(player.channel) > 0:
            return
        if player.stay_alone:
            return
        logger.info("Auto-disconnect: VC empty", extra={"bot_name": self.bot.bot_name})
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
        if payload.track.identifier and player.queue.mode == wavelink.QueueMode.normal:
            player.requesters.pop(payload.track.identifier, None)
