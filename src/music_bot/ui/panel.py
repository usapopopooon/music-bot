"""Music Control Panel: persistent in-channel UI. SPEC §5.2 / §6."""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import TYPE_CHECKING

import discord
import wavelink

from ..player import LoopMode, MusicPlayer
from ..utils import embeds as embed_helpers
from ..utils.checks import can_control_player
from ..utils.format import format_duration, make_progress_bar, truncate

if TYPE_CHECKING:
    from ..bot import MusicBotClient

logger = logging.getLogger(__name__)

_PROGRESS_TICK_SEC = 5
_FOOTER_FLASH_SEC = 10


class ControlPanel:
    """Owns the single in-channel message and runs a 5-sec refresh loop while playing.

    Only one Panel exists per Player. The Player holds a strong ref to the Panel,
    and the Panel holds a weakref to the Player to avoid the cycle described in §7.9.4.
    """

    def __init__(
        self,
        bot: MusicBotClient,
        player: MusicPlayer,
        channel: discord.abc.MessageableChannel,
    ) -> None:
        self.bot = bot
        self._player_ref = weakref.ref(player)
        self.channel = channel
        self.message: discord.Message | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._footer_flash_until: float = 0.0
        self._footer_flash_text: str | None = None
        self._terminated = False

    @property
    def player(self) -> MusicPlayer | None:
        return self._player_ref()

    async def send(self) -> None:
        view = ControlPanelView(self)
        embed = self._build_embed()
        self.message = await self.channel.send(embed=embed, view=view)
        self._start_refresh_loop()

    async def refresh(self) -> None:
        if self._terminated or self.message is None:
            return
        try:
            await self.message.edit(embed=self._build_embed(), view=ControlPanelView(self))
        except discord.NotFound:
            self._terminated = True
        except discord.HTTPException as exc:
            logger.warning("Panel edit failed: %s", exc, extra={"bot_name": self.bot.bot_name})

    async def terminate(self, *, silent: bool = False) -> None:
        if self._terminated:
            return
        self._terminated = True
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        if self.message is None or silent:
            return
        try:
            embed = self._build_embed(force_terminated=True)
            await self.message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

    def flash_footer(self, text: str) -> None:
        loop = asyncio.get_running_loop()
        self._footer_flash_until = loop.time() + _FOOTER_FLASH_SEC
        self._footer_flash_text = text

    def _start_refresh_loop(self) -> None:
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._refresh_loop(), name="panel-refresh")

    async def _refresh_loop(self) -> None:
        while not self._terminated:
            try:
                await asyncio.sleep(_PROGRESS_TICK_SEC)
                player = self.player
                if player is None or not player.connected:
                    await self.terminate()
                    return
                if player.playing or self._footer_flash_text is not None:
                    await self.refresh()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Panel refresh loop error", extra={"bot_name": self.bot.bot_name})

    def _build_embed(self, *, force_terminated: bool = False) -> discord.Embed:
        player = self.player
        if force_terminated or player is None or not player.connected:
            embed = discord.Embed(title="🎵 Disconnected", color=embed_helpers.COLOR_TERMINATED)
            return embed

        track = player.current
        if track is None:
            embed = discord.Embed(
                title="🎵 Idle",
                description="Queue is empty. Add a track with ➕ or `/play`.",
                color=embed_helpers.COLOR_PLAYING,
            )
            return embed

        color = embed_helpers.COLOR_PAUSED if player.paused else embed_helpers.COLOR_PLAYING
        embed = discord.Embed(
            title="🎵 Now playing",
            color=color,
            url=track.uri,
        )
        embed.description = (
            f"**[{truncate(track.title, 80)}]({track.uri})**\n"
            f"by {truncate(track.author or '?', 60)}"
        )
        if track.artwork:
            embed.set_thumbnail(url=track.artwork)

        if track.is_stream:
            time_field = "🔴 LIVE"
        else:
            bar = make_progress_bar(player.position, track.length)
            time_field = (
                f"{bar}  `{format_duration(player.position)} / {format_duration(track.length)}`"
            )
        embed.add_field(name="​", value=time_field, inline=False)

        upcoming = _peek_next(player)
        if upcoming is None:
            embed.add_field(name="Up next", value="Queue is empty.", inline=False)
        else:
            remaining = max(0, len(player.queue) - 1)
            extra = f" (+ {remaining} more)" if remaining > 0 else ""
            embed.add_field(
                name="Up next",
                value=f"**{truncate(upcoming.title, 80)}**{extra}",
                inline=False,
            )

        requester_id = player.get_requester(track)
        requester = f"<@{requester_id}>" if requester_id else "?"
        footer_parts = [f"Requested by {requester}"]

        if self._footer_flash_text is not None:
            loop = asyncio.get_running_loop()
            if loop.time() < self._footer_flash_until:
                footer_parts.append(self._footer_flash_text)
            else:
                self._footer_flash_text = None
        if self.bot.soft_limit_monitor and self.bot.soft_limit_monitor.is_pressured():
            footer_parts.append("⚠️ Memory pressure")

        embed.set_footer(text=" · ".join(footer_parts))
        return embed


def _peek_next(player: MusicPlayer) -> wavelink.Playable | None:
    if player.queue.is_empty:
        return None
    try:
        return player.queue.peek(0)
    except Exception:
        return None


# ---------- View ----------


class ControlPanelView(discord.ui.View):
    """Buttons rendered alongside the Panel embed.

    Note: the View is rebuilt on every refresh so labels reflect current state.
    Custom IDs use the `mb:<action>` prefix per SPEC §5.2.2.
    """

    def __init__(self, panel: ControlPanel) -> None:
        super().__init__(timeout=None)
        self.panel = panel
        self._build()

    def _build(self) -> None:
        player = self.panel.player
        if player is None or not player.connected:
            return

        # Row 0: transport
        self.add_item(_BackButton(self.panel))
        self.add_item(_PauseResumeButton(self.panel))
        self.add_item(_SkipButton(self.panel))
        self.add_item(_StopButton(self.panel))
        self.add_item(_LoopButton(self.panel))

        # Row 1: seek/queue
        is_live = bool(player.current and player.current.is_stream)
        self.add_item(_SeekButton(self.panel, delta_ms=-10_000, label="−10s", disabled=is_live))
        self.add_item(_SeekButton(self.panel, delta_ms=10_000, label="+10s", disabled=is_live))
        self.add_item(_ShuffleButton(self.panel))
        self.add_item(_QueueButton(self.panel))
        self.add_item(_LeaveButton(self.panel))

        # Row 2: volume
        self.add_item(_VolumeStepButton(self.panel, delta=-10, label="🔉 −10"))
        self.add_item(_VolumeShowButton(self.panel))
        self.add_item(_VolumeStepButton(self.panel, delta=10, label="🔊 +10"))

        # Row 3: add/search
        self.add_item(_AddButton(self.panel))
        self.add_item(_SearchButton(self.panel))


async def _check_and_get_player(
    interaction: discord.Interaction, panel: ControlPanel
) -> MusicPlayer | None:
    """Common preamble for every panel button: VC permission + valid player."""
    player = panel.player
    if player is None or not player.connected:
        await interaction.response.send_message(
            embed=embed_helpers.error("Player gone."), ephemeral=True
        )
        return None
    allowed, reason = can_control_player(interaction.user, player.channel)
    if not allowed:
        await interaction.response.send_message(
            embed=embed_helpers.error(reason or "Not allowed"), ephemeral=True
        )
        return None
    return player


class _BackButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        player = panel.player
        disabled = player is None or player.last_track is None
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="⏮",
            label="Back",
            custom_id="mb:back",
            row=0,
            disabled=disabled,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        if player.last_track is None:
            await interaction.response.send_message(
                embed=embed_helpers.error("No previous track."), ephemeral=True
            )
            return
        prev = player.last_track
        if player.current is not None:
            player.queue.put_at(0, player.current)
        player.queue.put_at(0, prev)
        await player.skip(force=True)
        await interaction.response.defer()


class _PauseResumeButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        player = panel.player
        is_paused = player is not None and player.paused
        super().__init__(
            style=discord.ButtonStyle.primary,
            emoji="▶" if is_paused else "⏸",
            label="Resume" if is_paused else "Pause",
            custom_id="mb:pause_resume",
            row=0,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        await player.pause(not player.paused)
        await self.panel.refresh()
        await interaction.response.defer()


class _SkipButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="⏭",
            label="Skip",
            custom_id="mb:skip",
            row=0,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        await player.skip(force=True)
        await interaction.response.defer()


class _StopButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        super().__init__(
            style=discord.ButtonStyle.danger,
            emoji="⏹",
            label="Stop",
            custom_id="mb:stop",
            row=0,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        player.queue.clear()
        await player.stop()
        await self.panel.terminate()
        await interaction.response.send_message(
            embed=embed_helpers.success("Stopped."), ephemeral=True
        )


class _LoopButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        player = panel.player
        mode = player.loop_mode.value if player else "off"
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="🔁",
            label=f"Loop: {mode}",
            custom_id="mb:loop_cycle",
            row=0,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        order = [LoopMode.OFF, LoopMode.TRACK, LoopMode.QUEUE]
        nxt = order[(order.index(player.loop_mode) + 1) % len(order)]
        player.loop_mode = nxt
        if nxt == LoopMode.TRACK:
            player.queue.mode = wavelink.QueueMode.loop
        elif nxt == LoopMode.QUEUE:
            player.queue.mode = wavelink.QueueMode.loop_all
        else:
            player.queue.mode = wavelink.QueueMode.normal
        await self.panel.refresh()
        await interaction.response.defer()


class _SeekButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel, delta_ms: int, label: str, disabled: bool) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="⏪" if delta_ms < 0 else "⏩",
            label=label,
            custom_id=f"mb:seek_{delta_ms}",
            row=1,
            disabled=disabled,
        )
        self.panel = panel
        self.delta_ms = delta_ms

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        if player.current is None:
            await interaction.response.send_message(
                embed=embed_helpers.error("Nothing playing."), ephemeral=True
            )
            return
        target = max(0, player.position + self.delta_ms)
        length = player.current.length or 0
        if length and target >= length:
            await player.skip(force=True)
        else:
            await player.seek(target)
        await self.panel.refresh()
        await interaction.response.defer()


class _ShuffleButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="🔀",
            label="Shuffle",
            custom_id="mb:shuffle",
            row=1,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        player.queue.shuffle()
        await self.panel.refresh()
        await interaction.response.defer()


class _QueueButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="📜",
            label="Queue",
            custom_id="mb:queue_view",
            row=1,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        from .queue_view import QueueView  # noqa: PLC0415

        view = QueueView(self.panel, player, page=1)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)


class _LeaveButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji="🔌",
            label="Leave",
            custom_id="mb:leave",
            row=1,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        from ..cogs.voice import VoiceCog  # noqa: PLC0415

        cog = self.panel.bot.get_cog("VoiceCog")
        if isinstance(cog, VoiceCog):
            await cog._teardown_player(player)  # noqa: SLF001
        await interaction.response.send_message(
            embed=embed_helpers.success("Disconnected."), ephemeral=True
        )


class _VolumeStepButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel, delta: int, label: str) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label,
            custom_id=f"mb:vol_{'up' if delta > 0 else 'down'}",
            row=2,
        )
        self.panel = panel
        self.delta = delta

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        new = max(0, min(200, player.volume + self.delta))
        await player.set_volume(new)
        if interaction.guild is not None and self.panel.bot.user is not None:
            await self.panel.bot.db.set_volume(interaction.guild.id, self.panel.bot.user.id, new)
        await self.panel.refresh()
        await interaction.response.defer()


class _VolumeShowButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        player = panel.player
        vol = player.volume if player else 100
        super().__init__(
            style=discord.ButtonStyle.primary,
            emoji="🎚️",
            label=f"{vol}%",
            custom_id="mb:vol_show",
            row=2,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        from .modals import VolumeModal  # noqa: PLC0415

        await interaction.response.send_modal(VolumeModal(player.volume))


class _AddButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        super().__init__(
            style=discord.ButtonStyle.success,
            emoji="➕",
            label="Add…",
            custom_id="mb:add",
            row=3,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        from ..cogs.playback import PlaybackCog  # noqa: PLC0415
        from .modals import AddTrackModal  # noqa: PLC0415

        cog = self.panel.bot.get_cog("PlaybackCog")
        if not isinstance(cog, PlaybackCog):
            await interaction.response.send_message(
                embed=embed_helpers.error("Playback unavailable."), ephemeral=True
            )
            return
        await interaction.response.send_modal(AddTrackModal(cog))


class _SearchButton(discord.ui.Button[ControlPanelView]):
    def __init__(self, panel: ControlPanel) -> None:
        super().__init__(
            style=discord.ButtonStyle.success,
            emoji="🔍",
            label="Search…",
            custom_id="mb:search",
            row=3,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction) -> None:
        player = await _check_and_get_player(interaction, self.panel)
        if player is None:
            return
        from ..cogs.playback import PlaybackCog  # noqa: PLC0415
        from .modals import SearchModal  # noqa: PLC0415

        cog = self.panel.bot.get_cog("PlaybackCog")
        if not isinstance(cog, PlaybackCog):
            await interaction.response.send_message(
                embed=embed_helpers.error("Playback unavailable."), ephemeral=True
            )
            return
        await interaction.response.send_modal(SearchModal(cog))
