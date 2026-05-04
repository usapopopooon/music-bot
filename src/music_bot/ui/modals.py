"""Add / Search / Volume modals + the search-result select view. SPEC §5.3."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import wavelink

from ..player import MusicPlayer
from ..utils import embeds
from ..utils.format import format_duration, truncate

if TYPE_CHECKING:
    from ..cogs.playback import PlaybackCog


class AddTrackModal(discord.ui.Modal, title="Add to queue"):
    query: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="URL or query",
        placeholder="https://… or 'Mr. Children innocent world'",
        min_length=1,
        max_length=256,
        required=True,
    )

    def __init__(self, playback: PlaybackCog, head: bool = False) -> None:
        super().__init__(custom_id=f"mb:modal_add:{int(head)}")
        self.playback = playback
        self.head = head

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        if interaction.guild is None:
            return
        player = interaction.guild.voice_client
        if not isinstance(player, MusicPlayer):
            await interaction.followup.send(embed=embeds.error("Not connected."), ephemeral=True)
            return
        await self.playback._enqueue(  # noqa: SLF001
            interaction, player, self.query.value, head=self.head
        )


class VolumeModal(discord.ui.Modal, title="Set volume"):
    level: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Volume (0-200)",
        placeholder="100",
        min_length=1,
        max_length=3,
        required=True,
    )

    def __init__(self, current: int) -> None:
        super().__init__(custom_id="mb:modal_volume")
        self.level.default = str(current)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            level = int(self.level.value)
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error("Volume must be an integer."), ephemeral=True
            )
            return
        if not 0 <= level <= 200:
            await interaction.response.send_message(
                embed=embeds.error("Volume must be 0-200."), ephemeral=True
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=embeds.error("This must be used in a server."), ephemeral=True
            )
            return
        player = interaction.guild.voice_client
        if not isinstance(player, MusicPlayer):
            await interaction.response.send_message(
                embed=embeds.error("Not connected."), ephemeral=True
            )
            return
        await player.set_volume(level)
        from ..bot import MusicBotClient  # noqa: PLC0415

        client = interaction.client
        if isinstance(client, MusicBotClient) and client.user is not None:
            await client.db.set_volume(interaction.guild.id, client.user.id, level)
        if player.panel:
            await player.panel.refresh()
        await interaction.response.send_message(
            embed=embeds.success(f"Volume set to **{level}%**"), ephemeral=True
        )


class SearchModal(discord.ui.Modal, title="Search"):
    query: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="Search query",
        placeholder="Mr. Children innocent world",
        min_length=1,
        max_length=128,
        required=True,
    )

    def __init__(self, playback: PlaybackCog) -> None:
        super().__init__(custom_id="mb:modal_search")
        self.playback = playback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        results = await self.playback._resolve_or_search(f"ytsearch:{self.query.value}")  # noqa: SLF001
        if not results:
            await interaction.followup.send(embed=embeds.error("No results."), ephemeral=True)
            return
        if isinstance(results, wavelink.Playlist):
            tracks = list(results.tracks)
        else:
            tracks = list(results)
        view = SearchSelectView(self.playback, tracks[:5])
        await interaction.followup.send(
            embed=embeds.info("Pick a track:"), view=view, ephemeral=True
        )


class SearchSelectView(discord.ui.View):
    """Ephemeral view shown after /search or 🔍 modal — pick 1 from up to 5 results."""

    def __init__(self, playback: PlaybackCog, tracks: list[wavelink.Playable]) -> None:
        super().__init__(timeout=60)
        self.playback = playback
        self._tracks = tracks
        options = []
        for i, t in enumerate(tracks):
            options.append(
                discord.SelectOption(
                    label=truncate(t.title, 90),
                    description=f"{truncate(t.author or '?', 40)} · {format_duration(t.length)}",
                    value=str(i),
                )
            )

        select: discord.ui.Select[SearchSelectView] = discord.ui.Select(
            placeholder="Pick one…", options=options, min_values=1, max_values=1
        )

        async def _picked(interaction: discord.Interaction) -> None:
            await interaction.response.defer(thinking=True, ephemeral=True)
            idx = int(select.values[0])
            track = self._tracks[idx]
            if interaction.guild is None:
                return
            player = interaction.guild.voice_client
            if not isinstance(player, MusicPlayer):
                # Connect afresh through the playback Cog's _ensure_player.
                player = await self.playback._ensure_player(interaction)  # noqa: SLF001
                if player is None:
                    return
            player.remember_requester(track, interaction.user.id)
            player.queue.put(track)
            if not player.playing:
                await player.play(player.queue.get())
            if player.panel is None and interaction.channel is not None:
                from .panel import ControlPanel  # noqa: PLC0415

                player.panel = ControlPanel(self.playback.bot, player, interaction.channel)  # type: ignore[arg-type]
                await player.panel.send()
            elif player.panel is not None:
                await player.panel.refresh()
            await interaction.followup.send(
                embed=embeds.success(f"Added: **{track.title}**"), ephemeral=True
            )

        select.callback = _picked  # type: ignore[method-assign]
        self.add_item(select)
