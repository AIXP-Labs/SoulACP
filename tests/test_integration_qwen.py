"""Integration tests for Qwen Code ACP adapter.

Run with: pytest tests/test_integration_qwen.py -v
Skip if qwen-code not available.
"""

import asyncio

import pytest

from soulacp.binary import find_qwen_binary

# Skip all tests if Qwen Code CLI not installed
pytestmark = pytest.mark.skipif(
    find_qwen_binary() is None,
    reason="Qwen Code CLI not installed",
)


@pytest.fixture
def config():
    from soulacp import ACPConfig

    return ACPConfig(
        provider="qwen",
        model="qwen-acp/default",
        timeout_connect=30,
        timeout_prompt=60,
    )


@pytest.fixture
def client_class():
    from soulacp import resolve_client_class

    return resolve_client_class("qwen")


def test_connect_and_query(config, client_class):
    """Test basic Qwen Code connection and query."""
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
    """Test Qwen Code multi-turn conversation."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                r1 = await client.query("Remember the number 42.")
                assert len(r1) > 0
                r2 = await client.query("What number did I ask you to remember?")
                assert "42" in r2
        finally:
            await pool.close_all()

    asyncio.run(run())
