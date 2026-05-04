"""Logging configuration with bot_name field. See SPEC §7.6.1."""

from __future__ import annotations

import logging
import sys


class _BotNameFilter(logging.Filter):
    """Inject a default bot_name into every record so the formatter never KeyErrors.

    Attached to the *handler* (not the logger) so that records propagated from
    child loggers also pass through it — Python's logging only consults a logger's
    filters when emitting via that logger directly, but handler filters run for
    every record reaching that handler.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "bot_name"):
            record.bot_name = "-"
        return True


_FORMAT = "[%(asctime)s] [%(levelname)s] [%(bot_name)s] [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger. Idempotent."""
    root = logging.getLogger()
    if getattr(root, "_music_bot_configured", False):
        return

    root.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    handler.addFilter(_BotNameFilter())
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("wavelink").setLevel(logging.INFO)

    root._music_bot_configured = True  # type: ignore[attr-defined]
