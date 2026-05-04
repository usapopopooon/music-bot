"""Voice-channel permission checks. See SPEC §7.2."""

from __future__ import annotations

import discord

# A "connectable" voice-style channel: regular voice or stage.
VoiceLikeChannel = discord.VoiceChannel | discord.StageChannel


def get_user_voice_channel(
    member: discord.Member | discord.User | None,
) -> VoiceLikeChannel | None:
    """Return the connected voice/stage channel for the member, or None."""
    if not isinstance(member, discord.Member):
        return None
    voice = member.voice
    if voice is None or voice.channel is None:
        return None
    if isinstance(voice.channel, discord.VoiceChannel | discord.StageChannel):
        return voice.channel
    return None


def humans_in_channel(channel: VoiceLikeChannel | None) -> int:
    """Number of non-bot members currently in the voice channel."""
    if channel is None:
        return 0
    return sum(1 for m in channel.members if not m.bot)


def can_control_player(
    member: discord.Member | discord.User | None,
    bot_voice_channel: VoiceLikeChannel | None,
) -> tuple[bool, str | None]:
    """Per SPEC §7.2: same VC required; if no humans are in the bot's VC, anyone may control.

    Returns (allowed, reason_if_denied).
    """
    if bot_voice_channel is None:
        return True, None
    if humans_in_channel(bot_voice_channel) == 0:
        return True, None
    user_vc = get_user_voice_channel(member)
    if user_vc is None:
        return False, "🛑 Join the same voice channel first."
    if user_vc.id != bot_voice_channel.id:
        return False, "🛑 Join the same voice channel first."
    return True, None
