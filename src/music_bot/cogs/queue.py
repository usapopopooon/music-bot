"""Queue-management slash commands. See SPEC §5.6."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from ..player import LoopMode, MusicPlayer
from ..utils import embeds
from ..utils.checks import can_control_player
from ..utils.format import format_duration, truncate

if TYPE_CHECKING:
    from ..bot import MusicBotClient

QUEUE_PAGE_SIZE = 10


class QueueCog(commands.Cog):
    def __init__(self, bot: MusicBotClient) -> None:
        self.bot = bot

    @app_commands.command(name="queue", description="Show the current queue.")
    @app_commands.describe(page="Page number (1-indexed).")
    async def queue(
        self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 250] = 1
    ) -> None:
        # /queue is read-only — no VC permission required (SPEC §7.2 covers control).
        player = _get_player(interaction)
        if player is None:
            await interaction.response.send_message(
                embed=embeds.error("Not connected."), ephemeral=True
            )
            return
        embed = render_queue_embed(player, page)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="clear", description="Clear the entire queue.")
    async def clear(self, interaction: discord.Interaction) -> None:
        player = await _player_with_permission(interaction, mutating=True)
        if player is None:
            return
        player.queue.clear()
        if player.panel:
            await player.panel.refresh()
        await interaction.response.send_message(
            embed=embeds.success("Queue cleared."), ephemeral=True
        )

    @app_commands.command(name="remove", description="Remove a track from the queue by position.")
    @app_commands.describe(position="1-indexed position.")
    async def remove(
        self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 10000]
    ) -> None:
        player = await _player_with_permission(interaction, mutating=True)
        if player is None:
            return
        if position > len(player.queue):
            await interaction.response.send_message(
                embed=embeds.error("Position out of range."), ephemeral=True
            )
            return
        track = player.queue.peek(position - 1)
        del player.queue[position - 1]
        if player.panel:
            await player.panel.refresh()
        await interaction.response.send_message(
            embed=embeds.success(f"Removed: **{track.title}**"), ephemeral=True
        )

    @app_commands.command(name="move", description="Move a track from one position to another.")
    @app_commands.describe(from_pos="Source position.", to_pos="Destination position.")
    async def move(
        self,
        interaction: discord.Interaction,
        from_pos: app_commands.Range[int, 1, 10000],
        to_pos: app_commands.Range[int, 1, 10000],
    ) -> None:
        player = await _player_with_permission(interaction, mutating=True)
        if player is None:
            return
        if from_pos > len(player.queue) or to_pos > len(player.queue):
            await interaction.response.send_message(
                embed=embeds.error("Position out of range."), ephemeral=True
            )
            return
        track = player.queue.peek(from_pos - 1)
        del player.queue[from_pos - 1]
        player.queue.put_at(min(to_pos - 1, len(player.queue)), track)
        if player.panel:
            await player.panel.refresh()
        await interaction.response.send_message(
            embed=embeds.success(f"Moved **{track.title}** → position {to_pos}."), ephemeral=True
        )

    @app_commands.command(name="jump", description="Jump to a queue position (history is not affected).")
    @app_commands.describe(position="1-indexed position.")
    async def jump(
        self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 10000]
    ) -> None:
        player = await _player_with_permission(interaction, mutating=True)
        if player is None:
            return
        if position > len(player.queue):
            await interaction.response.send_message(
                embed=embeds.error("Position out of range."), ephemeral=True
            )
            return
        for _ in range(position - 1):
            player.queue.get()
        await player.skip(force=True)
        await interaction.response.send_message(
            embed=embeds.success(f"Jumped to position {position}."), ephemeral=True
        )

    @app_commands.command(name="shuffle", description="Toggle shuffle (one-time queue shuffle).")
    async def shuffle(self, interaction: discord.Interaction) -> None:
        player = await _player_with_permission(interaction, mutating=True)
        if player is None:
            return
        player.queue.shuffle()
        if player.panel:
            await player.panel.refresh()
        await interaction.response.send_message(
            embed=embeds.success("Queue shuffled."), ephemeral=True
        )

    @app_commands.command(name="loop", description="Set loop mode.")
    @app_commands.describe(mode="off / track / queue")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="off", value="off"),
            app_commands.Choice(name="track", value="track"),
            app_commands.Choice(name="queue", value="queue"),
        ]
    )
    async def loop(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        player = await _player_with_permission(interaction, mutating=True)
        if player is None:
            return
        loop_mode = LoopMode(mode.value)
        player.loop_mode = loop_mode
        if loop_mode == LoopMode.TRACK:
            player.queue.mode = wavelink.QueueMode.loop
        elif loop_mode == LoopMode.QUEUE:
            player.queue.mode = wavelink.QueueMode.loop_all
        else:
            player.queue.mode = wavelink.QueueMode.normal
        if player.panel:
            await player.panel.refresh()
        await interaction.response.send_message(
            embed=embeds.success(f"Loop: **{loop_mode.value}**"), ephemeral=True
        )


def _get_player(interaction: discord.Interaction) -> MusicPlayer | None:
    if interaction.guild is None:
        return None
    vc = interaction.guild.voice_client
    return vc if isinstance(vc, MusicPlayer) else None


async def _player_with_permission(
    interaction: discord.Interaction, *, mutating: bool
) -> MusicPlayer | None:
    """Resolve the active Player and enforce VC permission for mutating ops.

    Per SPEC §7.2: same-VC required (or no humans in bot's VC). Read-only `/queue`
    skips this check.
    """
    player = _get_player(interaction)
    if player is None:
        await interaction.response.send_message(
            embed=embeds.error("Not connected."), ephemeral=True
        )
        return None
    if mutating:
        allowed, reason = can_control_player(interaction.user, player.channel)
        if not allowed:
            await interaction.response.send_message(
                embed=embeds.error(reason or "Not allowed"), ephemeral=True
            )
            return None
    return player


def render_queue_embed(player: MusicPlayer, page: int) -> discord.Embed:
    """Render a queue page (10 items). See SPEC §5.4."""
    total = len(player.queue)
    if total == 0:
        return discord.Embed(
            title="📜 Queue", description="The queue is empty.", color=0x5865F2
        )
    pages = max(1, (total + QUEUE_PAGE_SIZE - 1) // QUEUE_PAGE_SIZE)
    page = max(1, min(page, pages))
    start = (page - 1) * QUEUE_PAGE_SIZE
    end = min(start + QUEUE_PAGE_SIZE, total)
    lines = []
    for i in range(start, end):
        t = player.queue.peek(i)
        line = f"`{i + 1}.` **{truncate(t.title, 60)}** — `{format_duration(t.length)}`"
        lines.append(line)
    embed = discord.Embed(
        title=f"📜 Queue (page {page}/{pages}, {total} tracks)",
        description="\n".join(lines),
        color=0x5865F2,
    )
    return embed
