"""Tests for cancellation-safe internal asyncio ownership helpers."""

from __future__ import annotations

import asyncio

import pytest

from soulacp._async import wait_for_owned


@pytest.mark.asyncio
async def test_wait_for_owned_preserves_cancellation_when_inner_completes() -> None:
    loop = asyncio.get_running_loop()
    inner: asyncio.Future[str] = loop.create_future()
    outer = asyncio.create_task(wait_for_owned(inner, 10.0))
    await asyncio.sleep(0)

    outer.cancel()
    inner.set_result("completed")

    with pytest.raises(asyncio.CancelledError):
        await outer


@pytest.mark.asyncio
async def test_wait_for_owned_cancels_and_retrieves_timed_out_awaitable() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(wait_for_owned(blocked(), 0.01))
    await started.wait()

    with pytest.raises(asyncio.TimeoutError):
        await task
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_wait_for_owned_delays_repeated_cancellation_until_timeout_cleanup_finishes() -> None:
    inner_cancelled = asyncio.Event()
    release_inner = asyncio.Event()
    inner_finished = asyncio.Event()

    async def slow_cancellation() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            inner_cancelled.set()
            await release_inner.wait()
            raise
        finally:
            inner_finished.set()

    outer = asyncio.create_task(wait_for_owned(slow_cancellation(), 0.01))
    await inner_cancelled.wait()

    outer.cancel()
    await asyncio.sleep(0)
    assert outer.done() is False

    release_inner.set()
    with pytest.raises(asyncio.CancelledError):
        await outer
    assert inner_finished.is_set()
