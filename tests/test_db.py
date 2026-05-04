"""DB integration tests against a real PostgreSQL.

CI uses a service container; locally the conftest expects DATABASE_URL.
SPEC §4.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from music_bot.db import Database

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="DATABASE_URL not set; skipping DB integration tests",
)


@pytest.mark.asyncio
async def test_migration_creates_table(pg_pool: asyncpg.Pool, database_url: str) -> None:
    db = await Database.connect(database_url, pool_size=2)
    try:
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT to_regclass('guild_bot_settings') AS r")
        assert row is not None
        assert row["r"] == "guild_bot_settings"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_get_volume_returns_none_when_unset(database_url: str) -> None:
    db = await Database.connect(database_url, pool_size=2)
    try:
        v = await db.get_volume(guild_id=11111, bot_id=22222)
        assert v is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_set_then_get_volume(database_url: str) -> None:
    db = await Database.connect(database_url, pool_size=2)
    try:
        await db.set_volume(guild_id=11111, bot_id=22222, volume=85)
        assert await db.get_volume(11111, 22222) == 85
        # Update on conflict
        await db.set_volume(guild_id=11111, bot_id=22222, volume=120)
        assert await db.get_volume(11111, 22222) == 120
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_volume_independent_per_bot(database_url: str) -> None:
    db = await Database.connect(database_url, pool_size=2)
    try:
        await db.set_volume(guild_id=11111, bot_id=1, volume=50)
        await db.set_volume(guild_id=11111, bot_id=2, volume=150)
        assert await db.get_volume(11111, 1) == 50
        assert await db.get_volume(11111, 2) == 150
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_volume_out_of_range_rejected(database_url: str) -> None:
    db = await Database.connect(database_url, pool_size=2)
    try:
        with pytest.raises(ValueError):
            await db.set_volume(guild_id=11111, bot_id=1, volume=-1)
        with pytest.raises(ValueError):
            await db.set_volume(guild_id=11111, bot_id=1, volume=201)
    finally:
        await db.close()
