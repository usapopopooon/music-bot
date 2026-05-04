"""Common Embed helpers. See SPEC §6.1 for color palette."""

from __future__ import annotations

import discord

COLOR_PLAYING = 0x5865F2
COLOR_PAUSED = 0xFAA61A
COLOR_TERMINATED = 0x4F545C
COLOR_ERROR = 0xED4245
COLOR_SUCCESS = 0x57F287


def success(message: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {message}", color=COLOR_SUCCESS)


def error(message: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {message}", color=COLOR_ERROR)


def info(message: str, *, color: int = COLOR_PLAYING) -> discord.Embed:
    return discord.Embed(description=message, color=color)
