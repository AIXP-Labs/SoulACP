"""ManagedSession preserves all upstream configuration fields."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from soulacp.adapters.base_client import RPCError
from soulacp.config import ACPConfig
from soulacp.errors import (
    ACPPoolAcquireTimeoutError,
    ACPPoolClosedError,
    ACPPoolError,
    ACPPromptTimeoutError,
    ACPRequestOutcomeUnknownError,
    ACPResumeError,
)
from soulacp.session import ManagedSession


def test_managed_session_preserves_complete_explicit_config():
    config = ACPConfig(
        provider="claude",
        model="original",
        pool_size=3,
        pool_max_connections=4,
        pool_acquire_timeout=17,
        pool_idle_timeout=123,
        pool_keepalive_interval=0,
        pool_max_age=456,
        pool_max_uses=7,
        pool_cross_session_reuse=True,
        max_turns=321,
        extra_args=["--flag"],
        extra_env={"CUSTOM": "1"},
    )
    session = ManagedSession(provider="claude", model="target", config=config)

    pool_config = session._get_pool()._config

    assert pool_config.provider == "claude"
    assert pool_config.model == "target"
    assert pool_config.pool_size == 3
    assert pool_config.pool_max_connections == 4
    assert pool_config.pool_acquire_timeout == 17
    assert pool_config.pool_idle_timeout == 123
    assert pool_config.pool_max_age == 456
    assert pool_config.pool_max_uses == 7
    assert pool_config.pool_cross_session_reuse is True
    assert pool_config.max_turns == 321
    assert pool_config.extra_args == ["--flag"]
    assert pool_config.extra_env == {"CUSTOM": "1"}


def test_managed_session_preserves_falsy_custom_store():
    class FalsyStore:
        def __bool__(self) -> bool:
            return False

    store = FalsyStore()
    session = ManagedSession(provider="claude", model="target", session_store=store)

    assert session._store is store


@pytest.mark.asyncio
async def test_outer_retry_budget_is_not_multiplied_by_pool_startup(monkeypatch):
    class FailingStartupClient:
        instances = 0

        def __init__(self, _config) -> None:
            type(self).instances += 1
            self.session_id = None

        @property
        def is_connected(self) -> bool:
            return False

        @property
        def is_idle_timeout(self) -> bool:
            return False

        async def connect(self) -> None:
            raise ConnectionError()

        async def disconnect(self) -> None:
            return None

        async def ping(self) -> bool:
            return False

        async def resume(self, _session_id: str) -> bool:
            return False

        async def query(self, _prompt: str) -> str:
            raise AssertionError("query must not run")

        async def query_stream(self, _prompt: str):
            if False:
                yield ""

    monkeypatch.setattr("soulacp.session.resolve_client_class", lambda _provider: FailingStartupClient)
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(max_retries=3, retry_base_delay=0, pool_keepalive_interval=0),
    )

    with pytest.raises(ConnectionError):
        await session.query("hello")

    assert FailingStartupClient.instances == 3
    assert session._get_pool()._startup_attempts == 1
    await session.close()


def test_managed_session_without_config_reads_environment(monkeypatch):
    monkeypatch.setenv("ACP_POOL_SIZE", "2")
    monkeypatch.setenv("ACP_POOL_MAX_CONNECTIONS", "3")
    monkeypatch.setenv("ACP_POOL_MAX_AGE", "99")

    session = ManagedSession(provider="claude", model="target")

    assert session._config.pool_size == 2
    assert session._config.pool_max_connections == 3
    assert session._config.pool_max_age == 99


class StubStore:
    def __init__(self, session_id: str | None = "stored-session") -> None:
        self.session_id = session_id
        self.clear_calls = 0

    async def get_session_id(self, _user_id, _provider):
        return self.session_id

    async def set_session_id(self, _user_id, _provider, session_id):
        self.session_id = session_id

    async def clear(self, _user_id, _provider):
        self.clear_calls += 1
        self.session_id = None


class StubQueryClient:
    async def query(self, _prompt):
        return "ok"

    async def query_stream(self, _prompt):
        yield "ok"


class SlowQueryClient(StubQueryClient):
    def __init__(self, pool) -> None:
        self.pool = pool

    async def query(self, _prompt):
        await asyncio.sleep(0.01)
        return "ok"


class ConcurrentTrackingPool:
    def __init__(self) -> None:
        self.requests: list[str | None] = []
        self.active = 0
        self.max_active = 0

    @asynccontextmanager
    async def acquire(self, session_id=None):
        self.requests.append(session_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            yield SlowQueryClient(self), "shared-session"
        finally:
            self.active -= 1


class ClosableStreamingClient:
    def __init__(self) -> None:
        self.closed = asyncio.Event()

    async def query_stream(self, _prompt):
        try:
            yield "first"
            await asyncio.Event().wait()
        finally:
            self.closed.set()


class StreamingTrackingPool:
    def __init__(self) -> None:
        self.client = ClosableStreamingClient()
        self.released = asyncio.Event()

    @asynccontextmanager
    async def acquire(self, session_id=None):
        try:
            yield self.client, session_id
        finally:
            self.released.set()


@pytest.mark.asyncio
async def test_managed_session_serializes_first_turns_for_same_user():
    store = StubStore(session_id=None)
    pool = ConcurrentTrackingPool()
    session = ManagedSession(
        provider="claude",
        model="target",
        session_store=store,
        config=ACPConfig(max_retries=1),
    )
    session._get_pool = lambda *_args, **_kwargs: pool

    assert await asyncio.gather(
        session.query("first", user_id="same-user"),
        session.query("second", user_id="same-user"),
    ) == ["ok", "ok"]
    assert pool.max_active == 1
    assert pool.requests == [None, "shared-session"]


@pytest.mark.asyncio
async def test_managed_stream_close_closes_client_stream_before_releasing_pool():
    pool = StreamingTrackingPool()
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(max_retries=1),
    )
    session._get_pool = lambda *_args, **_kwargs: pool
    stream = session.stream("hello")

    assert await anext(stream) == "first"
    await stream.aclose()

    assert pool.client.closed.is_set()
    assert pool.released.is_set()


class ResumeThenFreshPool:
    def __init__(self) -> None:
        self.requests: list[str | None] = []

    @asynccontextmanager
    async def acquire(self, session_id=None):
        self.requests.append(session_id)
        if session_id is not None:
            raise ACPResumeError(session_id)
        yield StubQueryClient(), "new-session"


class ResumeTransportRetryPool:
    def __init__(self) -> None:
        self.requests: list[str | None] = []

    @asynccontextmanager
    async def acquire(self, session_id=None):
        self.requests.append(session_id)
        if len(self.requests) == 1:
            raise ConnectionError("transport lost during resume")
        yield StubQueryClient(), session_id


class DeferredSessionClient(StubQueryClient):
    def __init__(self) -> None:
        self.session_id = None

    async def query(self, _prompt):
        self.session_id = "deferred-session"
        return "ok"


class DeferredSessionPool:
    def __init__(self) -> None:
        self.client = DeferredSessionClient()

    @asynccontextmanager
    async def acquire(self, session_id=None):
        yield self.client, session_id


@pytest.mark.asyncio
async def test_session_created_during_query_is_saved_after_success():
    store = StubStore(session_id=None)
    pool = DeferredSessionPool()
    session = ManagedSession(
        provider="cursor-cli",
        model="cursor-cli/default",
        session_store=store,
        config=ACPConfig(max_retries=1),
    )
    session._get_pool = lambda *_args, **_kwargs: pool

    assert await session.query("hello", user_id="user") == "ok"
    assert store.session_id == "deferred-session"


@pytest.mark.asyncio
async def test_resume_reset_does_not_consume_only_network_attempt():
    store = StubStore()
    pool = ResumeThenFreshPool()
    session = ManagedSession(
        provider="claude",
        model="target",
        session_store=store,
        config=ACPConfig(max_retries=1),
    )
    session._get_pool = lambda *_args, **_kwargs: pool

    result = await session.query("hello", user_id="user")

    assert result == "ok"
    assert pool.requests == ["stored-session", None]
    assert store.clear_calls == 1
    assert store.session_id == "new-session"


@pytest.mark.asyncio
async def test_resume_transport_failure_retries_same_session_without_clearing_mapping():
    store = StubStore()
    pool = ResumeTransportRetryPool()
    session = ManagedSession(
        provider="claude",
        model="target",
        session_store=store,
        config=ACPConfig(max_retries=2, retry_base_delay=0),
    )
    session._get_pool = lambda *_args, **_kwargs: pool

    assert await session.query("hello", user_id="user") == "ok"
    assert pool.requests == ["stored-session", "stored-session"]
    assert store.clear_calls == 0
    assert store.session_id == "stored-session"


class CapacityTimeoutPool:
    def __init__(self) -> None:
        self.calls = 0

    @asynccontextmanager
    async def acquire(self, session_id=None):
        self.calls += 1
        raise ACPPoolAcquireTimeoutError(0.01, {"total_reserved": 8})
        yield StubQueryClient(), None


@pytest.mark.asyncio
async def test_capacity_timeout_is_not_retried_or_sent_to_fallback():
    pool = CapacityTimeoutPool()
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(enable_fallback=True, max_retries=3),
    )
    get_pool_calls = 0

    def get_pool(*_args, **_kwargs):
        nonlocal get_pool_calls
        get_pool_calls += 1
        return pool

    session._get_pool = get_pool

    with pytest.raises(ACPPoolAcquireTimeoutError):
        await session.query("hello")

    assert pool.calls == 1
    assert get_pool_calls == 1


class FailingClient:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def query(self, _prompt):
        self.calls += 1
        raise self.error


class YieldingPool:
    def __init__(self, client: FailingClient) -> None:
        self.client = client
        self.calls = 0

    @asynccontextmanager
    async def acquire(self, session_id=None):
        self.calls += 1
        yield self.client, "session"


@pytest.mark.asyncio
async def test_partial_connection_failure_is_never_retried_or_fallbacked():
    error = ConnectionError("transport lost")
    error.partial_output = "already emitted"
    client = FailingClient(error)
    pool = YieldingPool(client)
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(enable_fallback=True, max_retries=3),
    )
    get_pool_calls = 0

    def get_pool(*_args, **_kwargs):
        nonlocal get_pool_calls
        get_pool_calls += 1
        return pool

    session._get_pool = get_pool

    with pytest.raises(ACPRequestOutcomeUnknownError) as exc_info:
        await session.query("hello")
    assert exc_info.value.partial_output == "already emitted"
    assert client.calls == 1
    assert pool.calls == 1
    assert get_pool_calls == 1


@pytest.mark.asyncio
async def test_prompt_timeout_is_never_sent_to_fallback():
    error = ACPPromptTimeoutError(1)
    client = FailingClient(error)
    pool = YieldingPool(client)
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(enable_fallback=True, max_retries=3),
    )
    get_pool_calls = 0

    def get_pool(*_args, **_kwargs):
        nonlocal get_pool_calls
        get_pool_calls += 1
        return pool

    session._get_pool = get_pool

    with pytest.raises(ACPPromptTimeoutError):
        await session.query("hello")
    assert client.calls == 1
    assert get_pool_calls == 1


@pytest.mark.asyncio
async def test_unknown_prompt_outcome_is_never_retried_or_fallbacked():
    error = ACPRequestOutcomeUnknownError(ConnectionError("transport lost before text"))
    client = FailingClient(error)
    pool = YieldingPool(client)
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(enable_fallback=True, max_retries=3),
    )
    get_pool_calls = 0

    def get_pool(*_args, **_kwargs):
        nonlocal get_pool_calls
        get_pool_calls += 1
        return pool

    session._get_pool = get_pool

    with pytest.raises(ACPRequestOutcomeUnknownError):
        await session.query("run side effect")
    assert client.calls == 1
    assert pool.calls == 1
    assert get_pool_calls == 1


@pytest.mark.asyncio
async def test_prompt_rpc_error_is_never_retried_or_fallbacked():
    error = RPCError(code=-32603, message="Internal error", method="session/prompt")
    client = FailingClient(error)
    pool = YieldingPool(client)
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(enable_fallback=True, max_retries=3),
    )
    get_pool_calls = 0

    def get_pool(*_args, **_kwargs):
        nonlocal get_pool_calls
        get_pool_calls += 1
        return pool

    session._get_pool = get_pool

    with pytest.raises(RPCError):
        await session.query("run side effect")
    assert client.calls == 1
    assert pool.calls == 1
    assert get_pool_calls == 1


@pytest.mark.asyncio
async def test_prompt_rpc_context_overflow_is_not_implicitly_replayed():
    error = RPCError(code=-32000, message="prompt is too long for context limit", method="session/prompt")
    client = FailingClient(error)
    pool = YieldingPool(client)
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(enable_fallback=True, max_retries=3),
    )
    session._get_pool = lambda *_args, **_kwargs: pool

    with pytest.raises(RPCError):
        await session.query("run side effect")

    assert client.calls == 1
    assert pool.calls == 1


@pytest.mark.asyncio
async def test_generic_post_prompt_context_error_is_fail_closed():
    error = RuntimeError("context length exceeded")
    client = FailingClient(error)
    pool = YieldingPool(client)
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(enable_fallback=True, max_retries=3),
    )
    session._get_pool = lambda *_args, **_kwargs: pool

    with pytest.raises(ACPRequestOutcomeUnknownError) as exc_info:
        await session.query("run side effect")

    assert exc_info.value.cause is error
    assert client.calls == 1
    assert pool.calls == 1


class ProviderAwareStore:
    def __init__(self, sessions: dict[tuple[str, str], str] | None = None) -> None:
        self.sessions = dict(sessions or {})
        self.clear_calls: list[tuple[str, str | None]] = []

    async def get_session_id(self, user_id, provider):
        return self.sessions.get((user_id, provider))

    async def set_session_id(self, user_id, provider, session_id):
        self.sessions[(user_id, provider)] = session_id

    async def clear(self, user_id, provider=None):
        self.clear_calls.append((user_id, provider))
        if provider is not None:
            self.sessions.pop((user_id, provider), None)


class RecordingSuccessPool:
    def __init__(self, *, reject_session: str | None = None) -> None:
        self.requests: list[str | None] = []
        self.reject_session = reject_session

    @asynccontextmanager
    async def acquire(self, session_id=None):
        self.requests.append(session_id)
        if session_id == self.reject_session:
            raise ACPResumeError(session_id)
        yield StubQueryClient(), "fallback-new"


class ReleaseContractFailurePool:
    @asynccontextmanager
    async def acquire(self, session_id=None):
        yield StubQueryClient(), session_id
        raise ACPPoolError("release contract failed")


@pytest.mark.asyncio
async def test_fallback_reuses_its_own_stored_session():
    store = ProviderAwareStore({("user", "gemini"): "fallback-stored"})
    primary_pool = AcquireFailurePool(FileNotFoundError("primary missing"))
    fallback_pool = RecordingSuccessPool()
    session = ManagedSession(
        provider="claude",
        model="target",
        session_store=store,
        config=ACPConfig(enable_fallback=True, max_retries=1),
    )
    session._get_pool = lambda provider=None, model=None: primary_pool if provider is None else fallback_pool

    assert await session.query("hello", user_id="user") == "ok"
    assert fallback_pool.requests == ["fallback-stored"]
    assert store.sessions[("user", "gemini")] == "fallback-new"


@pytest.mark.asyncio
async def test_fallback_clears_stale_session_before_fresh_attempt():
    store = ProviderAwareStore({("user", "gemini"): "stale"})
    primary_pool = AcquireFailurePool(FileNotFoundError("primary missing"))
    fallback_pool = RecordingSuccessPool(reject_session="stale")
    session = ManagedSession(
        provider="claude",
        model="target",
        session_store=store,
        config=ACPConfig(enable_fallback=True, max_retries=1),
    )
    session._get_pool = lambda provider=None, model=None: primary_pool if provider is None else fallback_pool

    assert await session.query("hello", user_id="user") == "ok"
    assert fallback_pool.requests == ["stale", None]
    assert store.clear_calls == [("user", "gemini")]
    assert store.sessions[("user", "gemini")] == "fallback-new"


@pytest.mark.asyncio
async def test_reset_session_clears_primary_mapping():
    store = ProviderAwareStore({("user", "claude"): "old"})
    session = ManagedSession(provider="claude", model="target", session_store=store, config=ACPConfig())

    await session.reset_session("user")

    assert store.clear_calls == [("user", "claude")]
    assert ("user", "claude") not in store.sessions


@pytest.mark.asyncio
async def test_session_mapping_is_not_updated_before_pool_release_succeeds():
    store = ProviderAwareStore({("user", "claude"): "stored"})
    session = ManagedSession(
        provider="claude",
        model="target",
        session_store=store,
        config=ACPConfig(max_retries=1),
    )
    session._get_pool = lambda *_args, **_kwargs: ReleaseContractFailurePool()

    with pytest.raises(ACPRequestOutcomeUnknownError):
        await session.query("hello", user_id="user")

    assert store.sessions[("user", "claude")] == "stored"


@pytest.mark.asyncio
async def test_managed_session_rejects_invalid_prompt_and_user_id():
    session = ManagedSession(provider="claude", model="target", config=ACPConfig())

    with pytest.raises(TypeError, match="prompt"):
        await session.query(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="user_id"):
        await session.query("hello", user_id="")
    with pytest.raises(ValueError, match="prompt must contain valid Unicode"):
        await session.query("\ud800")
    with pytest.raises(ValueError, match="user_id must contain valid Unicode"):
        await session.query("hello", user_id="\ud800")

    assert session._pools == {}


@pytest.mark.asyncio
async def test_managed_session_cannot_reopen_after_close():
    session = ManagedSession(provider="claude", model="target", config=ACPConfig())

    await session.close()
    await session.close()

    with pytest.raises(ACPPoolClosedError, match="managed session is closed"):
        await session.query("hello")
    with pytest.raises(ACPPoolClosedError, match="managed session is closed"):
        await session.reset_session("user")
    with pytest.raises(ACPPoolClosedError, match="managed session is closed"):
        await session.__aenter__()


class AcquireFailurePool:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    @asynccontextmanager
    async def acquire(self, session_id=None):
        self.calls += 1
        raise self.error
        yield StubQueryClient(), None


@pytest.mark.asyncio
async def test_resume_authentication_required_is_not_retried_or_cleared():
    store = StubStore()
    error = RPCError(code=-32000, message="Authentication required", method="session/resume")
    pool = AcquireFailurePool(error)
    session = ManagedSession(
        provider="claude",
        model="target",
        session_store=store,
        config=ACPConfig(max_retries=3, retry_base_delay=0),
    )
    session._get_pool = lambda *_args, **_kwargs: pool

    with pytest.raises(RPCError) as exc_info:
        await session.query("hello", user_id="user")

    assert exc_info.value is error
    assert pool.calls == 1
    assert store.clear_calls == 0
    assert store.session_id == "stored-session"


@pytest.mark.asyncio
async def test_fallback_unknown_outcome_is_not_hidden_by_primary_error():
    primary_error = FileNotFoundError("primary CLI missing")
    fallback_error = ACPRequestOutcomeUnknownError(ConnectionError("fallback transport lost"))
    primary_pool = AcquireFailurePool(primary_error)
    fallback_pool = YieldingPool(FailingClient(fallback_error))
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(enable_fallback=True, max_retries=1),
    )

    def get_pool(provider=None, model=None):
        return primary_pool if provider is None else fallback_pool

    session._get_pool = get_pool

    with pytest.raises(ACPRequestOutcomeUnknownError) as exc_info:
        await session.query("run side effect")

    assert exc_info.value is fallback_error
    assert exc_info.value.__cause__ is primary_error


@pytest.mark.asyncio
async def test_fallback_transport_failure_after_prompt_start_is_outcome_unknown():
    primary_error = FileNotFoundError("primary CLI missing")
    fallback_error = ConnectionError("fallback transport lost")
    primary_pool = AcquireFailurePool(primary_error)
    fallback_pool = YieldingPool(FailingClient(fallback_error))
    session = ManagedSession(
        provider="claude",
        model="target",
        config=ACPConfig(enable_fallback=True, max_retries=1),
    )

    def get_pool(provider=None, model=None):
        return primary_pool if provider is None else fallback_pool

    session._get_pool = get_pool

    with pytest.raises(ACPRequestOutcomeUnknownError) as exc_info:
        await session.query("run side effect")

    assert exc_info.value.cause is fallback_error
    assert exc_info.value.__cause__ is primary_error


class SlowClosePool:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.close_calls = 0

    async def close_all(self) -> None:
        self.close_calls += 1
        self.started.set()
        await self.release.wait()
        self.closed = True


class RetryClosePool:
    def __init__(self) -> None:
        self.close_calls = 0
        self.closed = False

    async def close_all(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise ACPPoolError("transport still owned")
        self.closed = True


class RetryFlushStore:
    def __init__(self) -> None:
        self.flush_calls = 0

    async def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_calls == 1:
            raise OSError("flush failed")


@pytest.mark.asyncio
async def test_concurrent_managed_close_calls_share_physical_cleanup():
    session = ManagedSession(provider="claude", model="target", config=ACPConfig())
    pool = SlowClosePool()
    session._pools = {"one": pool}

    first = asyncio.create_task(session.close())
    await pool.started.wait()
    second = asyncio.create_task(session.close())
    await asyncio.sleep(0)

    assert not first.done()
    assert not second.done()
    assert pool.close_calls == 1

    pool.release.set()
    await asyncio.gather(first, second)

    assert pool.closed is True
    assert pool.close_calls == 1


@pytest.mark.asyncio
async def test_managed_close_finishes_all_pool_closes_when_caller_is_cancelled():
    session = ManagedSession(provider="claude", model="target", config=ACPConfig())
    first = SlowClosePool()
    second = SlowClosePool()
    session._pools = {"one": first, "two": second}

    closing = asyncio.create_task(session.close())
    await asyncio.gather(first.started.wait(), second.started.wait())
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()

    first.release.set()
    second.release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing

    assert first.closed is True
    assert second.closed is True
    assert session._pools == {}


@pytest.mark.asyncio
async def test_managed_close_retains_failed_pool_for_retry():
    session = ManagedSession(provider="claude", model="target", config=ACPConfig())
    failed = RetryClosePool()
    successful = RetryClosePool()
    successful.close_calls = 1
    session._pools = {"failed": failed, "successful": successful}

    with pytest.raises(ACPPoolError, match="transport still owned"):
        await session.close()

    assert session._pools == {"failed": failed}
    assert successful.closed is True

    await session.close()
    assert failed.closed is True
    assert session._pools == {}


@pytest.mark.asyncio
async def test_managed_close_retries_failed_store_flush():
    store = RetryFlushStore()
    session = ManagedSession(
        provider="claude",
        model="target",
        session_store=store,
        config=ACPConfig(),
    )

    with pytest.raises(OSError, match="flush failed"):
        await session.close()

    await session.close()
    assert store.flush_calls == 2
