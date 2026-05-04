"""Playback slash commands: /play /search /pause /resume /skip /back /stop /seek etc.

See SPEC §5.6.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

from ..player import MusicPlayer
from ..routing import ClientCandidate, select_client
from ..utils import embeds
from ..utils.checks import can_control_player, get_user_voice_channel

if TYPE_CHECKING:
    from ..bot import MusicBotClient

logger = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$|^(\d+)$")


def _parse_seek_time(s: str) -> int | None:
    """Parse `HH:MM:SS`, `MM:SS`, or `SSS` (seconds) -> milliseconds."""
    s = s.strip()
    m = _TIME_RE.match(s)
    if m is None:
        return None
    if m.group(4) is not None:
        return int(m.group(4)) * 1000
    h = int(m.group(1) or 0)
    mins = int(m.group(2))
    secs = int(m.group(3))
    if mins >= 60 or secs >= 60:
        return None
    return ((h * 3600) + (mins * 60) + secs) * 1000


class PlaybackCog(commands.Cog):
    def __init__(self, bot: MusicBotClient) -> None:
        self.bot = bot

    # ---------- Routing helpers ----------

    def _gather_routing_candidates(self, guild_id: int) -> list[ClientCandidate]:
        """Scan all sibling Clients for routing decisions. SPEC §7.7.4."""
        cands: list[ClientCandidate] = []
        for client in self.bot.all_clients:
            if client.user is None:
                continue
            guild = client.get_guild(guild_id)
            if guild is None:
                continue  # not invited there
            vc = guild.voice_client
            ch_id: int | None = None
            if isinstance(vc, MusicPlayer) and vc.channel is not None:
                ch_id = vc.channel.id
            cands.append(ClientCandidate(bot_id=client.user.id, connected_channel_id=ch_id))
        return cands

    async def _resolve_or_search(self, query: str) -> wavelink.Search:
        """Run Lavalink search; mostly a thin wrapper for testability."""
        return await wavelink.Playable.search(query)

    async def _ensure_player(self, interaction: discord.Interaction) -> MusicPlayer | None:
        """Connect to the user's VC (or return existing player). Returns None on error
        (and sends an ephemeral response itself)."""
        if interaction.guild is None:
            await interaction.followup.send(
                embed=embeds.error("This command must be used in a server."), ephemeral=True
            )
            return None
        guild = interaction.guild
        existing = guild.voice_client
        if isinstance(existing, MusicPlayer):
            allowed, reason = can_control_player(interaction.user, existing.channel)
            if not allowed:
                await interaction.followup.send(
                    embed=embeds.error(reason or "Not allowed"), ephemeral=True
                )
                return None
            return existing

        vc = get_user_voice_channel(interaction.user)
        if vc is None:
            await interaction.followup.send(
                embed=embeds.error("Join a voice channel first."), ephemeral=True
            )
            return None

        if self.bot.soft_limit_monitor and self.bot.soft_limit_monitor.is_pressured():
            await interaction.followup.send(
                embed=embeds.error("⚠️ Playback paused: memory pressure. Try again shortly."),
                ephemeral=True,
            )
            return None

        if self.bot.at_player_capacity():
            await interaction.followup.send(
                embed=embeds.error(
                    f"🛑 This bot is at its concurrent-playback limit "
                    f"({self.bot.max_players}). Try another bot."
                ),
                ephemeral=True,
            )
            return None

        player = await vc.connect(cls=MusicPlayer)
        # SPEC §7.3: queue advances automatically on track end (loop modes are handled by Queue.mode).
        player.autoplay = wavelink.AutoPlayMode.partial
        self.bot.increment_players()
        if self.bot.user is not None and interaction.guild is not None:
            stored = await self.bot.db.get_volume(interaction.guild.id, self.bot.user.id)
            if stored is not None:
                await player.set_volume(stored)
        if interaction.channel is not None:
            player.text_channel = interaction.channel  # type: ignore[assignment]
        return player

    # ---------- Commands ----------

    @app_commands.command(
        name="play", description="Play a track or playlist (URL or search query)."
    )
    @app_commands.describe(query="URL or search query.")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=embeds.error("This command must be used in a server."), ephemeral=True
            )
            return

        # Acknowledge first — the per-guild lock below may take >3s under contention.
        await interaction.response.defer(thinking=True, ephemeral=True)

        # SPEC §7.7.4: serialize concurrent /play in same guild + run routing.
        async with self.bot.guild_locks.get(interaction.guild.id):
            user_vc = get_user_voice_channel(interaction.user)
            if user_vc is None:
                await interaction.followup.send(
                    embed=embeds.error("Join a voice channel first."), ephemeral=True
                )
                return
            cands = self._gather_routing_candidates(interaction.guild.id)
            chosen = select_client(cands, user_vc.id)
            if chosen is None:
                await interaction.followup.send(
                    embed=embeds.error(
                        "All bots are busy in another voice channel in this server."
                    ),
                    ephemeral=True,
                )
                return
            if self.bot.user is None or chosen != self.bot.user.id:
                # Routing chose a sibling Client. Politely redirect.
                await interaction.followup.send(
                    embed=embeds.info(
                        f"Use `/play` on the bot with id `{chosen}` for this voice channel."
                    ),
                    ephemeral=True,
                )
                return

            player = await self._ensure_player(interaction)
            if player is None:
                return
            await self._enqueue(interaction, player, query, head=False)

    @app_commands.command(name="playnext", description="Add a track to the front of the queue.")
    @app_commands.describe(query="URL or search query.")
    async def playnext(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        player = await self._ensure_player(interaction)
        if player is None:
            return
        await self._enqueue(interaction, player, query, head=True)

    @app_commands.command(
        name="playnow", description="Play a track immediately, skipping the current one."
    )
    @app_commands.describe(query="URL or search query.")
    async def playnow(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        player = await self._ensure_player(interaction)
        if player is None:
            return
        await self._enqueue(interaction, player, query, head=True)
        if player.playing:
            await player.skip(force=True)

    @app_commands.command(name="search", description="Search YouTube and pick a result.")
    @app_commands.describe(query="Search query.")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)
        results = await self._resolve_or_search(f"ytsearch:{query}")
        if not results:
            await interaction.followup.send(embed=embeds.error("No results."), ephemeral=True)
            return
        # Build a select menu (top 5)
        from ..ui.modals import SearchSelectView  # noqa: PLC0415

        view = SearchSelectView(self, list(results)[:5])
        await interaction.followup.send(
            embed=embeds.info("Pick a track:"), view=view, ephemeral=True
        )

    async def _enqueue(
        self,
        interaction: discord.Interaction,
        player: MusicPlayer,
        query: str,
        *,
        head: bool,
    ) -> None:
        """Resolve a query, enqueue, and start playback if idle."""
        try:
            results = await self._resolve_or_search(query)
        except Exception as exc:
            logger.warning(
                "Search failed for %r: %s", query, exc, extra={"bot_name": self.bot.bot_name}
            )
            await interaction.followup.send(
                embed=embeds.error("Failed to load that track."), ephemeral=True
            )
            return
        if not results:
            await interaction.followup.send(embed=embeds.error("No results."), ephemeral=True)
            return

        added: list[wavelink.Playable] = []
        if isinstance(results, wavelink.Playlist):
            tracks = results.tracks
        else:
            tracks = [results[0]]

        # Enforce MAX_QUEUE_SIZE.
        capacity = self.bot.max_queue_size - len(player.queue)
        if capacity <= 0:
            await interaction.followup.send(embed=embeds.error("Queue is full."), ephemeral=True)
            return
        tracks = tracks[: max(0, capacity)]

        for t in tracks:
            player.remember_requester(t, interaction.user.id)
            if head:
                player.queue.put_at(0, t)
            else:
                player.queue.put(t)
            added.append(t)

        if not player.playing:
            await player.play(player.queue.get())

        # Send/refresh Control Panel + send ephemeral confirmation.
        await self._ensure_panel(interaction, player)
        if len(added) == 1:
            msg = f"Added: **{added[0].title}**"
        else:
            msg = f"Added **{len(added)}** tracks."
        await interaction.followup.send(embed=embeds.success(msg), ephemeral=True)

    async def _ensure_panel(self, interaction: discord.Interaction, player: MusicPlayer) -> None:
        from ..ui.panel import ControlPanel  # noqa: PLC0415

        if player.panel is None and interaction.channel is not None:
            player.panel = ControlPanel(self.bot, player, interaction.channel)  # type: ignore[arg-type]
            await player.panel.send()
        elif player.panel is not None:
            await player.panel.refresh()

    # ---------- Transport ----------

    @app_commands.command(name="pause", description="Pause playback.")
    async def pause(self, interaction: discord.Interaction) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        await player.pause(True)
        if player.panel:
            await player.panel.refresh()
        await interaction.response.send_message(embed=embeds.success("Paused."), ephemeral=True)

    @app_commands.command(name="resume", description="Resume playback.")
    async def resume(self, interaction: discord.Interaction) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        await player.pause(False)
        if player.panel:
            await player.panel.refresh()
        await interaction.response.send_message(embed=embeds.success("Resumed."), ephemeral=True)

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        await player.skip(force=True)
        await interaction.response.send_message(embed=embeds.success("Skipped."), ephemeral=True)

    @app_commands.command(
        name="skipto", description="Skip to a queue position (treats skipped as played)."
    )
    @app_commands.describe(position="1-indexed position.")
    async def skipto(
        self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 10000]
    ) -> None:
        player = await _player_or_error(interaction)
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
            embed=embeds.success(f"Skipped to position {position}."), ephemeral=True
        )

    @app_commands.command(name="back", description="Play the previous track (1-track history).")
    async def back(self, interaction: discord.Interaction) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        if player.last_track is None:
            await interaction.response.send_message(
                embed=embeds.error("No previous track."), ephemeral=True
            )
            return
        prev = player.last_track
        if player.current is not None:
            player.queue.put_at(0, player.current)
        player.queue.put_at(0, prev)
        await player.skip(force=True)
        await interaction.response.send_message(embed=embeds.success("Going back."), ephemeral=True)

    @app_commands.command(
        name="replay", description="Restart the current track from the beginning."
    )
    async def replay(self, interaction: discord.Interaction) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        if player.current is None:
            await interaction.response.send_message(
                embed=embeds.error("Nothing playing."), ephemeral=True
            )
            return
        await player.seek(0)
        await interaction.response.send_message(embed=embeds.success("Replaying."), ephemeral=True)

    @app_commands.command(name="seek", description="Seek to a time (HH:MM:SS or MM:SS or seconds).")
    @app_commands.describe(time="Target time, e.g. 01:23 or 90.")
    async def seek(self, interaction: discord.Interaction, time: str) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        ms = _parse_seek_time(time)
        if ms is None:
            await interaction.response.send_message(
                embed=embeds.error("Invalid time format."), ephemeral=True
            )
            return
        if player.current is None:
            await interaction.response.send_message(
                embed=embeds.error("Nothing playing."), ephemeral=True
            )
            return
        await player.seek(ms)
        await interaction.response.send_message(
            embed=embeds.success(f"Seeked to {time}."), ephemeral=True
        )

    @app_commands.command(name="forward", description="Skip forward by N seconds (default 10).")
    @app_commands.describe(seconds="Seconds to skip forward.")
    async def forward(
        self, interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 600] = 10
    ) -> None:
        await self._jog(interaction, seconds * 1000)

    @app_commands.command(name="rewind", description="Rewind by N seconds (default 10).")
    @app_commands.describe(seconds="Seconds to rewind.")
    async def rewind(
        self, interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 600] = 10
    ) -> None:
        await self._jog(interaction, -seconds * 1000)

    async def _jog(self, interaction: discord.Interaction, delta_ms: int) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        if player.current is None:
            await interaction.response.send_message(
                embed=embeds.error("Nothing playing."), ephemeral=True
            )
            return
        target = max(0, player.position + delta_ms)
        length = player.current.length or 0
        if length and target >= length:
            await player.skip(force=True)
        else:
            await player.seek(target)
        await interaction.response.send_message(
            embed=embeds.success(f"{'+' if delta_ms >= 0 else ''}{delta_ms // 1000}s"),
            ephemeral=True,
        )

    @app_commands.command(name="stop", description="Stop playback and clear the queue.")
    async def stop(self, interaction: discord.Interaction) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        player.queue.clear()
        await player.stop()
        if player.panel:
            await player.panel.terminate()
        await interaction.response.send_message(embed=embeds.success("Stopped."), ephemeral=True)

    @app_commands.command(name="grab", description="Send the current track to your DM.")
    async def grab(self, interaction: discord.Interaction) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        if player.current is None:
            await interaction.response.send_message(
                embed=embeds.error("Nothing playing."), ephemeral=True
            )
            return
        track = player.current
        try:
            await interaction.user.send(f"{track.title} — {track.uri or ''}")
            await interaction.response.send_message(
                embed=embeds.success("Sent to your DM."), ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error("I cannot DM you. Enable DMs from server members."),
                ephemeral=True,
            )

    @app_commands.command(name="nowplaying", description="Show the now-playing panel.")
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = await _player_or_error(interaction)
        if player is None:
            return
        if interaction.channel is not None:
            from ..ui.panel import ControlPanel  # noqa: PLC0415

            if player.panel is not None:
                await player.panel.terminate(silent=True)
            player.panel = ControlPanel(self.bot, player, interaction.channel)  # type: ignore[arg-type]
            await player.panel.send()
        await interaction.response.send_message(
            embed=embeds.success("Panel reposted."), ephemeral=True
        )


async def _player_or_error(interaction: discord.Interaction) -> MusicPlayer | None:
    """Common preamble: get the active player and check VC permissions."""
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=embeds.error("This command must be used in a server."), ephemeral=True
        )
        return None
    player = interaction.guild.voice_client
    if not isinstance(player, MusicPlayer):
        await interaction.response.send_message(
            embed=embeds.error("Not connected."), ephemeral=True
        )
        return None
    allowed, reason = can_control_player(interaction.user, player.channel)
    if not allowed:
        await interaction.response.send_message(
            embed=embeds.error(reason or "Not allowed"), ephemeral=True
        )
        return None
    return player
