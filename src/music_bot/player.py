"""Wavelink Player subclass that holds bot-specific playback state.

Per SPEC §7.4 / §7.7.2 / §7.9: history is per (bot_id, guild_id) and limited to 1 track
(last finished track), loop modes off/track/queue, shuffle, and a Control Panel pointer.
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any

import discord
import wavelink

if TYPE_CHECKING:
    from .ui.panel import ControlPanel


class LoopMode(enum.StrEnum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


# Displayed scale 0..MAX_DISPLAY_VOLUME maps to Lavalink 0..(MAX/2). A displayed 100
# corresponds to Lavalink 50 — i.e. half of the original audio amplitude — so the
# Lavalink volume filter never amplifies, eliminating clip-driven hearing damage.
MAX_DISPLAY_VOLUME = 100
DEFAULT_DISPLAY_VOLUME = 1


def display_to_lavalink(value: int) -> int:
    value = max(0, min(MAX_DISPLAY_VOLUME, value))
    # Round up so display=1 stays audible (Lavalink 1) instead of silent.
    return (value + 1) // 2


class MusicPlayer(wavelink.Player):
    """Per-(client × guild) playback state. SPEC §7.7.2 says Players are not shared."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.loop_mode: LoopMode = LoopMode.OFF
        self.last_track: wavelink.Playable | None = None
        self.text_channel: discord.abc.MessageableChannel | None = None
        self.panel: ControlPanel | None = None
        self.requesters: dict[str, int] = {}
        self._display_volume: int = DEFAULT_DISPLAY_VOLUME
        # Session-only override. When True, disable instant auto-disconnect on empty VC.
        self.stay_alone: bool = False

    @property
    def display_volume(self) -> int:
        return self._display_volume

    async def set_display_volume(self, value: int) -> None:
        value = max(0, min(MAX_DISPLAY_VOLUME, value))
        self._display_volume = value
        await self.set_volume(display_to_lavalink(value))

    def remember_requester(self, track: wavelink.Playable, user_id: int) -> None:
        if track.identifier:
            self.requesters[track.identifier] = user_id

    def get_requester(self, track: wavelink.Playable) -> int | None:
        if track.identifier:
            return self.requesters.get(track.identifier)
        return None
