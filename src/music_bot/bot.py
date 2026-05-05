"""Single discord.py Client (commands.Bot) factory + cog loader. SPEC §7.9.1."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from .db import Database
    from .memory_guard import SoftLimitMonitor
    from .routing import GuildLockRegistry

logger = logging.getLogger(__name__)


def build_intents() -> discord.Intents:
    """Minimal intents per SPEC §7.9.1: only guilds + voice_states."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.voice_states = True
    return intents


def build_member_cache_flags() -> discord.MemberCacheFlags:
    """Cache only voice-channel members. SPEC §7.9.1.

    `MemberCacheFlags(voice=True)` keeps `joined=True` from the default and
    requires the privileged `members` intent — so build from `none()` instead.
    """
    flags = discord.MemberCacheFlags.none()
    flags.voice = True
    return flags


class MusicBotClient(commands.Bot):
    """A single Discord Client. N of these run in one process (SPEC §7.7).

    Shared resources (db, soft-limit monitor, guild lock registry, guild_lock_registry)
    are injected; per-Client state lives on the instance.
    """

    def __init__(
        self,
        *,
        bot_name: str,
        db: Database,
        soft_limit_monitor: SoftLimitMonitor | None,
        guild_locks: GuildLockRegistry,
        all_clients: list[MusicBotClient],
        max_players: int,
        max_queue_size: int,
        dev_guild_id: int | None,
    ) -> None:
        super().__init__(
            command_prefix="!",  # unused; we only register slash commands
            intents=build_intents(),
            chunk_guilds_at_startup=False,
            member_cache_flags=build_member_cache_flags(),
            max_messages=None,
            help_command=None,
        )
        self.bot_name = bot_name
        self.db = db
        self.soft_limit_monitor = soft_limit_monitor
        self.guild_locks = guild_locks
        self.all_clients = all_clients
        self.max_players = max_players
        self.max_queue_size = max_queue_size
        self.dev_guild_id = dev_guild_id
        self._player_count = 0
        self._sync_lock = asyncio.Lock()

    @property
    def player_count(self) -> int:
        return self._player_count

    def increment_players(self) -> None:
        self._player_count += 1

    def decrement_players(self) -> None:
        self._player_count = max(0, self._player_count - 1)

    def at_player_capacity(self) -> bool:
        return self._player_count >= self.max_players

    async def setup_hook(self) -> None:
        """discord.py lifecycle hook: load cogs, sync commands."""
        from .cogs import playback, voice, volume  # noqa: PLC0415
        from .cogs import queue as queue_cog

        await self.add_cog(playback.PlaybackCog(self))
        await self.add_cog(queue_cog.QueueCog(self))
        await self.add_cog(voice.VoiceCog(self))
        await self.add_cog(volume.VolumeCog(self))

        async with self._sync_lock:
            if self.dev_guild_id is not None:
                guild = discord.Object(id=self.dev_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info(
                    "Synced %d commands to dev guild %d",
                    len(synced),
                    self.dev_guild_id,
                    extra={"bot_name": self.bot_name},
                )
            else:
                synced = await self.tree.sync()
                logger.info(
                    "Synced %d commands globally", len(synced), extra={"bot_name": self.bot_name}
                )

    async def on_ready(self) -> None:
        logger.info(
            "Logged in as %s (id=%s)",
            self.user,
            getattr(self.user, "id", "?"),
            extra={"bot_name": self.bot_name},
        )
