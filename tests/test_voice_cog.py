"""Smoke test for VoiceCog: structural / metadata only.

Full event-handler / interaction execution requires a running discord.py client,
so we only verify that the slash commands are registered.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from music_bot.bot import MusicBotClient
from music_bot.cogs.voice import VoiceCog


def test_voice_commands_registered() -> None:
    bot = MagicMock()
    cog = VoiceCog(bot)
    cmds = {c.name for c in cog.walk_app_commands()}
    assert {"join", "disconnect", "stayalone"} <= cmds


def test_member_cache_includes_voice() -> None:
    # Regression: with MemberCacheFlags.none(), VoiceChannel.members is empty
    # and the auto-disconnect in voice.py fires on the first leaver instead of
    # waiting for the VC to actually empty.
    client = MusicBotClient(
        bot_name="t",
        db=MagicMock(),
        soft_limit_monitor=None,
        guild_locks=MagicMock(),
        all_clients=[],
        max_players=1,
        max_queue_size=10,
        dev_guild_id=None,
    )
    assert client._connection.member_cache_flags.voice is True
