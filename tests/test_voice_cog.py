"""Smoke test for VoiceCog: structural / metadata only.

Full event-handler / interaction execution requires a running discord.py client,
so we only verify that the slash commands are registered.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from music_bot.cogs.voice import VoiceCog


def test_voice_commands_registered() -> None:
    bot = MagicMock()
    cog = VoiceCog(bot)
    cmds = {c.name for c in cog.walk_app_commands()}
    assert {"join", "disconnect", "stayalone"} <= cmds
