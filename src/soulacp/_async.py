"""Cancellation-safe asyncio helpers for supported Python versions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import suppress
from typing import Any, TypeVar

_T = TypeVar("_T")


async def _cancel_and_retrieve(task: asyncio.Future[Any]) -> None:
    """Cancel an owned task and retrieve its outcome without hiding caller cancellation."""
    task.cancel()

    async def _retrieve() -> None:
        with suppress(BaseException):
            await task

    retrieval = asyncio.create_task(_retrieve())
    cancelled = False
    while True:
        try:
            await asyncio.shield(retrieval)
        except asyncio.CancelledError:
            # A fresh shield on the next iteration keeps retrieval owned even
            # through repeated caller cancellation.
            cancelled = True
            continue
        break
    if cancelled:
        raise asyncio.CancelledError


async def wait_for_owned(awaitable: Awaitable[_T], timeout: float) -> _T:
    """Bound an owned awaitable while preserving external cancellation.

    Python 3.10's ``asyncio.wait_for`` can consume caller cancellation when
    the wrapped awaitable completes in the same event-loop iteration. This
    helper uses ``asyncio.wait`` and explicitly owns cancellation and outcome
    retrieval for the wrapped task.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout)
    except BaseException:
        await _cancel_and_retrieve(task)
        raise
    if task not in done:
        await _cancel_and_retrieve(task)
        raise asyncio.TimeoutError
    return task.result()
