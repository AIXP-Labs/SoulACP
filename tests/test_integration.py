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


def test_multi_turn(config, client_class):
    """Test Claude multi-turn conversation — context retention within same session."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                r1 = await client.query("Remember the number 7.")
                assert len(r1) > 0
                r2 = await client.query("What number did I ask you to remember?")
                assert "7" in r2, f"Claude did not remember context; got: {r2}"
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_model_selection(client_class):
    """Test that model can be set per-session via session/set_model RPC."""
    from soulacp import ACPConfig, ACPConnectionPool

    config = ACPConfig(
        provider="claude",
        model="claude-acp/sonnet",
        timeout_connect=30,
        timeout_prompt=60,
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
    """Test ManagedSession high-level API works with Claude."""
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


def test_extra_env_effort_level(client_class):
    """Test that ACPConfig.extra_env reaches Claude subprocess.

    Sets CLAUDE_CODE_EFFORT_LEVEL=low via extra_env and verifies the
    session starts successfully. Does not assert effort-level behavior
    (LLM is non-deterministic), only that env propagation doesn't break
    subprocess launch.
    """
    from soulacp import ACPConfig, ACPConnectionPool

    config = ACPConfig(
        provider="claude",
        model="claude-acp/opus",
        timeout_connect=30,
        timeout_prompt=60,
        extra_env={"CLAUDE_CODE_EFFORT_LEVEL": "low"},
    )

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                assert session_id is not None
                response = await client.query("Say 'OK' in one word.")
                assert len(response) > 0
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_session_resume(config, client_class):
    """Test that session/resume RPC can restore a previous Claude session id.

    Structural test — verifies the resume RPC works at the protocol level.
    Whether claude-code-acp restores full context depends on its version.
    """
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            # 1. First connection: get a session_id
            async with pool.acquire() as (client1, sid1):
                assert sid1
                _ = await client1.query("Reply 'hi'.")

            # 2. Second connection: try to resume sid1
            from soulacp.adapters.claude_client import ClaudeACPClient

            client2 = ClaudeACPClient(config)
            await client2.connect()
            ok = await client2.resume(sid1)
            await client2.disconnect()

            # resume may return False if claude-code-acp doesn't keep
            # cross-process state. The test verifies the call doesn't crash.
            assert isinstance(ok, bool), f"resume() should return bool, got {ok!r}"
        finally:
            await pool.close_all()

    asyncio.run(run())
