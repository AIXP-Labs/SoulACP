"""Integration tests for Codebuddy Code ACP adapter.

Run with: pytest tests/test_integration_codebuddy.py -v
Skip if codebuddy-code not available.
"""

import asyncio

import pytest

from soulacp.binary import find_codebuddy_binary

pytestmark = pytest.mark.skipif(
    find_codebuddy_binary() is None,
    reason="Codebuddy Code CLI not installed",
)


@pytest.fixture
def config():
    from soulacp import ACPConfig

    return ACPConfig(
        provider="codebuddy",
        model="codebuddy-acp/default",
        timeout_connect=30,
        timeout_prompt=60,
    )


@pytest.fixture
def client_class():
    from soulacp import resolve_client_class

    return resolve_client_class("codebuddy")


def test_connect_and_query(config, client_class):
    """Test basic Codebuddy connection and query."""
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
    """Test Codebuddy multi-turn conversation."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                r1 = await client.query("Remember the number 88.")
                assert len(r1) > 0
                r2 = await client.query("What number did I ask you to remember?")
                assert "88" in r2
        finally:
            await pool.close_all()

    asyncio.run(run())
