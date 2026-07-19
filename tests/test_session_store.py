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
    assert len(hashed) == 32


def test_hash_id_non_numeric():
    from soulacp.session_store import ProviderSessionStore

    # Human-readable identifiers must not leak into persisted cache keys.
    hashed = ProviderSessionStore._hash_id("alice")
    assert hashed != "alice"
    assert len(hashed) == 32


def test_legacy_plaintext_key_is_migrated_on_read():
    from soulacp.cache import MemoryCache
    from soulacp.session_store import ProviderSessionStore

    async def run():
        cache = MemoryCache()
        store = ProviderSessionStore(cache=cache)
        legacy_key = store._legacy_key("claude", "alice")
        new_key = store._key("claude", "alice")
        await cache.set(legacy_key, "legacy-session")

        assert await store.get_session_id("alice", "claude") == "legacy-session"
        assert await cache.get(legacy_key) is None
        assert await cache.get(new_key) == "legacy-session"

    asyncio.run(run())


def test_legacy_migration_cannot_overwrite_a_concurrent_new_session():
    from soulacp.cache import MemoryCache
    from soulacp.session_store import ProviderSessionStore

    class BlockingMigrationCache(MemoryCache):
        def __init__(self):
            super().__init__()
            self.migration_started = asyncio.Event()
            self.release_migration = asyncio.Event()

        async def set(self, key, value, ttl=0):
            if key.startswith("provider_session:v2:") and value == "legacy-session":
                self.migration_started.set()
                await self.release_migration.wait()
            await super().set(key, value, ttl)

    async def run():
        cache = BlockingMigrationCache()
        store = ProviderSessionStore(cache=cache)
        legacy_key = store._legacy_key("claude", "alice")
        await cache.set(legacy_key, "legacy-session")

        migration = asyncio.create_task(store.get_session_id("alice", "claude"))
        await cache.migration_started.wait()
        update = asyncio.create_task(store.set_session_id("alice", "claude", "new-session"))
        await asyncio.sleep(0)

        assert not update.done()
        cache.release_migration.set()
        assert await migration == "legacy-session"
        await update
        assert await store.get_session_id("alice", "claude") == "new-session"

    asyncio.run(run())


def test_ambiguous_numeric_legacy_key_is_never_migrated_or_deleted():
    from soulacp.cache import MemoryCache
    from soulacp.session_store import ProviderSessionStore

    async def run():
        cache = MemoryCache()
        store = ProviderSessionStore(cache=cache)
        numeric_user = "12345"
        colliding_text_user = store._legacy_hash_id(numeric_user)
        legacy_key = store._legacy_key("claude", numeric_user)
        assert legacy_key == store._legacy_key("claude", colliding_text_user)
        await cache.set(legacy_key, "numeric-user-session")

        assert await store.get_session_id(numeric_user, "claude") is None
        assert await store.get_session_id(colliding_text_user, "claude") is None

        await store.set_session_id(colliding_text_user, "claude", "new-session")
        assert await cache.get(legacy_key) == "numeric-user-session"
        await store.clear(colliding_text_user, "claude")
        assert await cache.get(legacy_key) == "numeric-user-session"

    asyncio.run(run())


def test_v2_namespace_does_not_claim_legacy_32_hex_identity_key():
    from soulacp.cache import MemoryCache
    from soulacp.session_store import ProviderSessionStore

    async def run():
        cache = MemoryCache()
        store = ProviderSessionStore(cache=cache)
        current_user = "current-user"
        legacy_raw_user = store._hash_id(current_user)
        ambiguous_key = store._legacy_key("claude", legacy_raw_user)
        await cache.set(ambiguous_key, "legacy-raw-session")

        assert ambiguous_key != store._key("claude", current_user)
        assert await store.get_session_id(current_user, "claude") is None
        assert await store.get_session_id(legacy_raw_user, "claude") is None

        await store.set_session_id(current_user, "claude", "current-session")
        await store.clear(current_user, "claude")
        assert await cache.get(ambiguous_key) == "legacy-raw-session"

    asyncio.run(run())


def test_legacy_delimiter_collision_is_not_migrated():
    from soulacp.cache import MemoryCache
    from soulacp.session_store import ProviderSessionStore

    async def run():
        cache = MemoryCache()
        store = ProviderSessionStore(cache=cache)
        legacy_key = store._legacy_key("provider", "team:user")
        assert legacy_key == store._legacy_key("provider:team", "user")
        await cache.set(legacy_key, "ambiguous-session")

        assert await store.get_session_id("team:user", "provider") is None
        assert await store.get_session_id("user", "provider:team") is None
        assert await cache.get(legacy_key) == "ambiguous-session"

    asyncio.run(run())


def test_store_preserves_falsy_custom_cache_backend():
    from soulacp.cache import MemoryCache
    from soulacp.session_store import ProviderSessionStore

    class FalsyMemoryCache(MemoryCache):
        def __bool__(self) -> bool:
            return False

    cache = FalsyMemoryCache()
    store = ProviderSessionStore(cache=cache)

    assert store._cache is cache


def test_store_rejects_ttl_outside_runtime_float_range():
    import pytest

    from soulacp.session_store import ProviderSessionStore

    with pytest.raises(ValueError, match="ttl"):
        ProviderSessionStore(ttl=10**400)


def test_store_rejects_invalid_unicode_identifiers():
    import pytest

    from soulacp.session_store import ProviderSessionStore

    async def run():
        store = ProviderSessionStore()
        with pytest.raises(ValueError, match="valid Unicode"):
            await store.get_session_id("\ud800", "claude")
        with pytest.raises(ValueError, match="valid Unicode"):
            await store.set_session_id("user", "claude", "\ud800")
        with pytest.raises(ValueError, match="valid Unicode"):
            await store.clear("\ud800")
        with pytest.raises(ValueError, match="valid Unicode"):
            await store.clear("user", "\ud800")

    asyncio.run(run())
