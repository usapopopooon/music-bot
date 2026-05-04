"""Multi-Client supervisor: starts N Clients and restarts crashed ones. SPEC §7.7."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .bot import MusicBotClient
from .db import Database
from .lavalink import connect_lavalink
from .memory_guard import SoftLimitMonitor
from .routing import GuildLockRegistry

_HookCoro = Callable[[], Awaitable[None]]

logger = logging.getLogger(__name__)

_RESTART_BACKOFFS = [2, 4, 8, 16, 32]
_MAX_RESTART_ATTEMPTS = len(_RESTART_BACKOFFS)


class ClientSupervisor:
    """Owns one MusicBotClient and restarts it on transient failures.

    Per SPEC §7.7.3: 5-attempt exponential backoff, then disabled.
    """

    def __init__(
        self,
        *,
        token: str,
        bot_name: str,
        db: Database,
        soft_limit_monitor: SoftLimitMonitor | None,
        guild_locks: GuildLockRegistry,
        all_clients_ref: list[MusicBotClient],
        max_players: int,
        max_queue_size: int,
        dev_guild_id: int | None,
        lavalink_host: str,
        lavalink_port: int,
        lavalink_password: str,
        lavalink_secure: bool,
    ) -> None:
        self._token = token
        self._bot_name = bot_name
        self._db = db
        self._soft_limit_monitor = soft_limit_monitor
        self._guild_locks = guild_locks
        self._all_clients_ref = all_clients_ref
        self._max_players = max_players
        self._max_queue_size = max_queue_size
        self._dev_guild_id = dev_guild_id
        self._lavalink = (lavalink_host, lavalink_port, lavalink_password, lavalink_secure)
        self.disabled = False
        self.failures = 0

    async def run(self) -> None:
        """Run loop: restart on failure with backoff, give up after N failures."""
        attempt = 0
        while True:
            client = MusicBotClient(
                bot_name=self._bot_name,
                db=self._db,
                soft_limit_monitor=self._soft_limit_monitor,
                guild_locks=self._guild_locks,
                all_clients=self._all_clients_ref,
                max_players=self._max_players,
                max_queue_size=self._max_queue_size,
                dev_guild_id=self._dev_guild_id,
            )
            self._all_clients_ref.append(client)
            try:
                host, port, password, secure = self._lavalink

                # Bind via default args so the closure captures the *current* values
                # rather than referencing the loop-scope variables (ruff B023).
                async def _start(
                    _client: MusicBotClient = client,
                    _host: str = host,
                    _port: int = port,
                    _password: str = password,
                    _secure: bool = secure,
                ) -> None:
                    await connect_lavalink(
                        client=_client,
                        host=_host,
                        port=_port,
                        password=_password,
                        secure=_secure,
                    )

                client.setup_hook = _replace_hook(client.setup_hook, _start)  # type: ignore[method-assign,assignment]
                await client.start(self._token)
                logger.info(
                    "Client %s exited cleanly", self._bot_name, extra={"bot_name": self._bot_name}
                )
                return
            except Exception as exc:
                logger.exception(
                    "Client %s crashed (attempt %d): %s",
                    self._bot_name,
                    attempt + 1,
                    exc,
                    extra={"bot_name": self._bot_name},
                )
                self.failures = attempt + 1
            finally:
                if client in self._all_clients_ref:
                    self._all_clients_ref.remove(client)
                if not client.is_closed():
                    try:
                        await client.close()
                    except Exception:
                        pass

            if attempt >= _MAX_RESTART_ATTEMPTS - 1:
                logger.error(
                    "Client %s disabled after %d failed attempts",
                    self._bot_name,
                    _MAX_RESTART_ATTEMPTS,
                    extra={"bot_name": self._bot_name},
                )
                self.disabled = True
                return

            backoff = _RESTART_BACKOFFS[attempt]
            logger.warning(
                "Client %s restarting in %ds",
                self._bot_name,
                backoff,
                extra={"bot_name": self._bot_name},
            )
            await asyncio.sleep(backoff)
            attempt += 1


def _replace_hook(original: _HookCoro, prepended: _HookCoro) -> _HookCoro:
    """Wrap a Client.setup_hook so that `prepended` runs before it.

    discord.py runs setup_hook *before* connecting to the gateway, which is exactly
    where we need to attach to Lavalink (else the bot would appear online before
    audio is reachable).
    """

    async def _wrapped() -> None:
        await prepended()
        await original()

    return _wrapped


async def run_all(
    *,
    tokens: list[str],
    db: Database,
    soft_limit_monitor: SoftLimitMonitor | None,
    guild_locks: GuildLockRegistry,
    max_players: int,
    max_queue_size: int,
    dev_guild_id: int | None,
    lavalink_host: str,
    lavalink_port: int,
    lavalink_password: str,
    lavalink_secure: bool,
) -> int:
    """Run all Clients to completion. Returns process exit code.

    Exit 1 only if every Client ends up disabled (SPEC §7.7.3).
    """
    all_clients: list[MusicBotClient] = []
    supervisors = [
        ClientSupervisor(
            token=token,
            bot_name=f"bot{i + 1}",
            db=db,
            soft_limit_monitor=soft_limit_monitor,
            guild_locks=guild_locks,
            all_clients_ref=all_clients,
            max_players=max_players,
            max_queue_size=max_queue_size,
            dev_guild_id=dev_guild_id,
            lavalink_host=lavalink_host,
            lavalink_port=lavalink_port,
            lavalink_password=lavalink_password,
            lavalink_secure=lavalink_secure,
        )
        for i, token in enumerate(tokens)
    ]
    await asyncio.gather(*[s.run() for s in supervisors], return_exceptions=False)

    if all(s.disabled for s in supervisors):
        logger.error("All Clients disabled. Exiting with code 1.")
        return 1
    return 0
