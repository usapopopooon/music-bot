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


class MusicPlayer(wavelink.Player):
    """Per-(client × guild) playback state. SPEC §7.7.2 says Players are not shared."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.loop_mode: LoopMode = LoopMode.OFF
        self.last_track: wavelink.Playable | None = None
        self.text_channel: discord.abc.MessageableChannel | None = None
        self.panel: ControlPanel | None = None
        self.requesters: dict[str, int] = {}

    def remember_requester(self, track: wavelink.Playable, user_id: int) -> None:
        if track.identifier:
            self.requesters[track.identifier] = user_id

    def get_requester(self, track: wavelink.Playable) -> int | None:
        if track.identifier:
            return self.requesters.get(track.identifier)
        return None
