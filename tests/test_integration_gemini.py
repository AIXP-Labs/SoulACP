"""Integration tests for Gemini CLI adapter.

Run with: pytest tests/test_integration_gemini.py -v
Skip if Gemini CLI not available.
"""

import asyncio

import pytest

from soulacp.binary import find_gemini_binary

# Skip all tests if Gemini CLI not installed
pytestmark = pytest.mark.skipif(find_gemini_binary() is None, reason="Gemini CLI not installed")


@pytest.fixture
def config():
    from soulacp import ACPConfig

    return ACPConfig(
        provider="gemini",
        model="gemini-3-flash-preview",
        timeout_connect=30,
        timeout_prompt=60,
    )


@pytest.fixture
def client_class():
    from soulacp import resolve_client_class

    return resolve_client_class("gemini")


def test_connect_and_query(config, client_class):
    """Test basic Gemini connection and query."""
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
    """Test Gemini multi-turn conversation."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                r1 = await client.query("Remember the number 7.")
                assert len(r1) > 0
                r2 = await client.query("What number did I ask you to remember?")
                assert "7" in r2
        finally:
            await pool.close_all()

    asyncio.run(run())
