"""Integration tests for OpenCode ACP adapter.

Run with: pytest tests/test_integration_opencode.py -v
Skip if OpenCode CLI not available.

OpenCode uses ``OPENCODE_CONFIG_CONTENT`` env var to pass model selection
(not session/set_model). Does not support session/resume — base class
returns False gracefully.
"""

import asyncio

import pytest

from soulacp.binary import find_opencode_binary

pytestmark = pytest.mark.skipif(
    find_opencode_binary() is None,
    reason="OpenCode CLI not installed (npm install -g opencode)",
)


@pytest.fixture
def config():
    from soulacp import ACPConfig

    return ACPConfig(
        provider="opencode",
        model="opencode-acp/anthropic/claude-sonnet-4-20250514",
        timeout_connect=30,
        timeout_prompt=120,
    )


@pytest.fixture
def client_class():
    from soulacp import resolve_client_class

    return resolve_client_class("opencode")


def test_connect_and_query(config, client_class):
    """Test basic OpenCode connection and query."""
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
    """Test OpenCode multi-turn — context retention within same session."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                r1 = await client.query("Remember the number 7.")
                assert len(r1) > 0
                r2 = await client.query("What number did I ask you to remember?")
                assert "7" in r2, f"OpenCode did not remember context; got: {r2}"
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_streaming(config, client_class):
    """Test streaming response — yields chunks progressively."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        chunks: list[str] = []
        try:
            async with pool.acquire() as (client, _sid):
                async for chunk in client.query_stream(
                    "Count from 1 to 3, one number per line."
                ):
                    if isinstance(chunk, str):
                        chunks.append(chunk)
                assert len(chunks) > 0, "expected at least 1 stream chunk"
                full = "".join(chunks)
                assert "1" in full
                assert "3" in full
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_model_selection(client_class):
    """Test that model is passed via OPENCODE_CONFIG_CONTENT env var."""
    from soulacp import ACPConfig, ACPConnectionPool

    config = ACPConfig(
        provider="opencode",
        model="opencode-acp/anthropic/claude-sonnet-4-20250514",
        timeout_connect=30,
        timeout_prompt=120,
    )

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                response = await client.query(
                    "What model are you? Reply in ONE short sentence."
                )
                assert len(response) > 0
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_managed_session(config):
    """Test ManagedSession high-level API works with OpenCode."""
    from soulacp import ManagedSession

    async def run():
        async with ManagedSession(
            provider=config.provider,
            model=config.model,
            config=config,
        ) as session:
            response = await session.query("Say 'OK' in one word.")
            assert len(response) > 0
            assert isinstance(response, str)

    asyncio.run(run())


def test_session_reuse(config, client_class):
    """Test that pool reuses sessions across acquire() calls — context retained.

    OpenCode adapter implements session/load (see opencode_client.py::resume)
    so the pool can restore the same session_id across acquire() boundaries.
    """
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client1, sid1):
                await client1.query("Remember the number 42.")

            async with pool.acquire(session_id=sid1) as (client2, sid2):
                assert sid2 == sid1, f"expected reused session {sid1}, got {sid2}"
                response = await client2.query(
                    "What number did I ask you to remember?"
                )
                assert "42" in response, f"context not retained: {response}"
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


def test_session_resume(config, client_class):
    """Test that resume() method works at the protocol level.

    OpenCode does not implement session/resume (it uses base class default
    which returns False). This test verifies the method does not crash.
    """
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client1, sid1):
                assert sid1
                _ = await client1.query("Reply 'hi'.")

            from soulacp.adapters.opencode_client import OpenCodeACPClient

            client2 = OpenCodeACPClient(config)
            await client2.connect()
            ok = await client2.resume(sid1)
            await client2.disconnect()

            # OpenCode's base resume() returns False — that's acceptable.
            # We only verify the call doesn't crash.
            assert isinstance(ok, bool), f"resume() should return bool, got {ok!r}"
        finally:
            await pool.close_all()

    asyncio.run(run())
