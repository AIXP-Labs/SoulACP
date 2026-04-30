"""Integration tests — require Claude Code CLI installed.

Run with: pytest tests/test_integration.py -v
Skip if Claude Code not available.
"""

import asyncio

import pytest

from soulacp.binary import find_claude_binary

# Skip all tests if Claude Code CLI not installed
pytestmark = pytest.mark.skipif(find_claude_binary() is None, reason="Claude Code CLI not installed")


@pytest.fixture
def config():
    from soulacp import ACPConfig

    return ACPConfig(
        provider="claude",
        model="claude-sonnet-4-20250514",
        timeout_connect=30,
        timeout_prompt=60,
    )


@pytest.fixture
def client_class():
    from soulacp import resolve_client_class

    return resolve_client_class("claude")


def test_connect_and_query(config, client_class):
    """Test basic connection and query."""
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


def test_streaming(config, client_class):
    """Test streaming response."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                chunks = []
                async for chunk in client.query_stream("Count from 1 to 3, one per line."):
                    chunks.append(chunk)
                full = "".join(chunks)
                assert len(chunks) > 0
                assert "1" in full
                assert "2" in full
                assert "3" in full
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_session_reuse(config, client_class):
    """Test that pool reuses sessions."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            # First query
            async with pool.acquire() as (client1, sid1):
                await client1.query("Remember the number 42.")

            # Second query — should reuse session
            async with pool.acquire(session_id=sid1) as (client2, sid2):
                assert sid2 == sid1
                response = await client2.query("What number did I ask you to remember?")
                assert "42" in response
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_pool_stats(config, client_class):
    """Test pool statistics."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            stats = pool.get_stats()
            assert stats["pool_size"] == 0
            assert stats["connected"] == 0

            async with pool.acquire() as (client, session_id):
                assert client.is_connected
        finally:
            await pool.close_all()

    asyncio.run(run())
