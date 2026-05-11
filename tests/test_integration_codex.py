"""Integration tests for Codex ACP adapter.

Run with: pytest tests/test_integration_codex.py -v
Skip if codex-acp not available.

Auth: relies on whatever ``codex login`` has stored locally:
  - ``codex login`` with ChatGPT account → uses ``gpt-5.5`` (default below)
  - ``OPENAI_API_KEY`` env var → uses API-key auth (override model accordingly)
"""

import asyncio

import pytest

from soulacp.binary import find_codex_binary

# Skip all tests if codex-acp CLI not installed.
# Auth is handled implicitly by codex-acp via ~/.codex/auth.json or env var.
pytestmark = pytest.mark.skipif(
    find_codex_binary() is None,
    reason="codex-acp not installed (npm install -g @zed-industries/codex-acp)",
)


@pytest.fixture
def config():
    from soulacp import ACPConfig

    return ACPConfig(
        provider="codex",
        model="codex-acp/gpt-5.5",  # ChatGPT-auth friendly default
        timeout_connect=30,
        timeout_prompt=120,
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
    """Test Codex multi-turn conversation — context retention within same session."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                r1 = await client.query("Remember the number 7.")
                assert len(r1) > 0
                r2 = await client.query("What number did I ask you to remember?")
                assert "7" in r2, f"Codex did not remember context; got: {r2}"
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_model_selection(client_class):
    """Test that model can be set per-session (without editing ~/.codex/config.toml)."""
    from soulacp import ACPConfig, ACPConnectionPool

    config = ACPConfig(
        provider="codex",
        model="codex-acp/gpt-5.5",
        timeout_connect=30,
        timeout_prompt=60,
    )

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                # If model selection works, this should respond.
                # If model is invalid for current auth, error is raised.
                response = await client.query(
                    "What model are you? Reply in ONE short sentence."
                )
                assert len(response) > 0
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_streaming(config, client_class):
    """Test that query_stream yields chunks progressively (not just final)."""
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        chunks: list[str] = []
        try:
            async with pool.acquire() as (client, _sid):
                async for chunk in client.query_stream(
                    "Count from 1 to 5, one number per line."
                ):
                    if isinstance(chunk, str):
                        chunks.append(chunk)
                assert len(chunks) > 0, "expected at least 1 stream chunk"
                joined = "".join(chunks)
                assert len(joined) > 0
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_managed_session(config):
    """Test ManagedSession high-level API works with Codex."""
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
    """Test that pool reuses sessions when acquire(session_id=...) is called.

    Mirrors test_integration.py::test_session_reuse for Claude — same pool
    semantics, verifies context is retained across separate acquire() calls
    (not just within one acquire).
    """
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            # First query — establish context
            async with pool.acquire() as (client1, sid1):
                await client1.query("Remember the number 42.")

            # Second query — explicitly reuse the same session_id
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
    """Test pool statistics — mirrors test_integration.py::test_pool_stats."""
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


def test_extra_args_reasoning_effort(client_class):
    """Test that ACPConfig.extra_args propagates to codex-acp.

    Sets ``-c model_reasoning_effort="low"`` and verifies the session
    starts successfully. Doesn't assert reasoning depth (LLM behavior
    is non-deterministic), only that the extra args don't break the
    subprocess launch.
    """
    from soulacp import ACPConfig, ACPConnectionPool

    config = ACPConfig(
        provider="codex",
        model="codex-acp/gpt-5.5",
        timeout_connect=30,
        timeout_prompt=60,
        extra_args=["-c", 'model_reasoning_effort="low"'],
    )

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            async with pool.acquire() as (client, session_id):
                assert session_id is not None
                response = await client.query("What is 2+2? Reply with just the number.")
                assert "4" in response, f"unexpected response: {response}"
        finally:
            await pool.close_all()

    asyncio.run(run())


def test_session_resume(config, client_class):
    """Test that session/load can restore a previous Codex session id.

    NOTE: This is a structural test — it verifies the resume RPC works at
    the protocol level. Whether codex-acp actually restores full context
    (previous turns) depends on codex-acp version.
    """
    from soulacp import ACPConnectionPool

    async def run():
        pool = ACPConnectionPool(config, client_class)
        try:
            # 1. First connection: get a session_id
            async with pool.acquire() as (client1, sid1):
                assert sid1
                # Use it briefly so codex-acp records it
                _ = await client1.query("Reply 'hi'.")

            # 2. Second connection: try to resume sid1
            #    This calls resume() on the client which sends session/load.
            #    If it succeeds without raising, the RPC is supported.
            from soulacp.adapters.codex_client import CodexACPClient

            client2 = CodexACPClient(config)
            await client2.connect()
            ok = await client2.resume(sid1)
            await client2.disconnect()

            # resume may return False if codex-acp version doesn't keep
            # cross-process state — that's acceptable. The test verifies
            # the resume() method does not crash.
            assert isinstance(ok, bool), f"resume() should return bool, got {ok!r}"
        finally:
            await pool.close_all()

    asyncio.run(run())
