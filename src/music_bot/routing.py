"""Multi-bot dispatch and per-guild Lock. See SPEC §7.7.4."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class ClientCandidate:
    """Snapshot of a Client's state in a single guild used for routing.

    Attributes:
        bot_id: Discord application_id of the Client.
        connected_channel_id: VC id the Client is currently in for this guild,
            or None if not connected.
    """

    bot_id: int
    connected_channel_id: int | None


def select_client(
    candidates: Iterable[ClientCandidate],
    user_voice_channel_id: int,
) -> int | None:
    """Select a Client to handle a `/play` in the user's VC.

    Per SPEC §7.7.4 step 3-4:
      - "available" = not connected to any VC in this guild, OR connected to the user's VC.
      - prefer a Client already in the user's VC; else lowest application_id among available.

    Returns the chosen Client's bot_id, or None if no Client is available.
    """
    available: list[ClientCandidate] = []
    for cand in candidates:
        ch = cand.connected_channel_id
        if ch is None or ch == user_voice_channel_id:
            available.append(cand)
    if not available:
        return None

    in_user_vc = [c for c in available if c.connected_channel_id == user_voice_channel_id]
    if in_user_vc:
        return min(in_user_vc, key=lambda c: c.bot_id).bot_id
    return min(available, key=lambda c: c.bot_id).bot_id


class GuildLockRegistry:
    """Per-guild asyncio.Lock registry to serialize concurrent /play in the same guild.

    Per SPEC §7.7.4 step 2.
    """

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get(self, guild_id: int) -> asyncio.Lock:
        lock = self._locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[guild_id] = lock
        return lock
