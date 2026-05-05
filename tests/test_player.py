"""Pure-function tests for player module."""

from __future__ import annotations

from music_bot.player import (
    DEFAULT_DISPLAY_VOLUME,
    MAX_DISPLAY_VOLUME,
    display_to_lavalink,
)


def test_default_volume_stays_audible() -> None:
    """Default display=1 must round to Lavalink>=1; otherwise the bot starts silent."""
    assert display_to_lavalink(DEFAULT_DISPLAY_VOLUME) >= 1


_LAVALINK_CAP = (MAX_DISPLAY_VOLUME + 1) // 2


def test_display_to_lavalink_boundaries() -> None:
    assert display_to_lavalink(0) == 0
    assert display_to_lavalink(1) == 1
    assert display_to_lavalink(2) == 1
    assert display_to_lavalink(MAX_DISPLAY_VOLUME) == _LAVALINK_CAP


def test_display_to_lavalink_clamps_out_of_range() -> None:
    assert display_to_lavalink(-50) == 0
    assert display_to_lavalink(9999) == _LAVALINK_CAP


def test_display_to_lavalink_never_amplifies() -> None:
    """Lavalink 100 = original loudness; the cap must keep us strictly below that."""
    for d in range(0, MAX_DISPLAY_VOLUME + 1):
        assert display_to_lavalink(d) <= _LAVALINK_CAP
