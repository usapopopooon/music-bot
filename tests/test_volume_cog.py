"""Smoke test for VolumeCog: structural / metadata only.

Full slash-command execution requires a running discord.py Bot, which is too heavy for
a unit test. We verify the command exists and is wired up.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from music_bot.cogs.volume import VolumeCog


def test_volume_command_registered() -> None:
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 1
    bot.db = MagicMock()
    bot.db.get_volume = AsyncMock(return_value=None)
    bot.db.set_volume = AsyncMock(return_value=None)

    cog = VolumeCog(bot)
    cmds = {c.name for c in cog.walk_app_commands()}
    assert "volume" in cmds


def test_format_module_imported() -> None:
    """Sanity check: format helpers are importable through the cog's transitive deps."""
    from music_bot.utils.format import format_duration

    assert format_duration(0) == "00:00"


def test_loop_mode_enum_values() -> None:
    from music_bot.player import LoopMode

    assert LoopMode("off") is LoopMode.OFF
    assert LoopMode("track") is LoopMode.TRACK
    assert LoopMode("queue") is LoopMode.QUEUE


def test_intents_minimal() -> None:
    from music_bot.bot import build_intents

    i = build_intents()
    assert i.guilds is True
    assert i.voice_states is True
    assert i.members is False
    assert i.presences is False
    assert i.message_content is False
