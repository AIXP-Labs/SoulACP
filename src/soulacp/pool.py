"""ACP connection pool — reuses CLI subprocess connections."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from soulacp.config import ACPConfig
from soulacp.retry import retry_async

if TYPE_CHECKING:
    from soulacp.adapters.protocol import ACPClient

logger = logging.getLogger(__name__)


class ACPConnectionPool:
    """Connection pool for ACP CLI subprocess clients.

    Maintains a pool of idle connections that can be reused, with:
    - Session ID matching for conversation continuity
    - Idle timeout cleanup
    - Max pool size enforcement
    - Concurrent-safe acquire/release via asyncio.Lock

    Usage::

        pool = ACPConnectionPool(config, ClaudeACPClient)

        async with pool.acquire(session_id="abc") as (client, sid):
            text = await client.query("Hello")
        # client is returned to pool automatically
    """

    def __init__(self, config: ACPConfig, client_class: type[ACPClient]) -> None:
        self._config = config
        self._client_class = client_class
        self._pool: list[ACPClient] = []
        self._lock = asyncio.Lock()
        self._keepalive_task: asyncio.Task | None = None

    @property
    def size(self) -> int:
        """Number of idle connections currently in the pool."""
        return len(self._pool)

    @asynccontextmanager
    async def acquire(self, session_id: str | None = None) -> AsyncGenerator[tuple[ACPClient, str | None], None]:
        """Acquire a client connection from the pool.

        Priority:
        1. Match by ``session_id`` (reuse conversation context)
        2. Any idle connection
        3. Create new connection

        Yields:
            A tuple of ``(client, session_id)`` where ``session_id`` is the
            client's current session (may differ from the requested one if
            resume was not needed or failed).
        """
        client: ACPClient | None = None

        async with self._lock:
            # Clean up expired / disconnected connections
            alive = []
            for c in self._pool:
                if c.is_connected and not c.is_idle_timeout:
                    alive.append(c)
                else:
                    # Disconnect expired in background
                    asyncio.create_task(self._safe_disconnect(c))
            self._pool = alive

            # 1. Match by session_id
            if session_id:
                for i, c in enumerate(self._pool):
                    if c.session_id == session_id:
                        client = self._pool.pop(i)
                        break

            # 2. Any idle connection
            if client is None and self._pool:
                client = self._pool.pop(0)

        # 3. Create new connection (outside lock — may be slow)
        if client is None:
            client = await self._create_client()

        # Health check: verify the connection is actually alive (Doc 10 A1)
        if client.session_id is not None:
            try:
                alive = await asyncio.wait_for(client.ping(), timeout=5)
            except Exception as e:
                logger.debug("Pool health check failed for session=%s: %s", client.session_id, e)
                alive = False
            if not alive:
                logger.warning("Pool health check failed, creating fresh connection")
                asyncio.create_task(self._safe_disconnect(client))
                client = await self._create_client()

        # Ensure session exists (for delayed-session providers like Gemini)
        if hasattr(client, "ensure_session") and client.session_id is None:
            await client.ensure_session()

        # Resume session if needed (outside lock)
        try:
            if session_id and client.session_id != session_id:
                if not await client.resume(session_id):
                    logger.warning(
                        "Session resume failed: %s -> starting fresh session %s",
                        session_id,
                        client.session_id,
                    )

            yield client, client.session_id

        except Exception:
            # On error, disconnect and don't return to pool
            await client.disconnect()
            raise

        else:
            # Return to pool
            async with self._lock:
                if len(self._pool) < self._config.pool_size and client.is_connected:
                    self._pool.append(client)
                else:
                    asyncio.create_task(self._safe_disconnect(client))

    async def _safe_disconnect(self, client: ACPClient) -> None:
        """Disconnect client, logging any errors (Doc 10 C4)."""
        try:
            await client.disconnect()
        except Exception as e:
            logger.debug("Error during client disconnect: %s", e)

    # ------------------------------------------------------------------
    # Keepalive (Doc 10 B1)
    # ------------------------------------------------------------------

    def start_keepalive(self) -> None:
        """Start background keepalive loop. Safe to call without a running loop."""
        interval = self._config.pool_keepalive_interval
        if interval <= 0 or self._keepalive_task is not None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # No running event loop — keepalive deferred
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Periodically check pool connections and remove dead ones."""
        interval = self._config.pool_keepalive_interval
        while True:
            await asyncio.sleep(interval)
            async with self._lock:
                alive = []
                for c in self._pool:
                    if not c.is_connected or c.is_idle_timeout:
                        asyncio.create_task(self._safe_disconnect(c))
                        continue
                    try:
                        ok = await asyncio.wait_for(c.ping(), timeout=5)
                    except Exception:
                        ok = False
                    if ok:
                        alive.append(c)
                    else:
                        logger.info("Keepalive: removing dead connection (session=%s)", c.session_id)
                        asyncio.create_task(self._safe_disconnect(c))
                self._pool = alive

    # ------------------------------------------------------------------

    async def close_all(self) -> None:
        """Disconnect all pooled connections."""
        if self._keepalive_task:
            self._keepalive_task.cancel()
            self._keepalive_task = None

        async with self._lock:
            clients = list(self._pool)
            self._pool.clear()

        for c in clients:
            try:
                await c.disconnect()
            except Exception as e:
                logger.debug("Error closing pooled client: %s", e)

    def get_stats(self) -> dict:
        """Return pool statistics."""
        return {
            "pool_size": len(self._pool),
            "max_pool_size": self._config.pool_size,
            "connected": sum(1 for c in self._pool if c.is_connected),
            "provider": self._config.provider,
        }

    async def _create_client(self) -> ACPClient:
        """Create and connect a new client with retry on transient errors."""

        async def _do_create() -> ACPClient:
            client = self._client_class(self._config)
            await client.connect()
            return client

        return await retry_async(
            _do_create,
            max_retries=self._config.max_retries,
            base_delay=self._config.retry_base_delay,
        )
