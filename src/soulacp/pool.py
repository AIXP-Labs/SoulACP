"""Session-safe ACP subprocess pool with bounded lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, cast

from soulacp._async import wait_for_owned
from soulacp.config import ACPConfig, _require_valid_unicode
from soulacp.errors import ACPPoolAcquireTimeoutError, ACPPoolClosedError, ACPPoolError, ACPResumeError
from soulacp.privacy import exception_summary
from soulacp.retry import retry_async
from soulacp.telemetry import PoolTelemetry

if TYPE_CHECKING:
    from soulacp.adapters.protocol import ACPClient

logger: logging.Logger = logging.getLogger(__name__)

EntryState = Literal["idle", "leased", "checking", "closing"]


@dataclass(slots=True)
class PoolEntry:
    """Pool-owned lifecycle metadata for one physical client transport."""

    client: ACPClient
    created_at: float
    released_at: float | None = None
    use_count: int = 0
    state: EntryState = "leased"
    close_reason: str | None = None
    close_task: asyncio.Task[None] | None = None
    claimed_session_id: str | None = None


@dataclass(slots=True)
class PoolWaiter:
    """FIFO capacity waiter."""

    session_id: str | None
    fresh: bool
    enqueued_at: float
    event: asyncio.Event = field(default_factory=asyncio.Event)


class ACPConnectionPool:
    """Bounded pool of ACP subprocess clients.

    ``pool_size`` limits retained idle clients. ``pool_max_connections`` is a
    hard limit for this pool instance and counts idle, leased, checking,
    closing, and currently-creating transports.
    """

    def __init__(
        self,
        config: ACPConfig,
        client_class: Callable[[ACPConfig], ACPClient],
        *,
        clock: Callable[[], float] = time.monotonic,
        telemetry: PoolTelemetry | None = None,
        startup_attempts: int | None = None,
    ) -> None:
        # Pool policy is a construction-time contract.  Revalidate and detach
        # mutable config fields so caller changes cannot bypass capacity or
        # lifecycle invariants after this pool starts serving leases.
        config = replace(config)
        if startup_attempts is None:
            startup_attempts = max(1, config.max_retries)
        if isinstance(startup_attempts, bool) or not isinstance(startup_attempts, int) or startup_attempts < 1:
            raise ValueError("startup_attempts must be an integer >= 1")
        self._config = config
        self._client_class = client_class
        self._startup_attempts = startup_attempts
        self._clock = clock
        self._entries: dict[int, PoolEntry] = {}
        self._unreaped_clients: dict[int, ACPClient] = {}
        self._untracked_close_tasks: dict[int, asyncio.Task[None]] = {}
        self._idle: deque[int] = deque()
        self._creating = 0
        self._creating_zero = asyncio.Event()
        self._creating_zero.set()
        self._waiters: deque[PoolWaiter] = deque()
        self._session_claims: set[str] = set()
        self._lock = asyncio.Lock()
        self._closed = False
        self._keepalive_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._close_all_task: asyncio.Task[None] | None = None
        self._stats: Counter[str] = Counter()
        self._created_by_reason: Counter[str] = Counter()
        self._evictions: Counter[str] = Counter()
        self._peak_total_open = 0
        self._peak_total_reserved = 0
        self._telemetry = telemetry or PoolTelemetry(config.provider)

    @property
    def size(self) -> int:
        """Number of clients currently available in the idle queue."""
        return sum(1 for entry in self._entries.values() if entry.state == "idle")

    @asynccontextmanager
    async def acquire(
        self,
        session_id: str | None = None,
        *,
        fresh: bool = False,
    ) -> AsyncGenerator[tuple[ACPClient, str | None], None]:
        """Acquire a safe client lease.

        With cross-session reuse disabled, an existing client is eligible only
        for an exact requested session, or when both requested and current
        session IDs are ``None``. ``fresh=True`` always creates a new transport
        but still obeys the hard capacity limit and FIFO wait timeout.
        """
        if session_id is not None and (not isinstance(session_id, str) or not session_id):
            raise ValueError("session_id must be a non-empty string or None")
        if session_id is not None:
            _require_valid_unicode(session_id, "session_id")
        if not isinstance(fresh, bool):
            raise TypeError("fresh must be a bool")
        self.start_keepalive()
        started_at = self._clock()
        entry, source, waited = await self._checkout(session_id, fresh=fresh)
        elapsed = self._clock() - started_at

        self._stats["acquisitions_total"] += 1
        if source in {"new", "fresh"}:
            if source == "fresh":
                self._stats["fresh_total"] += 1
        else:
            self._stats["reused_total"] += 1
        if source == "exact_session":
            self._stats["exact_session_hits"] += 1
        elif source == "cross_session":
            self._stats["cross_session_reuses"] += 1

        self._telemetry.add("acquisitions", source=source, result="success", fresh=fresh)
        self._telemetry.record("acquire_duration", elapsed, source=source, result="success")
        if waited > 0:
            self._telemetry.record("wait_duration", waited, result="success")

        try:
            yield entry.client, entry.client.session_id
        except BaseException as lease_error:
            try:
                await self._retire_entry(entry, "lease_error")
            except asyncio.CancelledError:
                raise
            except BaseException as cleanup_error:
                if isinstance(lease_error, GeneratorExit):
                    raise cleanup_error
                logger.error(
                    "ACP lease cleanup failed after leased operation failed: %s",
                    exception_summary(cleanup_error),
                )
            raise
        else:
            await self._release_entry(entry)

    async def _checkout(
        self,
        session_id: str | None,
        *,
        fresh: bool,
    ) -> tuple[PoolEntry, str, float]:
        waiter: PoolWaiter | None = None
        waited = 0.0
        creation_reason = "fresh" if fresh else ("session_resume" if session_id else "new_session")

        while True:
            entry: PoolEntry | None = None
            source = "fresh" if fresh else "new"
            reserve_creation = False
            entries_to_close: list[PoolEntry] = []
            untracked_clients_to_close: list[ACPClient] = []

            async with self._lock:
                if self._closed:
                    self._remove_waiter_locked(waiter)
                    raise ACPPoolClosedError("connection pool is closed")

                entries_to_close.extend(self._retire_idle_locked(self._clock()))
                entries_to_close.extend(
                    entry
                    for entry in self._entries.values()
                    if entry.state == "closing" and (entry.close_task is None or entry.close_task.done())
                )
                untracked_clients_to_close.extend(
                    client
                    for key, client in self._unreaped_clients.items()
                    if key not in self._entries
                    and (key not in self._untracked_close_tasks or self._untracked_close_tasks[key].done())
                )

                if self._waiters and (waiter is None or self._waiters[0] is not waiter):
                    waiter = self._enqueue_waiter_locked(waiter, session_id, fresh)
                elif session_id is not None and session_id in self._session_claims:
                    waiter = self._enqueue_waiter_locked(waiter, session_id, fresh)
                    waiter.event.clear()
                else:
                    entry, source = self._select_idle_locked(session_id, fresh=fresh)
                    if entry is not None:
                        self._claim_session_locked(entry, session_id)
                        waited = self._dequeue_selected_waiter_locked(waiter)
                        waiter = None
                    elif self._total_reserved_locked() < self._config.pool_max_connections:
                        self._reserve_session_claim_locked(session_id)
                        waited = self._dequeue_selected_waiter_locked(waiter)
                        waiter = None
                        self._creating += 1
                        self._creating_zero.clear()
                        self._update_peaks_locked()
                        reserve_creation = True
                    else:
                        replacement = self._select_replacement_locked()
                        if replacement is not None and self._mark_closing_locked(replacement, "capacity_rebalance"):
                            entries_to_close.append(replacement)
                            self._stats["capacity_rebalances_total"] += 1
                            creation_reason = "capacity_rebalance"
                        waiter = self._enqueue_waiter_locked(waiter, session_id, fresh)
                        waiter.event.clear()

            for closing in entries_to_close:
                self._ensure_close_task(closing)
            for client in untracked_clients_to_close:
                self._ensure_untracked_close_task(client)

            if entry is not None:
                try:
                    prepared = await self._prepare_entry(entry, session_id, source)
                except BaseException:
                    await self._retire_entry(entry, "prepare_failed")
                    raise
                if prepared:
                    return entry, source, waited
                creation_reason = "health_replacement"
                continue

            if reserve_creation:
                entry = await self._create_reserved_entry(creation_reason, session_id)
                try:
                    await self._prepare_entry(entry, session_id, source)
                except BaseException:
                    await self._retire_entry(entry, "prepare_failed")
                    raise
                return entry, source, waited

            if waiter is None:  # pragma: no cover - internal state invariant
                raise RuntimeError("pool checkout reached wait state without a waiter")
            timeout = self._config.pool_acquire_timeout
            remaining = timeout - (self._clock() - waiter.enqueued_at)
            if remaining <= 0:
                await self._timeout_waiter(waiter)
            try:
                await wait_for_owned(waiter.event.wait(), remaining)
            except asyncio.TimeoutError:
                await self._timeout_waiter(waiter)
            except asyncio.CancelledError:
                async with self._lock:
                    if self._remove_waiter_locked(waiter):
                        self._stats["waiters_cancelled_total"] += 1
                        self._wake_head_locked()
                raise

    async def _timeout_waiter(self, waiter: PoolWaiter) -> None:
        async with self._lock:
            self._remove_waiter_locked(waiter)
            self._stats["acquire_timeouts_total"] += 1
            self._wake_head_locked()
            stats = self._capacity_snapshot_locked()
        waited = self._clock() - waiter.enqueued_at
        self._telemetry.add("acquire_timeouts", result="capacity_timeout")
        self._telemetry.record("wait_duration", waited, result="capacity_timeout")
        raise ACPPoolAcquireTimeoutError(self._config.pool_acquire_timeout, stats)

    def _enqueue_waiter_locked(
        self,
        waiter: PoolWaiter | None,
        session_id: str | None,
        fresh: bool,
    ) -> PoolWaiter:
        if waiter is None:
            waiter = PoolWaiter(session_id=session_id, fresh=fresh, enqueued_at=self._clock())
            self._waiters.append(waiter)
            self._stats["waiters_total"] += 1
        return waiter

    def _remove_waiter_locked(self, waiter: PoolWaiter | None) -> bool:
        if waiter is None:
            return False
        try:
            self._waiters.remove(waiter)
        except ValueError:
            return False
        return True

    def _dequeue_selected_waiter_locked(self, waiter: PoolWaiter | None) -> float:
        if waiter is None:
            return 0.0
        waited = self._clock() - waiter.enqueued_at
        self._remove_waiter_locked(waiter)
        self._wake_head_locked()
        return waited

    def _wake_head_locked(self) -> None:
        if self._waiters:
            self._waiters[0].event.set()

    def _reserve_session_claim_locked(self, session_id: str | None) -> None:
        if session_id is not None:
            self._session_claims.add(session_id)

    def _claim_session_locked(self, entry: PoolEntry, session_id: str | None) -> None:
        self._reserve_session_claim_locked(session_id)
        entry.claimed_session_id = session_id

    def _release_session_claim_locked(self, entry: PoolEntry) -> None:
        if entry.claimed_session_id is not None:
            self._session_claims.discard(entry.claimed_session_id)
            entry.claimed_session_id = None

    def _select_idle_locked(
        self,
        session_id: str | None,
        *,
        fresh: bool,
    ) -> tuple[PoolEntry | None, str]:
        if fresh:
            return None, "fresh"

        exact_key: int | None = None
        for key in self._idle:
            entry = self._entries.get(key)
            if entry is None or entry.state != "idle":
                continue
            current_session = entry.client.session_id
            if current_session == session_id and (session_id is not None or current_session is None):
                exact_key = key
                break

        source = "exact_session" if session_id is not None else "idle_unbound"
        selected_key = exact_key
        if selected_key is None and session_id is not None and self._config.pool_cross_session_reuse:
            selected_key = next(
                (key for key in self._idle if (entry := self._entries.get(key)) is not None and entry.state == "idle"),
                None,
            )
            source = "cross_session"

        if selected_key is None:
            return None, "new"

        self._idle.remove(selected_key)
        entry = self._entries[selected_key]
        entry.state = "leased"
        entry.released_at = None
        entry.use_count += 1
        return entry, source

    def _select_replacement_locked(self) -> PoolEntry | None:
        candidates = [
            self._entries[key] for key in self._idle if key in self._entries and self._entries[key].state == "idle"
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda entry: entry.released_at or entry.created_at)

    async def _prepare_entry(
        self,
        entry: PoolEntry,
        session_id: str | None,
        source: str,
    ) -> bool:
        client = entry.client
        if source not in {"new", "fresh"}:
            try:
                alive = await wait_for_owned(client.ping(), 5)
            except Exception:
                alive = False
            self._telemetry.add("health", result="success" if alive else "failure", trigger="acquire")
            if not alive:
                self._stats["health_check_failures"] += 1
                await self._retire_entry(entry, "health_check_failed")
                return False

        if session_id is not None and client.session_id != session_id:
            self._stats["resume_attempts"] += 1
            try:
                resumed = await client.resume(session_id)
            except Exception:
                self._stats["resume_failures"] += 1
                self._telemetry.add("resume", result="failure")
                raise

            if not resumed or client.session_id != session_id:
                self._stats["resume_failures"] += 1
                self._telemetry.add("resume", result="failure")
                raise ACPResumeError(session_id)
            self._stats["resume_successes"] += 1
            self._telemetry.add("resume", result="success")

        ensure_session = getattr(client, "ensure_session", None)
        if session_id is None and client.session_id is None and callable(ensure_session):
            await cast(Callable[[], Awaitable[None]], ensure_session)()
        await self._finalize_prepared_entry(entry)
        return True

    async def _finalize_prepared_entry(self, entry: PoolEntry) -> None:
        """Fail closed and claim a provider-assigned session before yielding."""
        async with self._lock:
            if self._closed or self._entries.get(id(entry.client)) is not entry or entry.state != "leased":
                raise ACPPoolClosedError("connection pool closed while preparing a client")

            effective_session_id = entry.client.session_id
            claimed_session_id = entry.claimed_session_id
            if effective_session_id is None or effective_session_id == claimed_session_id:
                return
            if claimed_session_id is not None:
                raise ACPPoolError("ACP client changed its logical session while the transport was leased")
            if effective_session_id in self._session_claims:
                raise ACPPoolError("ACP provider returned a logical session that is already leased")

            self._session_claims.add(effective_session_id)
            entry.claimed_session_id = effective_session_id

    async def _create_reserved_entry(self, reason: str, session_id: str | None) -> PoolEntry:
        started_at = self._clock()
        try:
            client = await self._create_client()
        except BaseException:
            async with self._lock:
                self._creating -= 1
                if session_id is not None:
                    self._session_claims.discard(session_id)
                self._stats["creation_failures_total"] += 1
                if self._creating == 0:
                    self._creating_zero.set()
                self._wake_head_locked()
            self._telemetry.add("created", reason=reason, result="failure")
            self._telemetry.record("connect_duration", self._clock() - started_at, reason=reason, result="failure")
            raise

        close_task: asyncio.Task[None] | None = None
        entry: PoolEntry | None = None
        try:
            async with self._lock:
                self._creating -= 1
                entry = PoolEntry(
                    client=client,
                    created_at=self._clock(),
                    use_count=1,
                    state="closing" if self._closed else "leased",
                    close_reason="pool_closed" if self._closed else None,
                    claimed_session_id=session_id,
                )
                self._entries[id(client)] = entry
                self._stats["created_total"] += 1
                self._created_by_reason[reason] += 1
                self._update_peaks_locked()
                if self._closed:
                    # Register the physical close before publishing creating=0 so
                    # close_all() cannot return between connect() and disconnect().
                    close_task = self._ensure_close_task(entry)
                if self._creating == 0:
                    self._creating_zero.set()
                self._wake_head_locked()
        except BaseException:
            if entry is None:
                cleanup = asyncio.create_task(self._discard_unregistered_client(client, session_id))
            else:
                cleanup = asyncio.create_task(self._retire_entry(entry, "creation_abandoned"))
            await self._await_owned_cleanup(cleanup)
            raise

        elapsed = self._clock() - started_at
        self._telemetry.record("connect_duration", elapsed, reason=reason, result="success")
        self._telemetry.add("created", reason=reason, result="success")
        if close_task is not None:
            await close_task
            raise ACPPoolClosedError("connection pool closed while creating a client")
        return entry

    async def _discard_unregistered_client(self, client: ACPClient, session_id: str | None) -> None:
        """Close a connected client before returning its reserved capacity."""
        await self._safe_disconnect(client)
        async with self._lock:
            self._creating -= 1
            if session_id is not None:
                self._session_claims.discard(session_id)
            self._stats["creation_failures_total"] += 1
            if self._creating == 0:
                self._creating_zero.set()
            self._wake_head_locked()

    async def _release_entry(self, entry: PoolEntry) -> None:
        """Finish a lease transition before propagating caller cancellation."""
        cleanup = asyncio.create_task(self._finish_release_entry(entry))
        await self._await_owned_cleanup(cleanup)

    async def _finish_release_entry(self, entry: PoolEntry) -> None:
        entry_to_close: PoolEntry | None = None
        lru_to_close: PoolEntry | None = None
        contract_error: ACPPoolError | None = None
        async with self._lock:
            # Idle age starts at the actual leased -> idle state transition.
            now = self._clock()
            current = self._entries.get(id(entry.client))
            if current is not entry or entry.state != "leased":
                return

            reason: str | None = None
            if self._closed:
                reason = "pool_closed"
            elif not self._client_pool_reusable(entry.client):
                reason = "client_invalidated"
            elif entry.claimed_session_id is not None and entry.client.session_id != entry.claimed_session_id:
                reason = "session_changed"
                contract_error = ACPPoolError("ACP client changed its logical session while the transport was leased")
            elif not self._client_connected(entry.client):
                reason = "disconnected"
            elif now - entry.created_at >= self._config.pool_max_age:
                reason = "max_age"
            elif entry.use_count >= self._config.pool_max_uses:
                reason = "max_uses"

            if reason is not None:
                if self._mark_closing_locked(entry, reason):
                    entry_to_close = entry
            else:
                self._release_session_claim_locked(entry)
                entry.state = "idle"
                entry.released_at = now
                self._idle.append(id(entry.client))
                if self.size > self._config.pool_size:
                    lru = self._select_replacement_locked()
                    if lru is not None and self._mark_closing_locked(lru, "pool_full_lru"):
                        lru_to_close = lru
                self._wake_head_locked()

        if entry_to_close is not None:
            await self._await_close(entry_to_close)
        if lru_to_close is not None:
            self._ensure_close_task(lru_to_close)
        if contract_error is not None:
            raise contract_error

    def _retire_idle_locked(self, now: float) -> list[PoolEntry]:
        closing: list[PoolEntry] = []
        for key in list(self._idle):
            entry = self._entries.get(key)
            if entry is None or entry.state != "idle":
                self._remove_idle_key_locked(key)
                continue
            reason = self._retirement_reason(entry, now)
            if reason and self._mark_closing_locked(entry, reason):
                closing.append(entry)
        return closing

    def _retirement_reason(self, entry: PoolEntry, now: float) -> str | None:
        if not self._client_pool_reusable(entry.client):
            return "client_invalidated"
        if not self._client_connected(entry.client):
            return "disconnected"
        if entry.released_at is not None and now - entry.released_at >= self._config.pool_idle_timeout:
            return "idle_timeout"
        if now - entry.created_at >= self._config.pool_max_age:
            return "max_age"
        if entry.use_count >= self._config.pool_max_uses:
            return "max_uses"
        return None

    @staticmethod
    def _client_connected(client: ACPClient) -> bool:
        try:
            return client.is_connected
        except Exception:
            return False

    @staticmethod
    def _client_pool_reusable(client: ACPClient) -> bool:
        try:
            return bool(getattr(client, "is_pool_reusable", True))
        except Exception:
            return False

    async def _retire_entry(self, entry: PoolEntry, reason: str) -> None:
        """Claim retirement before propagating caller cancellation."""
        cleanup = asyncio.create_task(self._finish_retire_entry(entry, reason))
        await self._await_owned_cleanup(cleanup)

    async def _finish_retire_entry(self, entry: PoolEntry, reason: str) -> None:
        async with self._lock:
            if id(entry.client) not in self._entries:
                return
            self._mark_closing_locked(entry, reason)
        await self._await_close(entry)

    def _mark_closing_locked(self, entry: PoolEntry, reason: str) -> bool:
        if entry.state == "closing":
            return False
        self._remove_idle_key_locked(id(entry.client))
        entry.state = "closing"
        entry.close_reason = reason
        return True

    def _remove_idle_key_locked(self, key: int) -> None:
        with suppress(ValueError):
            self._idle.remove(key)

    def _ensure_close_task(self, entry: PoolEntry) -> asyncio.Task[None]:
        if entry.close_task is None or entry.close_task.done():
            if entry.close_task is not None:
                self._stats["cleanup_retries_total"] += 1
                self._telemetry.add("cleanup_retries", source="tracked")
            task = asyncio.create_task(self._finish_close(entry))
            entry.close_task = task
            self._background_tasks.add(task)

            def _close_done(done: asyncio.Task[None]) -> None:
                self._background_tasks.discard(done)
                if not done.cancelled():
                    with suppress(Exception):
                        done.exception()

            task.add_done_callback(_close_done)
        return entry.close_task

    def _ensure_untracked_close_task(self, client: ACPClient) -> asyncio.Task[None]:
        """Retry one failed startup cleanup without releasing its capacity."""
        key = id(client)
        existing = self._untracked_close_tasks.get(key)
        if existing is not None and not existing.done():
            return existing

        self._stats["cleanup_retries_total"] += 1
        self._telemetry.add("cleanup_retries", source="failed_startup")
        task = asyncio.create_task(self._finish_untracked_close(client))
        self._untracked_close_tasks[key] = task
        self._background_tasks.add(task)

        def _close_done(done: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done)
            if self._untracked_close_tasks.get(key) is done:
                self._untracked_close_tasks.pop(key, None)
            if not done.cancelled():
                with suppress(Exception):
                    done.exception()

        task.add_done_callback(_close_done)
        return task

    async def _finish_untracked_close(self, client: ACPClient) -> None:
        if not await self._safe_disconnect(client):
            raise ACPPoolError("ACP client disconnect retry failed; transport remains reserved")
        async with self._lock:
            self._wake_head_locked()

    async def _await_close(self, entry: PoolEntry) -> None:
        await self._await_owned_cleanup(self._ensure_close_task(entry))

    async def _finish_close(self, entry: PoolEntry) -> None:
        if not await self._safe_disconnect(entry.client):
            raise ACPPoolError("ACP client disconnect failed; transport remains reserved")
        async with self._lock:
            key = id(entry.client)
            if self._entries.get(key) is entry:
                self._entries.pop(key, None)
                self._release_session_claim_locked(entry)
                reason = entry.close_reason or "unknown"
                self._evictions[reason] += 1
                self._telemetry.add("evictions", reason=reason)
            self._wake_head_locked()

    async def _safe_disconnect(self, client: ACPClient) -> bool:
        try:
            await client.disconnect()
        except Exception as exc:
            self._unreaped_clients[id(client)] = client
            self._stats["disconnect_failures_total"] += 1
            self._telemetry.add("disconnect_failures", result="failure")
            logger.warning("ACP client disconnect failed: %s", exception_summary(exc))
            return False
        self._unreaped_clients.pop(id(client), None)
        return True

    def start_keepalive(self) -> None:
        """Start one background keepalive loop when an event loop is running."""
        interval = self._config.pool_keepalive_interval
        if interval <= 0 or self._closed:
            return
        if self._keepalive_task is not None and not self._keepalive_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        interval = self._config.pool_keepalive_interval
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    await self._run_keepalive()
                except Exception as exc:
                    self._stats["keepalive_loop_errors"] += 1
                    logger.error(
                        "Unexpected error in ACP pool keepalive cycle: %s",
                        exception_summary(exc),
                    )
        except asyncio.CancelledError:
            raise

    async def _run_keepalive(self) -> None:
        entries_to_check: list[PoolEntry] = []
        entries_to_close: list[PoolEntry] = []
        async with self._lock:
            if self._closed:
                return
            entries_to_close.extend(self._retire_idle_locked(self._clock()))
            for key in list(self._idle):
                entry = self._entries.get(key)
                if entry is None or entry.state != "idle":
                    continue
                self._remove_idle_key_locked(key)
                entry.state = "checking"
                entries_to_check.append(entry)

        for entry in entries_to_close:
            self._ensure_close_task(entry)

        semaphore = asyncio.Semaphore(4)

        async def _check(entry: PoolEntry) -> tuple[PoolEntry, bool]:
            async with semaphore:
                try:
                    return entry, await wait_for_owned(entry.client.ping(), 5)
                except Exception:
                    return entry, False

        results = await asyncio.gather(*(_check(entry) for entry in entries_to_check))
        failed: list[PoolEntry] = []
        async with self._lock:
            for entry, alive in results:
                if self._entries.get(id(entry.client)) is not entry or entry.state != "checking":
                    continue
                reason = self._retirement_reason(entry, self._clock())
                if not alive:
                    reason = "health_check_failed"
                    self._stats["health_check_failures"] += 1
                if self._closed:
                    reason = "pool_closed"
                if reason:
                    if self._mark_closing_locked(entry, reason):
                        failed.append(entry)
                else:
                    entry.state = "idle"
                    self._idle.append(id(entry.client))
                self._telemetry.add("health", result="success" if alive else "failure", trigger="keepalive")
            self._stats["keepalive_runs"] += 1
            if failed:
                self._stats["keepalive_failures"] += len(failed)
            self._wake_head_locked()

        self._telemetry.add("keepalive", result="failure" if failed else "success")
        for entry in failed:
            self._ensure_close_task(entry)

    async def close_all(self) -> None:
        """Close the pool and finish physical cleanup before propagating cancellation."""
        async with self._lock:
            self._closed = True
            if self._close_all_task is None or (self._close_all_task.done() and self._total_reserved_locked() > 0):
                keepalive = self._keepalive_task
                self._keepalive_task = None
                for waiter in self._waiters:
                    waiter.event.set()
                entries = list(self._entries.values())
                for entry in entries:
                    self._mark_closing_locked(entry, "pool_closed")
                self._close_all_task = asyncio.create_task(self._finish_close_all(keepalive, entries))
            close_task = self._close_all_task

        await self._await_owned_cleanup(close_task)

    async def _finish_close_all(
        self,
        keepalive: asyncio.Task[None] | None,
        entries: list[PoolEntry],
    ) -> None:
        if keepalive is not None:
            keepalive.cancel()
            try:
                await keepalive
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                # A failed health task must never prevent transport cleanup.
                logger.error("ACP pool keepalive task failed during close: %s", exception_summary(exc))

        tasks = [self._ensure_close_task(entry) for entry in entries]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # A connection that was already creating when close began registers its
        # close task before publishing creating=0.
        await self._creating_zero.wait()
        background = list(self._background_tasks)
        if background:
            await asyncio.gather(*background, return_exceptions=True)

        async with self._lock:
            retry_entries = [
                entry
                for entry in self._entries.values()
                if entry.state == "closing" and (entry.close_task is None or entry.close_task.done())
            ]
            tracked_ids = set(self._entries)
            untracked_clients = [client for key, client in self._unreaped_clients.items() if key not in tracked_ids]

        retry_tasks = [self._ensure_close_task(entry) for entry in retry_entries]
        if retry_tasks:
            await asyncio.gather(*retry_tasks, return_exceptions=True)
        if untracked_clients:
            await asyncio.gather(*(self._safe_disconnect(client) for client in untracked_clients))

        async with self._lock:
            if self._total_reserved_locked() > 0:
                raise ACPPoolError("connection pool closed with unreaped client transports")

    @staticmethod
    async def _await_owned_cleanup(task: asyncio.Task[None]) -> None:
        """Delay caller cancellation until an owned cleanup task has finished."""
        cancelled = False
        cleanup_error: BaseException | None = None
        while True:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if task.cancelled():
                    cleanup_error = exc
                    break
                cancelled = True
                continue
            except BaseException as exc:
                cleanup_error = exc
                break
            else:
                break

        if cancelled:
            if cleanup_error is not None:
                logger.error(
                    "Pool cleanup failed while its caller was cancelled: %s",
                    exception_summary(cleanup_error),
                )
            raise asyncio.CancelledError
        if cleanup_error is not None:
            raise cleanup_error

    async def __aenter__(self) -> ACPConnectionPool:
        if self._closed:
            raise ACPPoolClosedError("connection pool is closed")
        self.start_keepalive()
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.close_all()

    def get_stats(self) -> dict[str, Any]:
        """Return a non-blocking snapshot for this pool instance."""
        now = self._clock()
        states = Counter(entry.state for entry in self._entries.values())
        idle_ages = [
            now - entry.released_at
            for entry in self._entries.values()
            if entry.state == "idle" and entry.released_at is not None
        ]
        connection_ages = [now - entry.created_at for entry in self._entries.values()]
        untracked_unreaped = len(set(self._unreaped_clients) - set(self._entries))
        total_open = len(self._entries) + untracked_unreaped
        total_reserved = total_open + self._creating
        snapshot: dict[str, Any] = {
            # Backward-compatible keys.
            "pool_size": states["idle"],
            "max_pool_size": self._config.pool_size,
            "connected": sum(
                1 for entry in self._entries.values() if entry.state == "idle" and self._client_connected(entry.client)
            ),
            "provider": self._config.provider,
            # Current state.
            "closed": self._closed,
            "idle": states["idle"],
            "active": states["leased"],
            "checking": states["checking"],
            "closing": states["closing"] + untracked_unreaped,
            "unreaped": len(self._unreaped_clients),
            "creating": self._creating,
            "total_open": total_open,
            "total_reserved": total_reserved,
            "max_connections": self._config.pool_max_connections,
            "available_capacity": max(0, self._config.pool_max_connections - total_reserved),
            "waiting": len(self._waiters),
            "claimed_sessions": len(self._session_claims),
            "keepalive_running": self._keepalive_task is not None and not self._keepalive_task.done(),
            "oldest_idle_seconds": max(idle_ages, default=0.0),
            "oldest_connection_age_seconds": max(connection_ages, default=0.0),
            # Cumulative decisions.
            "acquisitions_total": self._stats["acquisitions_total"],
            "created_total": self._stats["created_total"],
            "created_by_reason": dict(self._created_by_reason),
            "creation_failures_total": self._stats["creation_failures_total"],
            "disconnect_failures_total": self._stats["disconnect_failures_total"],
            "cleanup_retries_total": self._stats["cleanup_retries_total"],
            "reused_total": self._stats["reused_total"],
            "exact_session_hits": self._stats["exact_session_hits"],
            "cross_session_reuses": self._stats["cross_session_reuses"],
            "fresh_total": self._stats["fresh_total"],
            "resume_attempts": self._stats["resume_attempts"],
            "resume_successes": self._stats["resume_successes"],
            "resume_failures": self._stats["resume_failures"],
            "health_check_failures": self._stats["health_check_failures"],
            "keepalive_runs": self._stats["keepalive_runs"],
            "keepalive_failures": self._stats["keepalive_failures"],
            "keepalive_loop_errors": self._stats["keepalive_loop_errors"],
            "waiters_total": self._stats["waiters_total"],
            "acquire_timeouts_total": self._stats["acquire_timeouts_total"],
            "waiters_cancelled_total": self._stats["waiters_cancelled_total"],
            "capacity_rebalances_total": self._stats["capacity_rebalances_total"],
            "peak_total_open": self._peak_total_open,
            "peak_total_reserved": self._peak_total_reserved,
            "evictions": dict(self._evictions),
            "policy": {
                "pool_max_connections": self._config.pool_max_connections,
                "pool_acquire_timeout": self._config.pool_acquire_timeout,
                "pool_idle_timeout": self._config.pool_idle_timeout,
                "pool_keepalive_interval": self._config.pool_keepalive_interval,
                "pool_max_age": self._config.pool_max_age,
                "pool_max_uses": self._config.pool_max_uses,
                "pool_cross_session_reuse": self._config.pool_cross_session_reuse,
                "startup_attempts": self._startup_attempts,
            },
        }
        return snapshot

    def _capacity_snapshot_locked(self) -> dict[str, int]:
        states = Counter(entry.state for entry in self._entries.values())
        untracked_unreaped = len(set(self._unreaped_clients) - set(self._entries))
        total_reserved = self._total_reserved_locked()
        return {
            "idle": states["idle"],
            "active": states["leased"],
            "checking": states["checking"],
            "closing": states["closing"] + untracked_unreaped,
            "unreaped": len(self._unreaped_clients),
            "creating": self._creating,
            "total_reserved": total_reserved,
            "max_connections": self._config.pool_max_connections,
            "waiting": len(self._waiters),
            "claimed_sessions": len(self._session_claims),
        }

    def _total_reserved_locked(self) -> int:
        client_ids = set(self._entries) | set(self._unreaped_clients)
        return len(client_ids) + self._creating

    def _update_peaks_locked(self) -> None:
        total_open = len(set(self._entries) | set(self._unreaped_clients))
        self._peak_total_open = max(self._peak_total_open, total_open)
        self._peak_total_reserved = max(self._peak_total_reserved, self._total_reserved_locked())

    async def _create_client(self) -> ACPClient:
        """Create a client with bounded startup and cleanup on every failure."""

        async def _do_create() -> ACPClient:
            # A client may expose its config publicly.  Give each transport an
            # independent snapshot so it cannot mutate this pool's policy or a
            # sibling transport's structured configuration.
            client = self._client_class(replace(self._config))
            connect_task = asyncio.create_task(client.connect())
            try:
                done, _pending = await asyncio.wait({connect_task}, timeout=self._config.timeout_connect)
                if connect_task not in done:
                    raise asyncio.TimeoutError
                await connect_task
                if not self._client_connected(client):
                    raise ConnectionError("ACP client connect() returned without a live transport")
                return client
            except BaseException as startup_error:
                cleanup_cause = startup_error
                if not connect_task.done():
                    connect_task.cancel()
                with suppress(BaseException):
                    await self._await_owned_cleanup(connect_task)

                async def _clean_failed_startup() -> None:
                    if not await self._safe_disconnect(client):
                        raise ACPPoolError(
                            "ACP client startup failed and its transport could not be reaped"
                        ) from cleanup_cause

                disconnect_task = asyncio.create_task(_clean_failed_startup())
                await self._await_owned_cleanup(disconnect_task)
                raise

        return await retry_async(
            _do_create,
            max_retries=self._startup_attempts,
            base_delay=self._config.retry_base_delay,
        )
