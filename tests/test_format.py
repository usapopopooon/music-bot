"""Tests for utils.format."""

from __future__ import annotations

import pytest

from music_bot.utils.format import format_duration, make_progress_bar, truncate


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, "00:00"),
        (1_500, "00:01"),
        (60_000, "01:00"),
        (3_600_000, "01:00:00"),
        (3_661_000, "01:01:01"),
        (None, "--:--"),
    ],
)
def test_format_duration(ms: int | None, expected: str) -> None:
    assert format_duration(ms) == expected


def test_progress_bar_at_zero() -> None:
    bar = make_progress_bar(0, 100, width=20)
    assert bar.startswith("🔘")
    assert len(bar) == 20  # 1 knob + 19 segments (segments are single ASCII chars)


def test_progress_bar_at_end() -> None:
    bar = make_progress_bar(100, 100, width=10)
    assert bar.endswith("🔘")


def test_progress_bar_zero_length_returns_safe_default() -> None:
    bar = make_progress_bar(0, 0, width=5)
    assert "🔘" in bar


def test_truncate() -> None:
    assert truncate("abc", 10) == "abc"
    assert truncate("abcdefghij", 5) == "abcd…"
