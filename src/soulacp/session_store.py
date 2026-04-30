"""ProviderSessionStore — ACP session ID mapping for CLI subprocess reuse."""

from __future__ import annotations

import hashlib

from soulacp.cache import CacheBackend, MemoryCache

DEFAULT_TTL: int = 604_800  # 7 days in seconds


class ProviderSessionStore:
    """Maps ``(user_id, provider)`` to ACP ``session_id``.

    This enables session resumption across multiple ACP queries.

    Args:
        cache: Cache backend (defaults to :class:`MemoryCache`).
        ttl: Time-to-live for session mappings.
    """

    PROVIDERS = ("claude", "gemini", "opencode", "openclaw", "cursor", "cursor-cli", "codex", "qwen", "kimi", "codebuddy", "cline", "copilot", "minion", "vibe", "nova", "crow", "amp", "auggie", "autohand", "corust", "deepagents", "droid", "fastagent", "copilot-ls", "goose", "junie", "kilo", "pi", "qoder", "stakpak")

    def __init__(
        self,
        cache: CacheBackend | None = None,
        ttl: int = DEFAULT_TTL,
    ) -> None:
        self._cache = cache or MemoryCache()
        self._ttl = ttl

    @staticmethod
    def _hash_id(user_id: str) -> str:
        if user_id.isdigit():
            return hashlib.sha256(user_id.encode()).hexdigest()[:10]
        return user_id

    def _key(self, provider: str, user_id: str) -> str:
        return f"provider_session:{provider}:{self._hash_id(user_id)}"

    async def get_session_id(self, user_id: str, provider: str) -> str | None:
        """Retrieve the stored ACP session ID, or ``None``."""
        return await self._cache.get(self._key(provider, user_id))

    async def set_session_id(self, user_id: str, provider: str, session_id: str) -> None:
        """Store an ACP session ID mapping."""
        await self._cache.set(self._key(provider, user_id), session_id, ttl=self._ttl)

    async def clear(self, user_id: str, provider: str | None = None) -> None:
        """Clear session mappings.

        If *provider* is ``None``, clears all known providers.
        """
        providers = [provider] if provider else list(self.PROVIDERS)
        for p in providers:
            await self._cache.delete(self._key(p, user_id))
