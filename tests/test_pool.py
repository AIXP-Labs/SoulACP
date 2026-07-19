"""Deterministic tests for the bounded, session-safe pool state machine."""

from __future__ import annotations

import asyncio

import pytest

from soulacp.config import ACPConfig
from soulacp.errors import ACPPoolAcquireTimeoutError, ACPPoolClosedError, ACPPoolError, ACPResumeError
from soulacp.pool import ACPConnectionPool


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeClient:
    instances: list[FakeClient] = []
    connect_gate: asyncio.Event | None = None
    connect_error: BaseException | None = None
    disconnect_gate: asyncio.Event | None = None
    resume_error: BaseException | None = None
    resume_succeeds = True
    ping_succeeds = True
    assign_session = True

    def __init__(self, config: ACPConfig) -> None:
        self.config = config
        self.index = len(type(self).instances) + 1
        self.session_id: str | None = None
        self.connected = False
        self.pool_reusable = True
        self.disconnect_calls = 0
        self.resume_calls: list[str] = []
        type(self).instances.append(self)

    @property
    def is_connected(self) -> bool:
        return self.connected

    @property
    def is_idle_timeout(self) -> bool:
        return False

    @property
    def is_pool_reusable(self) -> bool:
        return self.pool_reusable

    async def connect(self) -> None:
        if type(self).connect_gate is not None:
            await type(self).connect_gate.wait()
        if type(self).connect_error is not None:
            raise type(self).connect_error
        self.connected = True
        if type(self).assign_session:
            self.session_id = f"session-{self.index}"

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if type(self).disconnect_gate is not None:
            await type(self).disconnect_gate.wait()
        self.connected = False

    async def ping(self) -> bool:
        return self.connected and type(self).ping_succeeds

    async def resume(self, session_id: str) -> bool:
        self.resume_calls.append(session_id)
        if type(self).resume_error is not None:
            raise type(self).resume_error
        if not type(self).resume_succeeds:
            return False
        self.session_id = session_id
        return True

    async def query(self, prompt: str) -> str:
        return prompt

    async def query_stream(self, prompt: str):
        yield prompt


@pytest.fixture(autouse=True)
def reset_fake_client():
    FakeClient.instances = []
    FakeClient.connect_gate = None
    FakeClient.connect_error = None
    FakeClient.disconnect_gate = None
    FakeClient.resume_error = None
    FakeClient.resume_succeeds = True
    FakeClient.ping_succeeds = True
    FakeClient.assign_session = True


def make_config(**overrides) -> ACPConfig:
    values = {
        "pool_size": 2,
        "pool_max_connections": 2,
        "pool_acquire_timeout": 0.05,
        "pool_idle_timeout": 600,
        "pool_keepalive_interval": 0,
        "pool_max_age": 7200,
        "pool_max_uses": 50,
        "timeout_connect": 0.5,
        "max_retries": 1,
        "retry_base_delay": 0.0,
    }
    values.update(overrides)
    return ACPConfig(**values)


def test_pool_rejects_invalid_startup_attempt_budget():
    with pytest.raises(ValueError, match="startup_attempts"):
        ACPConnectionPool(make_config(), FakeClient, startup_attempts=0)


@pytest.mark.asyncio
async def test_pool_snapshots_policy_and_detaches_each_client_config():
    config = make_config(pool_size=1, pool_max_connections=1, extra_args=["--original"])
    pool = ACPConnectionPool(config, FakeClient)

    config.pool_size = 99
    config.pool_max_connections = 99
    config.extra_args.append("--mutated")

    async with pool.acquire() as (client, _session_id):
        assert client.config is not config
        assert client.config is not pool._config
        assert client.config.pool_size == 1
        assert client.config.pool_max_connections == 1
        assert client.config.extra_args == ["--original"]
        client.config.pool_max_connections = 50
        client.config.extra_args.append("--client-only")

    stats = pool.get_stats()
    assert stats["max_pool_size"] == 1
    assert stats["max_connections"] == 1
    assert stats["policy"]["pool_max_connections"] == 1
    assert pool._config.extra_args == ["--original"]
    await pool.close_all()


def test_pool_revalidates_a_mutated_config_snapshot():
    config = make_config()
    config.pool_max_connections = 0

    with pytest.raises(ValueError, match="pool_max_connections"):
        ACPConnectionPool(config, FakeClient)


@pytest.mark.asyncio
async def test_pool_rejects_invalid_session_unicode_before_reserving_capacity():
    pool = ACPConnectionPool(make_config(), FakeClient)

    with pytest.raises(ValueError, match="valid Unicode"):
        async with pool.acquire(session_id="\ud800"):
            pass

    assert pool.get_stats()["total_reserved"] == 0
    assert FakeClient.instances == []


@pytest.mark.asyncio
async def test_connect_must_publish_a_live_transport():
    class DisconnectedClient(FakeClient):
        async def connect(self) -> None:
            self.session_id = "not-live"

    pool = ACPConnectionPool(make_config(), DisconnectedClient, startup_attempts=1)

    with pytest.raises(ConnectionError, match="live transport"):
        async with pool.acquire():
            pass

    assert DisconnectedClient.instances[-1].disconnect_calls == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_failed_keepalive_task_cannot_block_pool_close():
    pool = ACPConnectionPool(make_config(), FakeClient)

    async def fail() -> None:
        raise RuntimeError("keepalive failed")

    pool._keepalive_task = asyncio.create_task(fail())
    await asyncio.sleep(0)

    await pool.close_all()
    assert pool.get_stats()["closed"] is True


@pytest.mark.asyncio
async def test_disconnect_failure_is_visible_in_pool_stats():
    class FailingDisconnectClient(FakeClient):
        fail_disconnect = True

        async def disconnect(self) -> None:
            self.disconnect_calls += 1
            if type(self).fail_disconnect:
                raise RuntimeError("disconnect failed")
            self.connected = False

    pool = ACPConnectionPool(make_config(), FailingDisconnectClient)
    async with pool.acquire():
        pass

    with pytest.raises(ACPPoolError, match="unreaped"):
        await pool.close_all()

    stats = pool.get_stats()
    assert stats["disconnect_failures_total"] >= 1
    assert stats["unreaped"] == 1
    assert stats["total_reserved"] == 1
    assert stats["available_capacity"] == 1

    FailingDisconnectClient.fail_disconnect = False
    await pool.close_all()
    assert pool.get_stats()["total_reserved"] == 0


@pytest.mark.asyncio
async def test_disconnect_failure_does_not_mask_lease_error():
    class FailingLeaseDisconnectClient(FakeClient):
        fail_disconnect = True

        async def disconnect(self) -> None:
            self.disconnect_calls += 1
            if type(self).fail_disconnect:
                raise RuntimeError("disconnect failed")
            self.connected = False

    pool = ACPConnectionPool(make_config(), FailingLeaseDisconnectClient)
    lease_error = ValueError("leased operation failed")

    with pytest.raises(ValueError) as exc_info:
        async with pool.acquire():
            raise lease_error

    assert exc_info.value is lease_error
    assert pool.get_stats()["unreaped"] == 1
    assert pool.get_stats()["total_reserved"] == 1

    FailingLeaseDisconnectClient.fail_disconnect = False
    await pool.close_all()


@pytest.mark.asyncio
async def test_next_acquire_retries_failed_tracked_disconnect():
    class RetryDisconnectClient(FakeClient):
        fail_disconnect = True

        async def disconnect(self) -> None:
            self.disconnect_calls += 1
            if type(self).fail_disconnect:
                raise RuntimeError("disconnect failed")
            self.connected = False

    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1),
        RetryDisconnectClient,
    )

    with pytest.raises(ValueError, match="lease failed"):
        async with pool.acquire():
            raise ValueError("lease failed")

    assert pool.get_stats()["total_reserved"] == 1
    RetryDisconnectClient.fail_disconnect = False

    async with pool.acquire() as (client, _session_id):
        assert client is not RetryDisconnectClient.instances[0]

    stats = pool.get_stats()
    assert stats["cleanup_retries_total"] >= 1
    assert stats["unreaped"] == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_failed_startup_cleanup_stays_reserved_and_stops_startup_retries():
    class UnreapedStartupClient(FakeClient):
        fail_disconnect = True

        async def connect(self) -> None:
            self.connected = True
            raise ConnectionError("startup failed after spawn")

        async def disconnect(self) -> None:
            self.disconnect_calls += 1
            if type(self).fail_disconnect:
                raise RuntimeError("cannot reap")
            self.connected = False

    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, max_retries=3),
        UnreapedStartupClient,
        startup_attempts=3,
    )

    with pytest.raises(ACPPoolError, match="could not be reaped"):
        async with pool.acquire():
            pytest.fail("failed startup must not yield")

    stats = pool.get_stats()
    assert len(UnreapedStartupClient.instances) == 1
    assert stats["creating"] == 0
    assert stats["unreaped"] == 1
    assert stats["total_reserved"] == 1
    assert stats["creation_failures_total"] == 1

    UnreapedStartupClient.fail_disconnect = False
    await pool.close_all()
    assert pool.get_stats()["total_reserved"] == 0


@pytest.mark.asyncio
async def test_next_acquire_retries_untracked_failed_startup_cleanup():
    class RetryStartupClient(FakeClient):
        fail_connect = True
        fail_disconnect = True

        async def connect(self) -> None:
            self.connected = True
            if type(self).fail_connect:
                raise ConnectionError("startup failed after spawn")
            self.session_id = f"session-{self.index}"

        async def disconnect(self) -> None:
            self.disconnect_calls += 1
            if type(self).fail_disconnect:
                raise RuntimeError("cannot reap")
            self.connected = False

    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1),
        RetryStartupClient,
        startup_attempts=1,
    )

    with pytest.raises(ACPPoolError, match="could not be reaped"):
        async with pool.acquire():
            pytest.fail("failed startup must not yield")

    RetryStartupClient.fail_connect = False
    RetryStartupClient.fail_disconnect = False
    async with pool.acquire() as (client, session_id):
        assert client is RetryStartupClient.instances[1]
        assert session_id == "session-2"

    stats = pool.get_stats()
    assert stats["cleanup_retries_total"] >= 1
    assert stats["unreaped"] == 0
    await pool.close_all()


async def wait_until(predicate, timeout: float = 1.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition was not reached before timeout")
        await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_anonymous_acquire_never_reuses_bound_session():
    pool = ACPConnectionPool(make_config(), FakeClient)

    async with pool.acquire() as (alice, alice_sid):
        assert alice_sid == "session-1"
    async with pool.acquire() as (bob, bob_sid):
        assert bob_sid == "session-2"
        assert bob is not alice

    stats = pool.get_stats()
    assert stats["created_total"] == 2
    assert stats["cross_session_reuses"] == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_exact_session_revisit_reuses_same_client():
    pool = ACPConnectionPool(make_config(), FakeClient)

    async with pool.acquire() as (first, sid):
        pass
    async with pool.acquire(sid) as (second, resumed_sid):
        assert second is first
        assert resumed_sid == sid

    stats = pool.get_stats()
    assert stats["exact_session_hits"] == 1
    assert stats["reused_total"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_exact_session_mutation_during_lease_retires_transport():
    pool = ACPConnectionPool(make_config(), FakeClient)

    async with pool.acquire() as (_client, sid):
        pass

    with pytest.raises(ACPPoolError, match="changed its logical session"):
        async with pool.acquire(sid) as (client, _resumed_sid):
            client.session_id = "different-session"

    assert pool.get_stats()["evictions"]["session_changed"] == 1
    assert pool.get_stats()["total_open"] == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_intentionally_invalidated_client_retires_without_contract_error():
    pool = ACPConnectionPool(make_config(), FakeClient)

    async with pool.acquire() as (client, _sid):
        client.pool_reusable = False
        client.session_id = None

    assert pool.get_stats()["evictions"]["client_invalidated"] == 1
    assert pool.get_stats()["total_open"] == 0
    assert client.disconnect_calls == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_resume_failure_never_yields_wrong_session():
    pool = ACPConnectionPool(make_config(), FakeClient)
    async with pool.acquire():
        pass

    FakeClient.resume_succeeds = False
    with pytest.raises(ACPResumeError):
        async with pool.acquire("missing-session"):
            pytest.fail("resume failure must not yield a client")

    assert FakeClient.instances[1].is_connected is False
    assert pool.get_stats()["resume_failures"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_resume_transport_failure_is_preserved_and_retires_client():
    pool = ACPConnectionPool(make_config(), FakeClient)
    async with pool.acquire():
        pass

    resume_error = ConnectionError("transport lost during resume")
    FakeClient.resume_error = resume_error
    with pytest.raises(ConnectionError, match="transport lost during resume") as exc_info:
        async with pool.acquire("stored-session"):
            pytest.fail("resume transport failure must not yield a client")

    assert exc_info.value is resume_error
    assert FakeClient.instances[1].disconnect_calls == 1
    assert FakeClient.instances[1].is_connected is False
    stats = pool.get_stats()
    assert stats["resume_failures"] == 1
    assert stats["total_open"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_explicit_cross_session_reuse_requires_successful_resume():
    pool = ACPConnectionPool(
        make_config(
            pool_size=1,
            pool_max_connections=1,
            pool_cross_session_reuse=True,
        ),
        FakeClient,
    )
    async with pool.acquire() as (first, _sid):
        pass

    async with pool.acquire("target-session") as (second, sid):
        assert second is first
        assert sid == "target-session"
        assert second.resume_calls == ["target-session"]

    assert pool.get_stats()["cross_session_reuses"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_fresh_skips_idle_but_obeys_capacity():
    pool = ACPConnectionPool(make_config(), FakeClient)
    async with pool.acquire() as (first, _sid):
        pass
    async with pool.acquire(fresh=True) as (second, _sid):
        assert second is not first

    assert pool.get_stats()["fresh_total"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_hard_capacity_times_out_without_creating_extra_client():
    pool = ACPConnectionPool(make_config(pool_acquire_timeout=0.02), FakeClient)
    first_cm = pool.acquire()
    second_cm = pool.acquire()
    await first_cm.__aenter__()
    await second_cm.__aenter__()

    try:
        with pytest.raises(ACPPoolAcquireTimeoutError) as exc_info:
            async with pool.acquire(fresh=True):
                pytest.fail("capacity timeout must not yield")
        assert exc_info.value.stats["total_reserved"] == 2
        assert len(FakeClient.instances) == 2
        assert pool.get_stats()["peak_total_reserved"] == 2
    finally:
        await first_cm.__aexit__(None, None, None)
        await second_cm.__aexit__(None, None, None)
        await pool.close_all()


@pytest.mark.asyncio
async def test_waiters_are_served_fifo():
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, pool_acquire_timeout=1),
        FakeClient,
    )
    holder = pool.acquire()
    _client, sid = await holder.__aenter__()
    order: list[str] = []

    async def use(label: str) -> None:
        async with pool.acquire(sid):
            order.append(label)
            await asyncio.sleep(0)

    first = asyncio.create_task(use("first"))
    await wait_until(lambda: pool.get_stats()["waiting"] == 1)
    second = asyncio.create_task(use("second"))
    await wait_until(lambda: pool.get_stats()["waiting"] == 2)
    await holder.__aexit__(None, None, None)
    await asyncio.gather(first, second)

    assert order == ["first", "second"]
    await pool.close_all()


@pytest.mark.asyncio
async def test_same_session_leases_are_serialized_without_extra_process():
    pool = ACPConnectionPool(make_config(pool_max_connections=2, pool_acquire_timeout=1), FakeClient)
    async with pool.acquire() as (first, sid):
        pass

    holder = pool.acquire(sid)
    held_client, _ = await holder.__aenter__()

    async def revisit() -> FakeClient:
        async with pool.acquire(sid) as (client, _session_id):
            return client

    waiting = asyncio.create_task(revisit())
    await wait_until(lambda: pool.get_stats()["waiting"] == 1)
    assert pool.get_stats()["claimed_sessions"] == 1
    assert len(FakeClient.instances) == 1

    await holder.__aexit__(None, None, None)
    revisited = await waiting
    assert revisited is held_client is first
    assert len(FakeClient.instances) == 1
    assert pool.get_stats()["claimed_sessions"] == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_anonymous_lease_claims_provider_assigned_session_before_yield():
    pool = ACPConnectionPool(make_config(pool_max_connections=2, pool_acquire_timeout=1), FakeClient)
    holder = pool.acquire()
    first, sid = await holder.__aenter__()

    assert sid == "session-1"
    assert pool.get_stats()["claimed_sessions"] == 1

    async def revisit() -> FakeClient:
        async with pool.acquire(sid) as (client, _session_id):
            return client

    waiting = asyncio.create_task(revisit())
    await wait_until(lambda: pool.get_stats()["waiting"] == 1)
    assert len(FakeClient.instances) == 1

    await holder.__aexit__(None, None, None)
    assert await waiting is first
    await pool.close_all()


@pytest.mark.asyncio
async def test_replacement_waits_for_physical_disconnect_before_create():
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, pool_acquire_timeout=1),
        FakeClient,
    )
    async with pool.acquire():
        pass

    disconnect_gate = asyncio.Event()
    FakeClient.disconnect_gate = disconnect_gate

    async def acquire_fresh() -> FakeClient:
        async with pool.acquire(fresh=True) as (client, _sid):
            return client

    task = asyncio.create_task(acquire_fresh())
    await wait_until(lambda: pool.get_stats()["closing"] == 1)
    assert len(FakeClient.instances) == 1
    assert pool.get_stats()["total_reserved"] == 1

    disconnect_gate.set()
    fresh = await task
    assert fresh is FakeClient.instances[1]
    assert pool.get_stats()["peak_total_reserved"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_cancelling_lease_exit_does_not_cancel_pool_owned_disconnect():
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, pool_max_uses=1),
        FakeClient,
    )
    disconnect_gate = asyncio.Event()
    FakeClient.disconnect_gate = disconnect_gate
    lease = pool.acquire()
    client, _sid = await lease.__aenter__()

    exit_task = asyncio.create_task(lease.__aexit__(None, None, None))
    await wait_until(lambda: pool.get_stats()["closing"] == 1)
    exit_task.cancel()
    await asyncio.sleep(0)
    exit_task.cancel()
    await asyncio.sleep(0)

    assert client.is_connected
    assert not exit_task.done()
    disconnect_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await exit_task
    assert client.is_connected is False
    await pool.close_all()


@pytest.mark.asyncio
async def test_repeated_cancellation_while_retirement_waits_for_pool_lock_cannot_leak_lease():
    pool = ACPConnectionPool(make_config(pool_size=1, pool_max_connections=1), FakeClient)
    lease_started = asyncio.Event()

    async def hold_lease() -> None:
        async with pool.acquire() as (_client, _sid):
            lease_started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold_lease())
    await lease_started.wait()
    await pool._lock.acquire()
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        pool._lock.release()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.get_stats()["active"] == 0
    assert pool.get_stats()["total_reserved"] == 0
    await pool.close_all()


@pytest.mark.asyncio
async def test_repeated_cancellation_while_release_waits_for_pool_lock_still_returns_lease():
    pool = ACPConnectionPool(make_config(pool_size=1, pool_max_connections=1), FakeClient)
    lease = pool.acquire()
    client, _sid = await lease.__aenter__()
    await pool._lock.acquire()
    try:
        exit_task = asyncio.create_task(lease.__aexit__(None, None, None))
        await asyncio.sleep(0)
        exit_task.cancel()
        await asyncio.sleep(0)
        exit_task.cancel()
        await asyncio.sleep(0)
        assert not exit_task.done()
    finally:
        pool._lock.release()

    with pytest.raises(asyncio.CancelledError):
        await exit_task
    assert pool.get_stats()["active"] == 0
    assert pool.get_stats()["idle"] == 1
    assert client.is_connected
    await pool.close_all()


@pytest.mark.asyncio
async def test_idle_timeout_uses_released_at_and_monotonic_clock():
    clock = FakeClock()
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1),
        FakeClient,
        clock=clock,
    )
    async with pool.acquire() as (first, sid):
        clock.advance(1000)
        assert first.is_connected

    clock.advance(599)
    async with pool.acquire(sid) as (same, _sid):
        assert same is first

    clock.advance(600)
    async with pool.acquire(sid) as (replacement, _sid):
        assert replacement is not first
        assert first.is_connected is False

    assert pool.get_stats()["evictions"]["idle_timeout"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_max_uses_retires_on_release_boundary():
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, pool_max_uses=2),
        FakeClient,
    )
    async with pool.acquire() as (client, sid):
        pass
    async with pool.acquire(sid) as (same, _sid):
        assert same is client
        assert client.is_connected

    assert client.is_connected is False
    assert pool.get_stats()["evictions"]["max_uses"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_max_age_never_interrupts_active_lease():
    clock = FakeClock()
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, pool_max_age=10),
        FakeClient,
        clock=clock,
    )
    async with pool.acquire() as (client, _sid):
        clock.advance(11)
        assert client.is_connected

    assert client.is_connected is False
    assert pool.get_stats()["evictions"]["max_age"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_keepalive_checks_outside_pool_lock_and_removes_dead_client():
    pool = ACPConnectionPool(make_config(), FakeClient)
    async with pool.acquire():
        pass
    FakeClient.ping_succeeds = False

    await pool._run_keepalive()
    await wait_until(lambda: pool.get_stats()["total_open"] == 0)

    stats = pool.get_stats()
    assert stats["keepalive_runs"] == 1
    assert stats["health_check_failures"] == 1
    await pool.close_all()


@pytest.mark.asyncio
async def test_close_all_closes_leased_client_and_rejects_new_acquire():
    pool = ACPConnectionPool(make_config(), FakeClient)
    lease = pool.acquire()
    client, _sid = await lease.__aenter__()

    await pool.close_all()
    assert client.is_connected is False
    await lease.__aexit__(None, None, None)

    with pytest.raises(ACPPoolClosedError):
        async with pool.acquire():
            pytest.fail("closed pool must not yield")


@pytest.mark.asyncio
async def test_close_all_publishes_closed_before_waiting_for_keepalive_exit():
    pool = ACPConnectionPool(make_config(pool_keepalive_interval=1), FakeClient)
    cancelling = asyncio.Event()
    allow_exit = asyncio.Event()

    async def slow_keepalive_exit() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelling.set()
            await allow_exit.wait()
            raise

    pool._keepalive_task = asyncio.create_task(slow_keepalive_exit())
    close_task = asyncio.create_task(pool.close_all())
    await cancelling.wait()

    with pytest.raises(ACPPoolClosedError):
        async with pool.acquire():
            pytest.fail("close must reject acquire before keepalive has exited")

    allow_exit.set()
    await close_task


@pytest.mark.asyncio
async def test_close_all_waits_for_connection_that_finishes_creating_during_close():
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, pool_acquire_timeout=1),
        FakeClient,
    )
    connect_gate = asyncio.Event()
    disconnect_gate = asyncio.Event()
    FakeClient.connect_gate = connect_gate
    FakeClient.disconnect_gate = disconnect_gate

    async def acquire_during_close() -> None:
        async with pool.acquire():
            pytest.fail("a connection completed after close must never be leased")

    acquire_task = asyncio.create_task(acquire_during_close())
    await wait_until(lambda: pool.get_stats()["creating"] == 1)
    close_task = asyncio.create_task(pool.close_all())
    await wait_until(lambda: pool._closed)

    connect_gate.set()
    await wait_until(lambda: pool.get_stats()["closing"] == 1)
    assert not close_task.done()

    disconnect_gate.set()
    await close_task
    with pytest.raises(ACPPoolClosedError):
        await acquire_task
    assert FakeClient.instances[0].is_connected is False
    assert pool.get_stats()["total_reserved"] == 0


@pytest.mark.asyncio
async def test_cancelling_close_all_does_not_cancel_physical_disconnect():
    pool = ACPConnectionPool(make_config(pool_size=1, pool_max_connections=1), FakeClient)
    async with pool.acquire() as (client, _sid):
        pass
    disconnect_gate = asyncio.Event()
    FakeClient.disconnect_gate = disconnect_gate

    close_task = asyncio.create_task(pool.close_all())
    await wait_until(lambda: pool.get_stats()["closing"] == 1)
    close_task.cancel()
    await asyncio.sleep(0)
    close_task.cancel()
    await asyncio.sleep(0)

    assert not close_task.done()
    assert client.is_connected
    second_close = asyncio.create_task(pool.close_all())

    disconnect_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await close_task
    await second_close

    assert pool.get_stats()["total_reserved"] == 0
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_one_hundred_concurrent_acquires_never_exceed_eight_reservations():
    pool = ACPConnectionPool(
        make_config(
            pool_size=5,
            pool_max_connections=8,
            pool_acquire_timeout=5,
        ),
        FakeClient,
    )
    connect_gate = asyncio.Event()
    release_gate = asyncio.Event()
    FakeClient.connect_gate = connect_gate

    async def worker() -> None:
        async with pool.acquire():
            await release_gate.wait()

    tasks = [asyncio.create_task(worker()) for _ in range(100)]
    await wait_until(
        lambda: pool.get_stats()["creating"] == 8 and pool.get_stats()["waiting"] == 92,
        timeout=2,
    )
    assert pool.get_stats()["total_reserved"] == 8

    connect_gate.set()
    await wait_until(lambda: pool.get_stats()["active"] == 8, timeout=2)
    release_gate.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)

    stats = pool.get_stats()
    assert stats["peak_total_reserved"] == 8
    assert stats["total_reserved"] <= 8
    assert stats["waiting"] == 0
    assert stats["created_total"] == sum(stats["created_by_reason"].values())
    await pool.close_all()


@pytest.mark.asyncio
async def test_capacity_budget_is_independent_per_pool_instance():
    config = make_config(pool_size=1, pool_max_connections=1)
    first_pool = ACPConnectionPool(config, FakeClient)
    second_pool = ACPConnectionPool(config, FakeClient)

    first_lease = first_pool.acquire()
    second_lease = second_pool.acquire()
    await first_lease.__aenter__()
    await second_lease.__aenter__()

    assert first_pool.get_stats()["total_reserved"] == 1
    assert second_pool.get_stats()["total_reserved"] == 1
    assert len(FakeClient.instances) == 2

    await first_lease.__aexit__(None, None, None)
    await second_lease.__aexit__(None, None, None)
    await first_pool.close_all()
    await second_pool.close_all()


@pytest.mark.asyncio
async def test_cancelled_waiter_is_removed_without_leaking_capacity():
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, pool_acquire_timeout=1),
        FakeClient,
    )
    holder = pool.acquire()
    await holder.__aenter__()

    async def wait_for_capacity() -> None:
        async with pool.acquire(fresh=True):
            pass

    waiter = asyncio.create_task(wait_for_capacity())
    await wait_until(lambda: pool.get_stats()["waiting"] == 1)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert pool.get_stats()["waiting"] == 0
    assert pool.get_stats()["waiters_cancelled_total"] == 1
    assert pool.get_stats()["total_reserved"] == 1
    await holder.__aexit__(None, None, None)
    await pool.close_all()


@pytest.mark.asyncio
async def test_creation_failure_releases_reservation_and_wakes_capacity():
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, max_retries=1),
        FakeClient,
    )
    FakeClient.connect_error = ConnectionError("startup failed")

    with pytest.raises(ConnectionError, match="startup failed"):
        async with pool.acquire():
            pytest.fail("failed creation must not yield")

    stats = pool.get_stats()
    assert stats["creating"] == 0
    assert stats["total_reserved"] == 0
    assert stats["creation_failures_total"] == 1

    FakeClient.connect_error = None
    async with pool.acquire() as (client, _sid):
        assert client.is_connected
    await pool.close_all()


@pytest.mark.asyncio
async def test_cancellation_after_connect_cleans_unregistered_client_and_claim():
    pool = ACPConnectionPool(
        make_config(pool_size=1, pool_max_connections=1, pool_acquire_timeout=1),
        FakeClient,
    )
    connect_gate = asyncio.Event()
    disconnect_gate = asyncio.Event()
    FakeClient.connect_gate = connect_gate
    FakeClient.disconnect_gate = disconnect_gate

    async def acquire_target() -> None:
        async with pool.acquire("target-session"):
            pytest.fail("cancelled creation must not yield")

    task = asyncio.create_task(acquire_target())
    await wait_until(lambda: pool.get_stats()["creating"] == 1)

    async with pool._lock:
        connect_gate.set()
        await wait_until(lambda: len(FakeClient.instances) == 1 and FakeClient.instances[0].is_connected)
        task.cancel()
        await asyncio.sleep(0)

    await wait_until(lambda: FakeClient.instances[0].disconnect_calls == 1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    disconnect_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    stats = pool.get_stats()
    assert FakeClient.instances[0].is_connected is False
    assert stats["total_reserved"] == 0
    assert stats["claimed_sessions"] == 0
    await pool.close_all()
