"""Async retry with exponential backoff for ACP operations."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

RETRYABLE_KEYWORDS: list[str] = [
    "timeout",
    "connection",
    "reset",
    "refused",
    "overloaded",
    "rate_limit",
    "503",
    "502",
    "429",
]


def is_retryable(error: Exception) -> bool:
    """Check if an error is worth retrying based on keyword matching."""
    msg = str(error).lower()
    return any(kw in msg for kw in RETRYABLE_KEYWORDS)


async def retry_async(
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs: Any,
) -> Any:
    """Call *func* with exponential backoff retry on retryable errors.

    Delay sequence: ``base_delay`` * 2^(attempt-1), capped at *max_delay*.
    Non-retryable errors are raised immediately.

    Args:
        func: Async callable to invoke.
        max_retries: Maximum number of attempts.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if not is_retryable(exc) or attempt == max_retries:
                raise
            # Full jitter: randomize delay to prevent thundering herd
            max_backoff = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay = random.uniform(0, max_backoff)
            logger.warning(
                "Retry %d/%d after %.1fs: %s",
                attempt,
                max_retries,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    # Theoretically unreachable: the loop always raises on the last attempt.
    # Kept for type-checker satisfaction.
    assert last_error is not None  # pragma: no cover
    raise last_error  # pragma: no cover
