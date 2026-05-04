"""Time formatting and progress-bar rendering."""

from __future__ import annotations

PROGRESS_BAR_WIDTH = 20


def format_duration(ms: int | float | None) -> str:
    """Format milliseconds as `HH:MM:SS` (or `MM:SS` if < 1 hour). Returns `--:--` for None."""
    if ms is None:
        return "--:--"
    total_seconds = max(0, int(ms) // 1000)
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def make_progress_bar(position_ms: int, length_ms: int, width: int = PROGRESS_BAR_WIDTH) -> str:
    """Render a 20-char progress bar with current position marked by 🔘. SPEC §5.2.1."""
    if length_ms <= 0 or width <= 0:
        return "🔘" + "▬" * (width - 1) if width > 0 else ""
    pos = max(0, min(position_ms, length_ms))
    knob = round(pos / length_ms * (width - 1))
    return "▬" * knob + "🔘" + "▬" * (width - 1 - knob)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
