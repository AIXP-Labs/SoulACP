"""Async retry with exponential backoff for ACP operations."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from typing import Any

from soulacp.config import _is_finite_number
from soulacp.privacy import exception_summary

logger: logging.Logger = logging.getLogger(__name__)

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


def _inspect_partial_output(error: BaseException) -> tuple[str, bool]:
    """Return partial text plus whether the exception exposed it safely."""
    try:
        partial = getattr(error, "partial_output", "")
    except Exception:
        return "", False
    if partial is None:
        return "", True
    if isinstance(partial, str):
        return partial, True
    return "", False


def _bounded_backoff(base_delay: float, attempt: int, max_delay: float) -> float:
    """Compute capped exponential backoff without materializing huge integers."""
    base = float(base_delay)
    cap = float(max_delay)
    if base <= 0 or cap <= 0:
        return 0.0
    try:
        return min(math.ldexp(base, max(0, attempt - 1)), cap)
    except OverflowError:
        return cap


def is_retryable(error: Exception) -> bool:
    """Check if an error is worth retrying.

    Decision order:
    1. ``RPCError`` — defer to ``RPCError.is_retryable`` (JSON-RPC code-based).
    2. Fallback to keyword match on the stringified error.
    """
    # Pool pressure, explicit response deadlines, and any failure after partial
    # output must not be replayed automatically.
    try:
        from soulacp.errors import ACPPoolError, ACPResponseError

        if isinstance(error, (ACPPoolError, ACPResponseError)):
            return False
    except ImportError:  # pragma: no cover - package layout change
        pass
    partial_output, partial_output_is_safe = _inspect_partial_output(error)
    if not partial_output_is_safe or partial_output:
        return False

    # Lazy import to avoid circular dependency (adapters import retry-free).
    try:
        from soulacp.adapters.base_client import RPCError

        if isinstance(error, RPCError):
            try:
                return error.is_retryable
            except Exception:
                return False
    except ImportError:  # pragma: no cover — package layout change
        pass

    # These exception types are transient even when their message is empty
    # (``asyncio.TimeoutError()`` commonly is).  Keyword matching alone would
    # incorrectly turn them into single-attempt failures.
    if isinstance(error, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True

    try:
        msg = str(error).lower()
    except Exception:
        return False
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
    if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
        raise ValueError("max_retries must be an integer >= 0")
    for name, value in (("base_delay", base_delay), ("max_delay", max_delay)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not _is_finite_number(value) or value < 0:
            raise ValueError(f"{name} must be a finite number >= 0")

    attempts = max(1, max_retries)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            if not is_retryable(exc) or attempt == attempts:
                raise
            # Full jitter: randomize delay to prevent thundering herd
            max_backoff = _bounded_backoff(base_delay, attempt, max_delay)
            # Retry jitter is deliberately non-cryptographic.
            delay = random.uniform(0, max_backoff)  # nosec B311
            logger.warning(
                "Retry %d/%d after %.1fs: %s",
                attempt,
                attempts,
                delay,
                exception_summary(exc),
            )
            await asyncio.sleep(delay)
    if last_error is None:  # pragma: no cover - loop always returns or raises
        raise RuntimeError("retry loop ended without a result or error")
    raise last_error  # pragma: no cover
