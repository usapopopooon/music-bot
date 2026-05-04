"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgres://postgres:test@localhost:5432/music_bot_test"
    )


@pytest_asyncio.fixture(autouse=True)
async def pg_pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    """Drop and recreate the table before every DB test for isolation."""
    if "DATABASE_URL" not in os.environ:
        # Skip DB setup when no DATABASE_URL is configured (lets non-DB tests run).
        yield None  # type: ignore[misc]
        return
    pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=2)
    assert pool is not None
    try:
        async with pool.acquire() as conn:
            await conn.execute("DROP TABLE IF EXISTS guild_bot_settings")
        yield pool
    finally:
        await pool.close()
