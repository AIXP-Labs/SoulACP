"""Test cache backends."""

import asyncio
import tempfile
from pathlib import Path


def test_memory_cache_basic():
    from soulacp.cache import MemoryCache

    async def run():
        cache = MemoryCache()
        await cache.set("k1", "v1")
        assert await cache.get("k1") == "v1"
        assert await cache.exists("k1") is True
        assert await cache.get("missing") is None
        assert await cache.exists("missing") is False

    asyncio.run(run())


def test_memory_cache_ttl():
    from soulacp.cache import MemoryCache

    async def run():
        cache = MemoryCache()
        await cache.set("k1", "v1", ttl=1)
        assert await cache.get("k1") == "v1"
        await asyncio.sleep(1.1)
        assert await cache.get("k1") is None

    asyncio.run(run())


def test_memory_cache_lru_eviction():
    from soulacp.cache import MemoryCache

    async def run():
        cache = MemoryCache(max_size=3)
        await cache.set("a", 1)
        await cache.set("b", 2)
        await cache.set("c", 3)
        await cache.set("d", 4)  # Evicts "a"
        assert await cache.get("a") is None
        assert await cache.get("b") == 2

    asyncio.run(run())


def test_memory_cache_delete():
    from soulacp.cache import MemoryCache

    async def run():
        cache = MemoryCache()
        await cache.set("k1", "v1")
        assert await cache.delete("k1") is True
        assert await cache.delete("k1") is False
        assert await cache.get("k1") is None

    asyncio.run(run())


def test_file_cache_basic():
    from soulacp.cache import FileCache

    async def run():
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"
            cache = FileCache(path)
            await cache.set("k1", "v1")
            assert await cache.get("k1") == "v1"

            # Verify persisted to disk
            cache2 = FileCache(path)
            assert await cache2.get("k1") == "v1"

    asyncio.run(run())


def test_file_cache_ttl():
    from soulacp.cache import FileCache

    async def run():
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"
            cache = FileCache(path)
            await cache.set("k1", "v1", ttl=1)
            assert await cache.get("k1") == "v1"
            await asyncio.sleep(1.1)
            assert await cache.get("k1") is None

    asyncio.run(run())
