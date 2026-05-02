"""High-level session management — session reuse, rotation, fallback."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from soulacp.adapters.base_client import RPCError
from soulacp.config import FALLBACK_MAP, ACPConfig, resolve_client_class, resolve_provider
from soulacp.pool import ACPConnectionPool
from soulacp.retry import is_retryable
from soulacp.session_store import ProviderSessionStore

logger = logging.getLogger(__name__)


# Provider-specific overflow markers — server returns these in error.message
# when the CLI session has run out of context budget. Keep lowercase.
_OVERFLOW_MARKERS: tuple[str, ...] = (
    "prompt is too long",
    "context length",
    "context window",
    "too many tokens",
    "context limit",
)


def is_context_overflow(error: BaseException) -> bool:
    """Return True if *error* indicates the session ran out of context."""
    msg = str(error).lower()
    return any(marker in msg for marker in _OVERFLOW_MARKERS)


class ManagedSession:
    """High-level ACP session with automatic reuse, rotation, and fallback.

    Wraps ACPConnectionPool + ProviderSessionStore to provide:
    - Session reuse across requests (same user gets same CLI session)
    - Session rotation on context overflow ("prompt too long")
    - Retry with exponential backoff on transient errors
    - Fallback to alternate provider on persistent failure

    Usage::

        session = ManagedSession(
            provider="claude",
            model="claude-sonnet-4-20250514",
        )

        # Simple query
        response = await session.query("Hello!", user_id="user1")

        # Streaming
        async for chunk in session.stream("Write hello world", user_id="user1"):
            print(chunk, end="")

        await session.close()
    """

    def __init__(
        self,
        provider: str,
        model: str,
        session_store: ProviderSessionStore | None = None,
        config: ACPConfig | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._store = session_store or ProviderSessionStore()
        self._config = config or ACPConfig(provider=provider, model=model)
        self._pools: dict[str, ACPConnectionPool] = {}

    def _get_pool(self, provider: str | None = None, model: str | None = None) -> ACPConnectionPool:
        p = provider or self.provider
        m = model or self.model
        key = f"{p}:{m}"
        if key not in self._pools:
            cfg = ACPConfig(
                provider=p,
                model=m,
                pool_size=self._config.pool_size,
                pool_idle_timeout=self._config.pool_idle_timeout,
                pool_keepalive_interval=self._config.pool_keepalive_interval,
                timeout_connect=self._config.timeout_connect,
                timeout_prompt=self._config.timeout_prompt,
                timeout_stream=self._config.timeout_stream,
                cwd=self._config.cwd,
                auto_approve_permissions=self._config.auto_approve_permissions,
                enable_fallback=self._config.enable_fallback,
                max_retries=self._config.max_retries,
                retry_base_delay=self._config.retry_base_delay,
            )
            client_class = resolve_client_class(p)
            self._pools[key] = ACPConnectionPool(cfg, client_class)
        return self._pools[key]

    async def query(self, prompt: str, *, user_id: str | None = None) -> str:
        """Send a prompt and get the full response.

        Handles session reuse, rotation, retry, and fallback.
        """
        result = ""
        async for chunk in self._execute(prompt, user_id=user_id, stream=False):
            result = chunk  # Last chunk is full response
        return result

    async def stream(self, prompt: str, *, user_id: str | None = None) -> AsyncGenerator[str, None]:
        """Send a prompt and stream response chunks."""
        async for chunk in self._execute(prompt, user_id=user_id, stream=True):
            yield chunk

    async def _execute(
        self, prompt: str, *, user_id: str | None = None, stream: bool = False
    ) -> AsyncGenerator[str, None]:
        pool = self._get_pool()

        # Session store lookup
        session_id: str | None = None
        if self._store and user_id:
            try:
                session_id = await self._store.get_session_id(user_id, self.provider)
            except Exception as e:
                logger.debug("Session store lookup failed for user=%s provider=%s: %s", user_id, self.provider, e)

        max_attempts = self._config.max_retries
        base_delay = self._config.retry_base_delay
        session_rotated = False
        primary_err: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                async with pool.acquire(session_id=session_id) as (client, sid):
                    # Session changed (resume failed)
                    if session_id and sid != session_id:
                        logger.info("Session changed: %s -> %s", session_id, sid)

                    # Save actual session_id back to store
                    if self._store and user_id and sid:
                        try:
                            await self._store.set_session_id(user_id, self.provider, sid)
                        except Exception as e:
                            logger.debug("Session store save failed for user=%s: %s", user_id, e)

                    if stream:
                        async for chunk in client.query_stream(prompt):
                            yield chunk
                    else:
                        text = await client.query(prompt)
                        yield text
                return  # success

            except (ConnectionError, asyncio.TimeoutError) as ce:
                if attempt < max_attempts:
                    delay = min(base_delay * (2 ** (attempt - 1)), 30.0)
                    logger.warning(
                        "ACP error (attempt %d/%d), retrying in %.1fs: %s",
                        attempt,
                        max_attempts,
                        delay,
                        ce,
                    )
                    session_id = None
                    if self._store and user_id:
                        try:
                            await self._store.clear(user_id, self.provider)
                        except Exception as e:
                            logger.debug("Session store clear failed for user=%s: %s", user_id, e)
                    await asyncio.sleep(delay)
                    continue
                primary_err = ce
                break

            except Exception as exc:
                # Session rotation on context overflow
                if not session_rotated and is_context_overflow(exc):
                    logger.warning(
                        "Session context overflow (session=%s). Rotating to fresh session.",
                        session_id,
                    )
                    session_rotated = True
                    session_id = None
                    if self._store and user_id:
                        try:
                            await self._store.clear(user_id, self.provider)
                        except Exception as e:
                            logger.debug("Session store clear failed for user=%s: %s", user_id, e)
                    continue
                # Retry transient RPC errors using code-aware decision
                if attempt < max_attempts and is_retryable(exc):
                    delay = min(base_delay * (2 ** (attempt - 1)), 30.0)
                    code = exc.code if isinstance(exc, RPCError) else None
                    logger.warning(
                        "ACP retryable error (attempt %d/%d, code=%s), retrying in %.1fs: %s",
                        attempt, max_attempts, code, delay, exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                primary_err = exc
                break

        # Fallback to alternate provider
        if self._config.enable_fallback and primary_err:
            fallback_model = FALLBACK_MAP.get(self.provider)
            if fallback_model:
                fallback_provider = resolve_provider(fallback_model)
                logger.warning(
                    "Primary %s failed (%s), falling back to %s",
                    self.provider,
                    primary_err,
                    fallback_model,
                )
                fallback_pool = self._get_pool(fallback_provider, fallback_model)
                try:
                    async with fallback_pool.acquire() as (client, sid):
                        if stream:
                            async for chunk in client.query_stream(prompt):
                                yield chunk
                        else:
                            text = await client.query(prompt)
                            yield text
                    return
                except Exception as fb_err:
                    logger.error("Fallback %s also failed: %s", fallback_model, fb_err)

        if primary_err:
            raise primary_err

    async def close(self) -> None:
        """Close all connection pools."""
        for pool in self._pools.values():
            await pool.close_all()
        self._pools.clear()

    async def __aenter__(self) -> ManagedSession:
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
