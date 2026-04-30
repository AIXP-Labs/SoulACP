"""Cache backends — abstract interface + Memory (LRU/TTL) and File (JSON) implementations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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


class MemoryCache(CacheBackend):
    """In-memory cache with LRU eviction and TTL expiration.

    Args:
        max_size: Maximum number of entries before LRU eviction.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        # key → (value, expire_at)   expire_at == 0 means "no expiry"
        self._data: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_size = max_size

    async def get(self, key: str) -> Any | None:
        if key not in self._data:
            return None
        value, expire_at = self._data[key]
        if expire_at and time.time() > expire_at:
            del self._data[key]
            return None
        self._data.move_to_end(key)  # mark as recently used
        return value

    async def set(self, key: str, value: Any, ttl: int = 0) -> None:
        expire_at = time.time() + ttl if ttl > 0 else 0.0
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = (value, expire_at)
        # LRU eviction
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

    async def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            return True
        return False

    async def exists(self, key: str) -> bool:
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

    Args:
        path: Path to the JSON cache file.
        debounce_seconds: Minimum interval between disk writes.
            Multiple mutations within this window are coalesced into a
            single write.  Set to ``0`` to write immediately (default).
    """

    def __init__(self, path: str | Path, debounce_seconds: float = 0) -> None:
        self._lock = asyncio.Lock()
        self._path = Path(path)
        self._data: dict[str, dict[str, Any]] = {}
        self._debounce = debounce_seconds
        self._save_task: asyncio.Task[None] | None = None
        self._load()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw: dict = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        now = time.time()
        self._data = {k: v for k, v in raw.items() if not v.get("expire_at") or v["expire_at"] > now}

    def _save(self) -> None:
        """Write data to disk atomically (temp file + replace)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self._data, ensure_ascii=False)
        fd, tmp_path = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            Path(tmp_path).replace(self._path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def _schedule_save(self) -> None:
        """Persist data, respecting the debounce window.

        If ``debounce_seconds == 0`` the write happens immediately.
        Otherwise, a background task coalesces rapid successive mutations
        into a single disk write.
        """
        if self._debounce <= 0:
            self._save()
            return
        # If a debounced save is already pending, let it handle the write
        if self._save_task and not self._save_task.done():
            return
        try:
            self._save_task = asyncio.create_task(self._debounced_save())
        except RuntimeError:
            # No running event loop — fall back to immediate save
            self._save()

    async def _debounced_save(self) -> None:
        """Wait for the debounce window, then write."""
        await asyncio.sleep(self._debounce)
        self._save()

    # ------------------------------------------------------------------
    # CacheBackend interface
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            if entry.get("expire_at") and time.time() > entry["expire_at"]:
                del self._data[key]
                self._schedule_save()
                return None
            return entry["value"]

    async def set(self, key: str, value: Any, ttl: int = 0) -> None:
        async with self._lock:
            entry: dict[str, Any] = {"value": value}
            if ttl > 0:
                entry["expire_at"] = time.time() + ttl
            self._data[key] = entry
            self._schedule_save()

    async def delete(self, key: str) -> bool:
        async with self._lock:
            if key in self._data:
                del self._data[key]
                self._schedule_save()
                return True
            return False

    async def exists(self, key: str) -> bool:
        async with self._lock:
            entry = self._data.get(key)
            if not entry:
                return False
            if entry.get("expire_at") and time.time() > entry["expire_at"]:
                del self._data[key]
                self._schedule_save()
                return False
            return True
