"""Test ProviderSessionStore."""

import asyncio


def test_get_set_session():
    from soulacp.session_store import ProviderSessionStore

    async def run():
        store = ProviderSessionStore()
        assert await store.get_session_id("user1", "claude") is None
        await store.set_session_id("user1", "claude", "sess_abc")
        assert await store.get_session_id("user1", "claude") == "sess_abc"

    asyncio.run(run())


def test_different_users():
    from soulacp.session_store import ProviderSessionStore

    async def run():
        store = ProviderSessionStore()
        await store.set_session_id("alice", "claude", "sess_a")
        await store.set_session_id("bob", "claude", "sess_b")
        assert await store.get_session_id("alice", "claude") == "sess_a"
        assert await store.get_session_id("bob", "claude") == "sess_b"

    asyncio.run(run())


def test_different_providers():
    from soulacp.session_store import ProviderSessionStore

    async def run():
        store = ProviderSessionStore()
        await store.set_session_id("user1", "claude", "sess_c")
        await store.set_session_id("user1", "gemini", "sess_g")
        assert await store.get_session_id("user1", "claude") == "sess_c"
        assert await store.get_session_id("user1", "gemini") == "sess_g"

    asyncio.run(run())


def test_clear_single_provider():
    from soulacp.session_store import ProviderSessionStore

    async def run():
        store = ProviderSessionStore()
        await store.set_session_id("user1", "claude", "sess_c")
        await store.set_session_id("user1", "gemini", "sess_g")
        await store.clear("user1", "claude")
        assert await store.get_session_id("user1", "claude") is None
        assert await store.get_session_id("user1", "gemini") == "sess_g"

    asyncio.run(run())


def test_clear_all_providers():
    from soulacp.session_store import ProviderSessionStore

    async def run():
        store = ProviderSessionStore()
        await store.set_session_id("user1", "claude", "sess_c")
        await store.set_session_id("user1", "gemini", "sess_g")
        await store.clear("user1")
        assert await store.get_session_id("user1", "claude") is None
        assert await store.get_session_id("user1", "gemini") is None

    asyncio.run(run())


def test_hash_id_numeric():
    from soulacp.session_store import ProviderSessionStore

    # Numeric user_id should be hashed
    hashed = ProviderSessionStore._hash_id("12345")
    assert hashed != "12345"
    assert len(hashed) == 10


def test_hash_id_non_numeric():
    from soulacp.session_store import ProviderSessionStore

    # Non-numeric user_id should pass through
    assert ProviderSessionStore._hash_id("alice") == "alice"
