"""Integration tests for Cursor ACP adapter.

Run with: pytest tests/test_integration_cursor_acp.py -v
Skip if cursor-agent not available.
"""

import asyncio

import pytest

from soulacp.binary import find_cursor_binary

pytestmark = pytest.mark.skipif(
    find_cursor_binary() is None,
    reason="Cursor CLI not installed",
)


@pytest.fixture
def config():
    from soulacp import ACPConfig

    return ACPConfig(
        provider="cursor",
        model="cursor-acp/default",
        timeout_connect=30,
        timeout_prompt=60,
    )


@pytest.fixture
def client_class():
    from soulacp import resolve_client_class

    return resolve_client_class("cursor")


def test_connect_and_query(config, client_class):
    """Test basic Cursor ACP connection and query."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                assert session_id is not None
                response = await client.query("Say hello in one word.")
                assert len(response) > 0
                assert isinstance(response, str)
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_multi_turn(config, client_class):
    """Test Cursor ACP multi-turn conversation."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                r1 = await client.query("Remember the number 99.")
                # Skip gracefully if Cursor backend hit quota / rate limit —
                # this is not a soulacp bug, just unavailable backend.
                if "resource_exhausted" in r1.lower() or "rate" in r1.lower():
                    pytest.skip(f"Cursor backend rate-limited / quota exhausted: {r1[:200]}")
                assert len(r1) > 0
                r2 = await client.query("What number did I ask you to remember?")
                if "resource_exhausted" in r2.lower() or "rate" in r2.lower():
                    pytest.skip(f"Cursor backend rate-limited / quota exhausted: {r2[:200]}")
                assert "99" in r2
        finally:
            await pool.close_all()

    asyncio.run(run())
