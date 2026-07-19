"""Test cache backends."""

import asyncio
import json
import tempfile
import threading
from pathlib import Path

import pytest


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


def test_memory_cache_ttl(monkeypatch):
    import soulacp.cache as cache_module

    class Clock:
        now = 1_000.0

        @classmethod
        def time(cls) -> float:
            return cls.now

    monkeypatch.setattr(cache_module, "time", Clock)
    cache = cache_module.MemoryCache()

    async def run():
        await cache.set("k1", "v1", ttl=1)
        assert await cache.get("k1") == "v1"
        Clock.now += 1.1
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


def test_file_cache_ttl(monkeypatch):
    import soulacp.cache as cache_module

    class Clock:
        now = 1_000.0

        @classmethod
        def time(cls) -> float:
            return cls.now

    monkeypatch.setattr(cache_module, "time", Clock)

    async def run():
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cache.json"
            cache = cache_module.FileCache(path)
            await cache.set("k1", "v1", ttl=1)
            assert await cache.get("k1") == "v1"
            Clock.now += 1.1
            assert await cache.get("k1") is None

    asyncio.run(run())


@pytest.mark.asyncio
async def test_file_cache_flush_persists_debounced_mutations(tmp_path):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    cache = FileCache(path, debounce_seconds=60)
    await cache.set("key", "value")
    assert not path.exists()

    await cache.flush()

    assert await FileCache(path).get("key") == "value"
    assert cache._save_task is None or cache._save_task.done()


@pytest.mark.asyncio
async def test_file_cache_flush_propagates_caller_cancellation_after_persisting(monkeypatch, tmp_path):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    cache = FileCache(path, debounce_seconds=60)
    debounce_cancelled = asyncio.Event()
    allow_debounce_exit = asyncio.Event()

    async def delayed_debounce_exit():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            debounce_cancelled.set()
            await allow_debounce_exit.wait()

    monkeypatch.setattr(cache, "_debounced_save", delayed_debounce_exit)
    await cache.set("key", "value")

    flushing = asyncio.create_task(cache.flush())
    await debounce_cancelled.wait()
    flushing.cancel()
    allow_debounce_exit.set()

    with pytest.raises(asyncio.CancelledError):
        await flushing

    assert await FileCache(path).get("key") == "value"
    assert cache._dirty is False


@pytest.mark.asyncio
async def test_file_cache_persistence_survives_repeated_caller_cancellation(monkeypatch, tmp_path):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    cache = FileCache(path)
    original_save = cache._save_snapshot
    save_started = threading.Event()
    allow_save = threading.Event()

    def blocked_save(snapshot):
        save_started.set()
        assert allow_save.wait(timeout=2)
        original_save(snapshot)

    monkeypatch.setattr(cache, "_save_snapshot", blocked_save)
    setting = asyncio.create_task(cache.set("key", "value"))
    assert await asyncio.to_thread(save_started.wait, 1)

    setting.cancel()
    await asyncio.sleep(0)
    setting.cancel()
    await asyncio.sleep(0)
    assert not setting.done()

    allow_save.set()
    with pytest.raises(asyncio.CancelledError):
        await setting

    assert await FileCache(path).get("key") == "value"
    assert cache._dirty is False


@pytest.mark.asyncio
async def test_file_cache_detaches_values_from_caller_mutation(tmp_path):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    cache = FileCache(path, debounce_seconds=60)
    original = {"items": ["first"]}
    await cache.set("key", original)

    original["items"].append("mutated-after-set")
    returned = await cache.get("key")
    returned["items"].append("mutated-after-get")
    await cache.flush()

    expected = {"items": ["first"]}
    assert await cache.get("key") == expected
    assert await FileCache(path).get("key") == expected


@pytest.mark.asyncio
async def test_file_cache_immediately_returns_the_persisted_json_shape(tmp_path):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    cache = FileCache(path)
    await cache.set("key", ("tuple", {1: "integer-key"}))

    expected = ["tuple", {"1": "integer-key"}]
    assert await cache.get("key") == expected
    assert await FileCache(path).get("key") == expected


@pytest.mark.asyncio
async def test_cache_backends_reject_keys_that_cannot_round_trip_through_json(tmp_path):
    from soulacp.cache import FileCache, MemoryCache

    async def assert_rejected(cache, key, error):
        with pytest.raises(error):
            await cache.get(key)
        with pytest.raises(error):
            await cache.set(key, "value")
        with pytest.raises(error):
            await cache.delete(key)
        with pytest.raises(error):
            await cache.exists(key)

    for cache in (MemoryCache(), FileCache(tmp_path / "cache.json")):
        await assert_rejected(cache, 1, TypeError)
        await assert_rejected(cache, "\ud800", ValueError)


@pytest.mark.asyncio
async def test_cache_backends_reject_ttl_outside_runtime_float_range(tmp_path):
    from soulacp.cache import FileCache, MemoryCache

    for cache in (MemoryCache(), FileCache(tmp_path / "cache.json")):
        with pytest.raises(ValueError, match="ttl"):
            await cache.set("key", "value", ttl=10**400)
        assert await cache.get("key") is None


def test_file_cache_expands_user_path(monkeypatch, tmp_path):
    from soulacp.cache import FileCache

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cache = FileCache("~/soulacp-cache.json")

    assert cache._path == tmp_path / "soulacp-cache.json"


def test_file_cache_ignores_structurally_invalid_entries(tmp_path):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"bad": "value", "good": {"value": "ok"}}), encoding="utf-8")
    cache = FileCache(path)

    assert "bad" not in cache._data
    assert cache._data["good"]["value"] == "ok"


def test_file_cache_ignores_expiry_outside_runtime_float_range(tmp_path):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    path.write_text(
        '{"bad":{"value":"ignored","expire_at":' + "9" * 400 + '},"good":{"value":"ok"}}',
        encoding="utf-8",
    )

    assert FileCache(path)._data == {"good": {"value": "ok"}}


@pytest.mark.asyncio
async def test_file_cache_rejects_non_json_values_without_mutating(tmp_path):
    from soulacp.cache import FileCache

    cache = FileCache(tmp_path / "cache.json")

    with pytest.raises(ValueError, match="JSON serializable"):
        await cache.set("bad", object())
    with pytest.raises(ValueError, match="JSON serializable"):
        await cache.set("nan", float("nan"))

    assert await cache.get("bad") is None
    assert await cache.get("nan") is None


def test_file_cache_ignores_invalid_utf8_and_nonstandard_json(tmp_path):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    path.write_bytes(b"\xff")
    assert FileCache(path)._data == {}

    path.write_text('{"bad": {"value": NaN}}', encoding="utf-8")
    assert FileCache(path)._data == {}


@pytest.mark.parametrize(
    "content",
    [
        '{"key":{"value":"first"},"key":{"value":"second"}}',
        '{"key":{"value":{"role":"user","role":"admin"}}}',
    ],
)
def test_file_cache_ignores_json_with_duplicate_object_keys(tmp_path, content):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    path.write_text(content, encoding="utf-8")

    assert FileCache(path)._data == {}


def test_file_cache_ignores_json_escaped_invalid_unicode(tmp_path):
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    path.write_text(
        r'{"\ud800": {"value": "bad-key"}, "bad-value": {"value": "\ud800"}, "good": {"value": "ok"}}',
        encoding="utf-8",
    )

    assert FileCache(path)._data == {"good": {"value": "ok"}}


@pytest.mark.asyncio
async def test_file_cache_rejects_oversized_snapshot_without_mutating(monkeypatch, tmp_path):
    from soulacp import cache as cache_module
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    monkeypatch.setattr(cache_module, "MAX_CACHE_FILE_BYTES", 64)
    cache = FileCache(path)
    await cache.set("small", "ok")
    persisted = path.read_bytes()

    with pytest.raises(ValueError, match="cache snapshot exceeds"):
        await cache.set("large", "x" * 100)

    assert await cache.get("large") is None
    assert await cache.get("small") == "ok"
    assert path.read_bytes() == persisted


def test_file_cache_ignores_oversized_file(monkeypatch, tmp_path):
    from soulacp import cache as cache_module
    from soulacp.cache import FileCache

    path = tmp_path / "cache.json"
    path.write_text('{"key": {"value": "value"}}', encoding="utf-8")
    monkeypatch.setattr(cache_module, "MAX_CACHE_FILE_BYTES", 1)

    cache = FileCache(path)

    assert cache._data == {}


def test_cache_configuration_validation(tmp_path):
    from soulacp.cache import FileCache, MemoryCache

    with pytest.raises(ValueError):
        MemoryCache(max_size=0)
    with pytest.raises(ValueError):
        FileCache(tmp_path / "cache.json", debounce_seconds=-1)
    with pytest.raises(ValueError, match="debounce_seconds"):
        FileCache(tmp_path / "cache.json", debounce_seconds=10**400)
