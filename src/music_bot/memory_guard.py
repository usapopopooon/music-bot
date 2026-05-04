"""Memory hard/soft limit enforcement. See SPEC §7.8."""

from __future__ import annotations

import asyncio
import logging
import platform
from collections.abc import Callable

import psutil

logger = logging.getLogger(__name__)

_RSS_POLL_INTERVAL_SEC = 30
_WARN_THROTTLE_SEC = 60


def apply_hard_limit(limit_mb: int | None) -> None:
    """Set RLIMIT_AS so the process raises MemoryError before the cgroup OOM kills it.

    See SPEC §7.8.1: macOS / Windows fall through with a warning.
    """
    if limit_mb is None:
        return

    system = platform.system()
    if system == "Darwin":
        logger.warning("MEMORY_LIMIT_MB=%d ignored: RLIMIT_AS is non-functional on macOS", limit_mb)
        return
    if system == "Windows":
        logger.warning(
            "MEMORY_LIMIT_MB=%d ignored: resource module unavailable on Windows", limit_mb
        )
        return

    try:
        import resource
    except ImportError:
        logger.warning("MEMORY_LIMIT_MB=%d ignored: resource module unavailable", limit_mb)
        return

    n_bytes = limit_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (n_bytes, n_bytes))
        logger.info("RLIMIT_AS set to %d MB", limit_mb)
    except (ValueError, OSError) as exc:
        logger.warning("Failed to set RLIMIT_AS=%d MB: %s", limit_mb, exc)


class SoftLimitMonitor:
    """Background RSS monitor that flips a flag when the process exceeds soft_limit_mb.

    The bot consults `is_pressured()` before allocating a new Player.
    See SPEC §7.8.2.
    """

    def __init__(
        self,
        soft_limit_mb: int,
        on_pressure_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._soft_limit_bytes = soft_limit_mb * 1024 * 1024
        self._pressured = False
        self._task: asyncio.Task[None] | None = None
        self._on_change = on_pressure_change
        self._proc = psutil.Process()
        self._last_warn_at = 0.0

    def is_pressured(self) -> bool:
        return self._pressured

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="memory-soft-limit-monitor")
            logger.info(
                "SoftLimitMonitor started (limit=%d MB)", self._soft_limit_bytes // (1024 * 1024)
            )

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                rss = self._proc.memory_info().rss
                pressured = rss > self._soft_limit_bytes
                if pressured != self._pressured:
                    self._pressured = pressured
                    logger.warning(
                        "Memory pressure %s (rss=%d MB, limit=%d MB)",
                        "ENTERED" if pressured else "CLEARED",
                        rss // (1024 * 1024),
                        self._soft_limit_bytes // (1024 * 1024),
                    )
                    if self._on_change is not None:
                        self._on_change(pressured)
                elif pressured:
                    now = loop.time()
                    if now - self._last_warn_at > _WARN_THROTTLE_SEC:
                        logger.warning("Memory pressure ongoing (rss=%d MB)", rss // (1024 * 1024))
                        self._last_warn_at = now
            except Exception:
                logger.exception("SoftLimitMonitor loop iteration failed")
            await asyncio.sleep(_RSS_POLL_INTERVAL_SEC)


def compute_soft_limit_mb(hard_limit_mb: int | None, percent: int) -> int | None:
    """Soft limit is `percent` of the hard limit, or None when no hard limit is set."""
    if hard_limit_mb is None:
        return None
    return max(1, hard_limit_mb * percent // 100)
