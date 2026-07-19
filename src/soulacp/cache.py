"""Cache backends — abstract interface + Memory (LRU/TTL) and File (JSON) implementations."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import tempfile
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from contextlib import suppress
from pathlib import Path
from typing import Any

from soulacp._json import strict_json_loads
from soulacp.config import _is_finite_number
from soulacp.privacy import exception_summary

logger: logging.Logger = logging.getLogger(__name__)
MAX_CACHE_FILE_BYTES = 10 * 1024 * 1024


def _validate_cache_key(key: str) -> str:
    if not isinstance(key, str):
        raise TypeError("cache key must be a string")
    try:
        key.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("cache key must be valid UTF-8") from exc
    return key


def _validate_ttl(ttl: int) -> int:
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0 or not _is_finite_number(ttl):
        raise ValueError("ttl must be an integer within the finite runtime range and >= 0")
    return ttl


class CacheBackend(ABC):
    """Abstract cache backend with TTL support."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Retrieve a value, returning ``None`` on miss or expiry."""
        ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Store a value.  *ttl* ≤ 0 means no expiration."""
        ...

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a key.  Returns ``True`` if it existed."""
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check whether a non-expired key exists."""
        ...

    async def flush(self) -> None:
        """Persist pending mutations when the backend buffers writes."""
        return None


class MemoryCache(CacheBackend):
    """In-memory cache with LRU eviction and TTL expiration.

    Args:
        max_size: Maximum number of entries before LRU eviction.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        if isinstance(max_size, bool) or not isinstance(max_size, int) or max_size < 1:
            raise ValueError("max_size must be an integer >= 1")
        # key → (value, expire_at)   expire_at == 0 means "no expiry"
        self._data: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size

    async def get(self, key: str) -> Any | None:
        """Return a cached value and refresh its LRU position."""
        key = _validate_cache_key(key)
        if key not in self._data:
            return None
        value, expire_at = self._data[key]
        if expire_at and time.time() > expire_at:
            del self._data[key]
            return None
        self._data.move_to_end(key)  # mark as recently used
        return value

    async def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Store a value with an optional TTL in seconds."""
        key = _validate_cache_key(key)
        ttl = _validate_ttl(ttl)
        expire_at = time.time() + ttl if ttl > 0 else 0.0
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = (value, expire_at)
        # LRU eviction
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

    async def delete(self, key: str) -> bool:
        """Delete a key and report whether it existed."""
        key = _validate_cache_key(key)
        if key in self._data:
            del self._data[key]
            return True
        return False

    async def exists(self, key: str) -> bool:
        """Return whether a non-expired key exists."""
        key = _validate_cache_key(key)
        if key not in self._data:
            return False
        _, expire_at = self._data[key]
        if expire_at and time.time() > expire_at:
            del self._data[key]
            return False
        return True


class FileCache(CacheBackend):
    """JSON-file-backed cache with TTL support and write debounce.

    Each entry is stored as ``{"value": ..., "expire_at": ...}``.
    Expired entries are pruned on load.
    One instance must exclusively own a given path; coordinating multiple
    instances or processes requires an external cache backend.

    Args:
        path: Path to the JSON cache file.
        debounce_seconds: Minimum interval between disk writes.
            Multiple mutations within this window are coalesced into a
            single write.  Set to ``0`` to write immediately (default).
    """

    def __init__(self, path: str | Path, debounce_seconds: float = 0) -> None:
        if isinstance(debounce_seconds, bool) or not isinstance(debounce_seconds, (int, float)):
            raise ValueError("debounce_seconds must be a number")
        if not _is_finite_number(debounce_seconds) or debounce_seconds < 0:
            raise ValueError("debounce_seconds must be finite and >= 0")
        self._lock = asyncio.Lock()
        self._path = Path(path).expanduser()
        self._data: dict[str, dict[str, Any]] = {}
        self._debounce = float(debounce_seconds)
        self._save_task: asyncio.Task[None] | None = None
        self._dirty = False
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("rb") as cache_file:
                content = cache_file.read(MAX_CACHE_FILE_BYTES + 1)
            if len(content) > MAX_CACHE_FILE_BYTES:
                logger.warning("Ignoring cache file larger than %d bytes", MAX_CACHE_FILE_BYTES)
                return
            raw = strict_json_loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeError, ValueError, OSError, RecursionError) as exc:
            logger.warning("Ignoring unreadable cache file: %s", exception_summary(exc))
            return
        if not isinstance(raw, dict):
            logger.warning("Ignoring cache file with non-object root")
            return
        now = time.time()
        loaded: dict[str, dict[str, Any]] = {}
        for key, entry in raw.items():
            if not isinstance(key, str) or not isinstance(entry, dict) or "value" not in entry:
                continue
            try:
                validated_key = _validate_cache_key(key)
                value = self._normalize_value(entry["value"])
            except (TypeError, ValueError, UnicodeError, RecursionError):
                continue
            expire_at = entry.get("expire_at", 0)
            if isinstance(expire_at, bool) or not isinstance(expire_at, (int, float)):
                continue
            if not _is_finite_number(expire_at):
                continue
            if expire_at and expire_at <= now:
                continue
            loaded_entry: dict[str, Any] = {"value": value}
            if expire_at:
                loaded_entry["expire_at"] = expire_at
            loaded[validated_key] = loaded_entry
        self._data = loaded

    @staticmethod
    def _normalize_value(value: Any) -> Any:
        """Detach a value and convert it to the representation stored in JSON."""
        content = json.dumps(value, ensure_ascii=False, allow_nan=False)
        content.encode("utf-8")
        return strict_json_loads(content)

    @staticmethod
    def _serialize_snapshot(snapshot: dict[str, dict[str, Any]]) -> bytes:
        content = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_CACHE_FILE_BYTES:
            raise ValueError(f"cache snapshot exceeds the {MAX_CACHE_FILE_BYTES}-byte file limit")
        return encoded

    def _save_snapshot(self, snapshot: dict[str, dict[str, Any]]) -> None:
        """Write data to disk atomically (temp file + replace)."""
        content = self._serialize_snapshot(snapshot)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            Path(tmp_path).replace(self._path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    async def _persist_locked(self) -> None:
        snapshot = copy.deepcopy(self._data)
        writer = asyncio.create_task(asyncio.to_thread(self._save_snapshot, snapshot))
        cancelled = False
        write_error: BaseException | None = None
        while True:
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError as exc:
                if writer.cancelled():
                    write_error = exc
                    break
                cancelled = True
                continue
            except BaseException as exc:
                write_error = exc
                break
            else:
                break

        self._dirty = write_error is not None
        if cancelled:
            if write_error is not None:
                logger.error(
                    "Cache persistence failed while its caller was cancelled: %s",
                    exception_summary(write_error),
                )
            raise asyncio.CancelledError
        if write_error is not None:
            raise write_error

    async def _schedule_save_locked(self) -> None:
        """Persist now or start one cancellation-safe debounce task."""
        self._dirty = True
        if self._debounce <= 0:
            await self._persist_locked()
            return
        if self._save_task and not self._save_task.done():
            return
        task = asyncio.create_task(self._debounced_save())
        self._save_task = task

        def _consume_result(done: asyncio.Task[None]) -> None:
            if not done.cancelled():
                with suppress(Exception):
                    done.exception()

        task.add_done_callback(_consume_result)

    async def _debounced_save(self) -> None:
        """Wait for the debounce window, then write."""
        failed = False
        try:
            await asyncio.sleep(self._debounce)
            async with self._lock:
                if self._dirty:
                    await self._persist_locked()
        except asyncio.CancelledError:
            failed = True
            raise
        except Exception as exc:
            failed = True
            logger.error("Debounced cache save failed: %s", exception_summary(exc))
        finally:
            async with self._lock:
                if self._save_task is asyncio.current_task():
                    self._save_task = None
                if self._dirty and not failed and self._save_task is None:
                    await self._schedule_save_locked()

    # ------------------------------------------------------------------
    # CacheBackend interface
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        """Return a detached cached value, or ``None`` on miss or expiry."""
        key = _validate_cache_key(key)
        async with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            if entry.get("expire_at") and time.time() > entry["expire_at"]:
                del self._data[key]
                await self._schedule_save_locked()
                return None
            return copy.deepcopy(entry["value"])

    async def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Store a JSON-compatible value and schedule durable persistence."""
        key = _validate_cache_key(key)
        ttl = _validate_ttl(ttl)
        try:
            normalized_value = await asyncio.to_thread(self._normalize_value, value)
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError("FileCache values must be standard JSON serializable") from exc
        async with self._lock:
            entry: dict[str, Any] = {"value": normalized_value}
            if ttl > 0:
                entry["expire_at"] = time.time() + ttl
            candidate = dict(self._data)
            candidate[key] = entry
            await asyncio.to_thread(self._serialize_snapshot, candidate)
            self._data[key] = entry
            await self._schedule_save_locked()

    async def delete(self, key: str) -> bool:
        """Delete a key and schedule persistence when it existed."""
        key = _validate_cache_key(key)
        async with self._lock:
            if key in self._data:
                del self._data[key]
                await self._schedule_save_locked()
                return True
            return False

    async def exists(self, key: str) -> bool:
        """Return whether a non-expired key exists."""
        key = _validate_cache_key(key)
        async with self._lock:
            entry = self._data.get(key)
            if not entry:
                return False
            if entry.get("expire_at") and time.time() > entry["expire_at"]:
                del self._data[key]
                await self._schedule_save_locked()
                return False
            return True

    async def flush(self) -> None:
        """Durably persist the latest snapshot before propagating cancellation."""
        flush_task = asyncio.create_task(self._finish_flush())
        cancelled = False
        flush_error: BaseException | None = None
        while True:
            try:
                await asyncio.shield(flush_task)
            except asyncio.CancelledError as exc:
                if flush_task.cancelled():
                    flush_error = exc
                    break
                cancelled = True
                continue
            except BaseException as exc:
                flush_error = exc
                break
            else:
                break

        if cancelled:
            if flush_error is not None:
                logger.error(
                    "Cache flush failed while its caller was cancelled: %s",
                    exception_summary(flush_error),
                )
            raise asyncio.CancelledError
        if flush_error is not None:
            raise flush_error

    async def _finish_flush(self) -> None:
        """Own debounce cancellation and persistence independently of the caller."""
        task = self._save_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        async with self._lock:
            if self._save_task is task:
                self._save_task = None
            if self._dirty:
                await self._persist_locked()

    async def close(self) -> None:
        """Flush pending mutations."""
        await self.flush()
