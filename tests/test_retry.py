"""Test retry logic."""

import asyncio

import pytest


def test_is_retryable():
    from soulacp.retry import is_retryable

    # Retryable errors
    assert is_retryable(ConnectionError("connection reset"))
    assert is_retryable(TimeoutError("timeout"))
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
