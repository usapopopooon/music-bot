"""Entry point: `python -m music_bot`. SPEC §9 / §7.7."""

from __future__ import annotations

import asyncio
import logging
import platform
import signal
import sys

from .config import get_settings
from .db import Database
from .logging_setup import configure_logging
from .memory_guard import SoftLimitMonitor, apply_hard_limit, compute_soft_limit_mb
from .routing import GuildLockRegistry
from .supervisor import run_all

logger = logging.getLogger(__name__)


def _install_uvloop_if_available() -> None:
    """SPEC §7.9.5: prefer uvloop on Linux/macOS, fall back to asyncio elsewhere."""
    if platform.system() == "Windows":
        return
    try:
        import uvloop
    except ImportError:
        logger.info("uvloop unavailable, using stdlib asyncio")
        return
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    logger.info("uvloop installed")


async def _main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    apply_hard_limit(settings.memory_limit_mb)

    soft_limit_mb = compute_soft_limit_mb(
        settings.memory_limit_mb, settings.memory_soft_limit_percent
    )
    soft_limit_monitor: SoftLimitMonitor | None = None
    if soft_limit_mb is not None:
        soft_limit_monitor = SoftLimitMonitor(soft_limit_mb)
        soft_limit_monitor.start()

    db = await Database.connect(settings.database_url, settings.db_pool_size)
    guild_locks = GuildLockRegistry()

    # Convert SIGTERM into a graceful cancellation so the `finally` below runs.
    # (Railway sends SIGTERM on stop; default action is immediate process exit.)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    if platform.system() != "Windows":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                # add_signal_handler not available on every loop (e.g. some Windows envs).
                signal.signal(sig, lambda *_: stop_event.set())

    try:
        run_task = asyncio.create_task(
            run_all(
                tokens=settings.tokens,
                db=db,
                soft_limit_monitor=soft_limit_monitor,
                guild_locks=guild_locks,
                max_players=settings.max_players_per_bot,
                max_queue_size=settings.max_queue_size,
                dev_guild_id=settings.dev_guild_id,
                lavalink_host=settings.lavalink_host,
                lavalink_port=settings.lavalink_port,
                lavalink_password=settings.lavalink_password,
                lavalink_secure=settings.lavalink_secure,
            ),
            name="run-all",
        )
        stop_task = asyncio.create_task(stop_event.wait(), name="stop-signal")
        done, _ = await asyncio.wait(
            {run_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_task in done and not run_task.done():
            logger.info("Stop signal received, cancelling run-all")
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass
            return 0
        stop_task.cancel()
        return run_task.result()
    finally:
        if soft_limit_monitor is not None:
            await soft_limit_monitor.stop()
        await db.close()


def main() -> int:
    _install_uvloop_if_available()
    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down")
        return 0


if __name__ == "__main__":
    sys.exit(main())
