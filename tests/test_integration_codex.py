"""Integration tests for Codex ACP adapter.

Run with: pytest tests/test_integration_codex.py -v
Skip if codex-acp not available.
"""

import asyncio
import os

import pytest

from soulacp.binary import find_codex_binary

# Skip all tests if codex-acp CLI not installed or no API key
pytestmark = pytest.mark.skipif(
    find_codex_binary() is None or not os.environ.get("OPENAI_API_KEY"),
    reason="codex-acp not installed or OPENAI_API_KEY not set",
)


@pytest.fixture
def config():
    from soulacp import ACPConfig

    return ACPConfig(
        provider="codex",
        model="codex-acp/gpt-5",
        timeout_connect=30,
        timeout_prompt=60,
    )


@pytest.fixture
def client_class():
    from soulacp import resolve_client_class

    return resolve_client_class("codex")


def test_connect_and_query(config, client_class):
    """Test basic Codex connection and query."""
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
    """Test Codex multi-turn conversation."""
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
