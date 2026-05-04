"""asyncpg pool, migration, and volume read/write. See SPEC §4 and §7.9.3."""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS guild_bot_settings (
  guild_id   BIGINT NOT NULL,
  bot_id     BIGINT NOT NULL,
  volume     SMALLINT NOT NULL DEFAULT 100 CHECK (volume BETWEEN 0 AND 200),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (guild_id, bot_id)
);
"""

_UPSERT_SQL = """
INSERT INTO guild_bot_settings (guild_id, bot_id, volume)
VALUES ($1, $2, $3)
ON CONFLICT (guild_id, bot_id)
DO UPDATE SET volume = EXCLUDED.volume, updated_at = now();
"""

_SELECT_SQL = """
SELECT volume FROM guild_bot_settings WHERE guild_id = $1 AND bot_id = $2;
"""


class Database:
    """Thin asyncpg pool wrapper used by all Clients (shared)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str, pool_size: int) -> Database:
        pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=pool_size,
            statement_cache_size=0,
            max_inactive_connection_lifetime=300.0,
        )
        if pool is None:
            raise RuntimeError("asyncpg.create_pool returned None")
        db = cls(pool)
        await db.migrate()
        logger.info("Database connected: pool_size=%d", pool_size)
        return db

    async def migrate(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)

    async def get_volume(self, guild_id: int, bot_id: int) -> int | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_SELECT_SQL, guild_id, bot_id)
        return None if row is None else int(row["volume"])

    async def set_volume(self, guild_id: int, bot_id: int, volume: int) -> None:
        if not 0 <= volume <= 200:
            raise ValueError(f"volume out of range: {volume}")
        async with self._pool.acquire() as conn:
            await conn.execute(_UPSERT_SQL, guild_id, bot_id, volume)

    async def close(self) -> None:
        await self._pool.close()
