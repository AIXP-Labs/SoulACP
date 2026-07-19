"""ProviderSessionStore — ACP session ID mapping for CLI subprocess reuse."""

from __future__ import annotations

import asyncio
import hashlib
import re

from soulacp.cache import CacheBackend, MemoryCache
from soulacp.config import FALLBACK_MAP, _is_finite_number

DEFAULT_TTL: int = 604_800  # 7 days in seconds
_AMBIGUOUS_LEGACY_ID = re.compile(r"(?:[0-9a-f]{10}|[0-9a-f]{32})\Z")


class ProviderSessionStore:
    """Maps ``(user_id, provider)`` to ACP ``session_id``.

    This enables session resumption across multiple ACP queries.

    Args:
        cache: Cache backend (defaults to :class:`MemoryCache`).
        ttl: Time-to-live for session mappings.
    """

    PROVIDERS = tuple(FALLBACK_MAP)

    def __init__(
        self,
        cache: CacheBackend | None = None,
        ttl: int = DEFAULT_TTL,
    ) -> None:
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0 or not _is_finite_number(ttl):
            raise ValueError("ttl must be an integer within the finite runtime range and >= 0")
        self._cache = cache if cache is not None else MemoryCache()
        self._ttl = ttl
        self._lock = asyncio.Lock()

    @staticmethod
    def _hash_id(user_id: str) -> str:
        """Return a fixed-width pseudonymous cache-key component."""
        return hashlib.sha256(user_id.encode()).hexdigest()[:32]

    @staticmethod
    def _legacy_hash_id(user_id: str) -> str:
        if user_id.isdigit():
            return hashlib.sha256(user_id.encode()).hexdigest()[:10]
        return user_id

    def _key(self, provider: str, user_id: str) -> str:
        return f"provider_session:v2:{provider}:{self._hash_id(user_id)}"

    def _legacy_key(self, provider: str, user_id: str) -> str:
        return f"provider_session:{provider}:{self._legacy_hash_id(user_id)}"

    def _legacy_key_if_unambiguous(self, provider: str, user_id: str) -> str | None:
        """Return a legacy key only when its historical owner is unique."""
        if (
            user_id.isdigit()
            or _AMBIGUOUS_LEGACY_ID.fullmatch(user_id) is not None
            or ":" in provider
            or ":" in user_id
        ):
            return None
        return self._legacy_key(provider, user_id)

    @staticmethod
    def _validate_identity(user_id: str, provider: str) -> None:
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider must be a non-empty string")
        try:
            user_id.encode("utf-8")
            provider.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("user_id and provider must contain valid Unicode") from exc

    async def get_session_id(self, user_id: str, provider: str) -> str | None:
        """Retrieve the stored ACP session ID, or ``None``."""
        self._validate_identity(user_id, provider)
        async with self._lock:
            key = self._key(provider, user_id)
            session_id = await self._cache.get(key)
            if isinstance(session_id, str) and session_id:
                return session_id
            if session_id is not None:
                await self._cache.delete(key)

            legacy_key = self._legacy_key_if_unambiguous(provider, user_id)
            if legacy_key is None:
                return None
            legacy_session_id = await self._cache.get(legacy_key)
            if not isinstance(legacy_session_id, str) or not legacy_session_id:
                if legacy_session_id is not None:
                    await self._cache.delete(legacy_key)
                return None
            await self._cache.set(key, legacy_session_id, ttl=self._ttl)
            await self._cache.delete(legacy_key)
            return legacy_session_id

    async def set_session_id(self, user_id: str, provider: str, session_id: str) -> None:
        """Store an ACP session ID mapping."""
        self._validate_identity(user_id, provider)
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        try:
            session_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("session_id must contain valid Unicode") from exc
        async with self._lock:
            key = self._key(provider, user_id)
            await self._cache.set(key, session_id, ttl=self._ttl)
            legacy_key = self._legacy_key_if_unambiguous(provider, user_id)
            if legacy_key is not None:
                await self._cache.delete(legacy_key)

    async def clear(self, user_id: str, provider: str | None = None) -> None:
        """Clear session mappings.

        If *provider* is ``None``, clears all known providers.
        """
        self._validate_identity(user_id, provider if provider is not None else self.PROVIDERS[0])
        providers = [provider] if provider else list(self.PROVIDERS)
        async with self._lock:
            for p in providers:
                await self._cache.delete(self._key(p, user_id))
                legacy_key = self._legacy_key_if_unambiguous(p, user_id)
                if legacy_key is not None:
                    await self._cache.delete(legacy_key)

    async def flush(self) -> None:
        """Persist pending cache writes."""
        async with self._lock:
            await self._cache.flush()
