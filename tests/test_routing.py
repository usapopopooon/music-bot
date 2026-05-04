"""Tests for the multi-bot routing algorithm. SPEC §7.7.4."""

from __future__ import annotations

import asyncio

import pytest

from music_bot.routing import ClientCandidate, GuildLockRegistry, select_client


def test_no_clients() -> None:
    assert select_client([], user_voice_channel_id=42) is None


def test_all_idle_picks_lowest_id() -> None:
    cands = [
        ClientCandidate(bot_id=300, connected_channel_id=None),
        ClientCandidate(bot_id=100, connected_channel_id=None),
        ClientCandidate(bot_id=200, connected_channel_id=None),
    ]
    assert select_client(cands, user_voice_channel_id=42) == 100


def test_already_in_user_vc_wins() -> None:
    cands = [
        ClientCandidate(bot_id=100, connected_channel_id=None),  # idle, lowest id
        ClientCandidate(bot_id=200, connected_channel_id=42),  # already in user's VC
        ClientCandidate(bot_id=300, connected_channel_id=None),
    ]
    assert select_client(cands, user_voice_channel_id=42) == 200


def test_busy_in_other_vc_filtered_out() -> None:
    cands = [
        ClientCandidate(bot_id=100, connected_channel_id=999),  # busy elsewhere
        ClientCandidate(bot_id=200, connected_channel_id=None),
    ]
    assert select_client(cands, user_voice_channel_id=42) == 200


def test_all_busy_returns_none() -> None:
    cands = [
        ClientCandidate(bot_id=100, connected_channel_id=999),
        ClientCandidate(bot_id=200, connected_channel_id=888),
    ]
    assert select_client(cands, user_voice_channel_id=42) is None


def test_multiple_already_in_user_vc_picks_lowest_id() -> None:
    cands = [
        ClientCandidate(bot_id=300, connected_channel_id=42),
        ClientCandidate(bot_id=100, connected_channel_id=42),
    ]
    assert select_client(cands, user_voice_channel_id=42) == 100


@pytest.mark.asyncio
async def test_guild_lock_registry_returns_same_lock() -> None:
    reg = GuildLockRegistry()
    a = reg.get(1)
    b = reg.get(1)
    assert a is b
    c = reg.get(2)
    assert c is not a


@pytest.mark.asyncio
async def test_guild_lock_serializes_concurrent_callers() -> None:
    reg = GuildLockRegistry()
    order: list[str] = []

    async def task(name: str, sleep: float) -> None:
        async with reg.get(1):
            order.append(f"{name}-start")
            await asyncio.sleep(sleep)
            order.append(f"{name}-end")

    await asyncio.gather(task("A", 0.05), task("B", 0.0))
    # B must wait for A to finish before starting.
    assert order == ["A-start", "A-end", "B-start", "B-end"]
