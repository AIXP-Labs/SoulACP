"""Test retry logic."""

import asyncio

import pytest


def test_is_retryable():
    from soulacp.retry import is_retryable

    # Retryable errors
    assert is_retryable(ConnectionError("connection reset"))
    assert is_retryable(TimeoutError("timeout"))
    assert is_retryable(ConnectionError())
    assert is_retryable(asyncio.TimeoutError())
    assert is_retryable(OSError("connection refused"))

    # Non-retryable
    assert not is_retryable(ValueError("bad value"))
    assert not is_retryable(KeyError("missing key"))


def test_retry_async_success():
    from soulacp.retry import retry_async

    call_count = 0

    async def succeed():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = asyncio.run(retry_async(succeed, max_retries=3, base_delay=0.01))
    assert result == "ok"
    assert call_count == 1


def test_retry_async_eventual_success():
    from soulacp.retry import retry_async

    call_count = 0

    async def fail_then_succeed():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("connection reset")
        return "ok"

    result = asyncio.run(retry_async(fail_then_succeed, max_retries=3, base_delay=0.01))
    assert result == "ok"
    assert call_count == 3


def test_retry_async_exhausted():
    from soulacp.retry import retry_async

    call_count = 0

    async def always_fail():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("connection reset")

    with pytest.raises(ConnectionError):
        asyncio.run(retry_async(always_fail, max_retries=2, base_delay=0.01))
    assert call_count == 2  # 1 initial + 1 retry (max_retries=2 means 2 total attempts)


def test_retry_async_zero_means_one_attempt_without_retry():
    from soulacp.retry import retry_async

    call_count = 0

    async def fail_once():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("connection reset")

    with pytest.raises(ConnectionError):
        asyncio.run(retry_async(fail_once, max_retries=0, base_delay=0))
    assert call_count == 1


def test_retry_helpers_fail_closed_and_bound_large_attempts():
    from soulacp.retry import _bounded_backoff, is_retryable

    class BrokenError(Exception):
        @property
        def partial_output(self):
            raise RuntimeError("broken partial output")

        def __str__(self):
            raise RuntimeError("broken string conversion")

    assert not is_retryable(BrokenError())
    assert _bounded_backoff(1.0, 10**100, 30.0) == 30.0
    assert _bounded_backoff(0.0, 10**100, 30.0) == 0.0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_retries": -1}, "max_retries"),
        ({"max_retries": True}, "max_retries"),
        ({"base_delay": float("nan")}, "base_delay"),
        ({"base_delay": 10**400}, "base_delay"),
        ({"max_delay": -1}, "max_delay"),
    ],
)
def test_retry_async_rejects_invalid_policy(kwargs, message):
    from soulacp.retry import retry_async

    async def succeed():
        return "ok"

    with pytest.raises(ValueError, match=message):
        asyncio.run(retry_async(succeed, **kwargs))
