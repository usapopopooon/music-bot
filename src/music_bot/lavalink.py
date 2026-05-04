"""Wavelink Pool connection (shared across all Clients). See SPEC §7.5."""

from __future__ import annotations

import asyncio
import logging

import aiohttp
import discord
import wavelink

logger = logging.getLogger(__name__)

_INITIAL_RETRY_INTERVAL_SEC = 30
_INITIAL_MAX_ATTEMPTS = 5
_PROBE_TIMEOUT_SEC = 5


async def _probe_lavalink(uri: str, password: str) -> None:
    """HTTP-probe Lavalink's `/version` endpoint. Raises on failure.

    `wavelink.Pool.connect` queues the WebSocket connection and does not raise on a
    dead Lavalink, so we verify reachability here ourselves to honour SPEC §7.5
    (retry 5× then exit so Bot does not appear "online" with broken audio).
    """
    timeout = aiohttp.ClientTimeout(total=_PROBE_TIMEOUT_SEC)
    headers = {"Authorization": password}
    async with (
        aiohttp.ClientSession(timeout=timeout, headers=headers) as session,
        session.get(f"{uri}/version") as resp,
    ):
        if resp.status >= 400:
            body = await resp.text()
            raise RuntimeError(f"Lavalink probe failed: HTTP {resp.status}: {body[:200]}")


async def connect_lavalink(
    *,
    client: discord.Client,
    host: str,
    port: int,
    password: str,
    secure: bool,
) -> None:
    """Connect to a single Lavalink node, retrying on initial failure.

    Per SPEC §7.5: 30 sec interval × 5 retries; if all fail, raise so the supervisor
    aborts startup (Bot does not appear "online" on Discord — silent failures are bad).
    """
    uri = f"{'https' if secure else 'http'}://{host}:{port}"
    last_error: BaseException | None = None
    for attempt in range(1, _INITIAL_MAX_ATTEMPTS + 1):
        try:
            await _probe_lavalink(uri, password)
            node = wavelink.Node(
                identifier=f"main-{client.user.id if client.user else id(client)}",
                uri=uri,
                password=password,
                inactive_player_timeout=None,
            )
            await wavelink.Pool.connect(client=client, nodes=[node])
            logger.info("Lavalink connected: %s (attempt %d)", uri, attempt)
            return
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Lavalink connect attempt %d/%d failed: %s",
                attempt,
                _INITIAL_MAX_ATTEMPTS,
                exc,
            )
            if attempt < _INITIAL_MAX_ATTEMPTS:
                await asyncio.sleep(_INITIAL_RETRY_INTERVAL_SEC)
    raise RuntimeError(
        f"Lavalink connection failed after {_INITIAL_MAX_ATTEMPTS} attempts: {last_error}"
    )
