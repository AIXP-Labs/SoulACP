"""High-level session management with safe reuse, retry, and fallback."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import replace
from types import TracebackType
from typing import cast
from weakref import WeakValueDictionary

from soulacp.adapters.base_client import RPCError
from soulacp.config import FALLBACK_MAP, ACPConfig, _require_valid_unicode, resolve_client_class, resolve_provider
from soulacp.errors import (
    ACPAuthenticationError,
    ACPPoolAcquireTimeoutError,
    ACPPoolClosedError,
    ACPRequestOutcomeUnknownError,
    ACPResponseError,
    ACPResumeError,
)
from soulacp.pool import ACPConnectionPool
from soulacp.privacy import _raise_private_cause, exception_summary
from soulacp.retry import _bounded_backoff, _inspect_partial_output, is_retryable
from soulacp.session_store import ProviderSessionStore

logger: logging.Logger = logging.getLogger(__name__)


# Provider-specific overflow markers — server returns these in error.message
# when the CLI session has run out of context budget. Keep lowercase.
_OVERFLOW_MARKERS: tuple[str, ...] = (
    "prompt is too long",
    "context length",
    "context window",
    "too many tokens",
    "context limit",
)


def is_context_overflow(error: BaseException) -> bool:
    """Return True if *error* indicates the session ran out of context."""
    try:
        message = error.message if isinstance(error, RPCError) else str(error)
    except Exception:
        return False
    if not isinstance(message, str):
        return False
    msg = message.lower()
    return any(marker in msg for marker in _OVERFLOW_MARKERS)


class ManagedSession:
    """High-level ACP session with safe reuse, startup retry, and fallback.

    Wraps ACPConnectionPool + ProviderSessionStore to provide:
    - Session reuse across requests (same user gets same CLI session)
    - Explicit session reset after a caller-reviewed context overflow
    - Retry with exponential backoff on pre-prompt transient errors
    - Fallback to an alternate provider only before a prompt may have started

    Usage::

        session = ManagedSession(
            provider="claude",
            model="claude-acp/sonnet",
        )

        # Simple query
        response = await session.query("Hello!", user_id="user1")

        # Streaming
        async for chunk in session.stream("Write hello world", user_id="user1"):
            print(chunk, end="")

        await session.close()
    """

    def __init__(
        self,
        provider: str,
        model: str,
        session_store: ProviderSessionStore | None = None,
        config: ACPConfig | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self._store = session_store if session_store is not None else ProviderSessionStore()
        self._config = (
            ACPConfig.from_env(provider=provider, model=model)
            if config is None
            else replace(config, provider=provider, model=model)
        )
        self._pools: dict[str, ACPConnectionPool] = {}
        self._user_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._store_flush_complete = False

    def _get_pool(self, provider: str | None = None, model: str | None = None) -> ACPConnectionPool:
        if self._closed:
            raise ACPPoolClosedError("managed session is closed")
        p = provider or self.provider
        m = model or self.model
        key = f"{p}:{m}"
        if key not in self._pools:
            cfg = replace(self._config, provider=p, model=m)
            client_class = resolve_client_class(p)
            # ManagedSession owns the total pre-prompt attempt budget.  Pool
            # startup is therefore one attempt here, avoiding N x N retries.
            self._pools[key] = ACPConnectionPool(cfg, client_class, startup_attempts=1)
        return self._pools[key]

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        lock_key = f"{self.provider}:{user_id}"
        lock = self._user_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[lock_key] = lock
        return lock

    async def _save_session_id(self, user_id: str | None, provider: str, session_id: str | None) -> None:
        if not self._store or not user_id or not session_id:
            return
        try:
            await self._store.set_session_id(user_id, provider, session_id)
        except Exception as exc:
            logger.debug(
                "Session store save failed for provider=%s: %s",
                provider,
                exception_summary(exc),
            )

    async def query(self, prompt: str, *, user_id: str | None = None) -> str:
        """Send a prompt and get the full response.

        Handles session reuse and pre-prompt retry/fallback without replaying a
        prompt whose outcome may be unknown.
        """
        result = ""
        async with aclosing(self._execute(prompt, user_id=user_id, stream=False)) as execution:
            async for chunk in execution:
                result = chunk  # Last chunk is full response
        return result

    async def stream(self, prompt: str, *, user_id: str | None = None) -> AsyncGenerator[str, None]:
        """Send a prompt and stream response chunks."""
        async with aclosing(self._execute(prompt, user_id=user_id, stream=True)) as execution:
            async for chunk in execution:
                yield chunk

    async def reset_session(self, user_id: str) -> None:
        """Clear one user's primary-provider session mapping safely.

        This is the explicit recovery path after callers inspect a context
        overflow error. The same per-user lock used by queries prevents a late
        successful turn from restoring the mapping after the reset.
        """
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non-empty string")
        _require_valid_unicode(user_id, "user_id")
        if self._closed:
            raise ACPPoolClosedError("managed session is closed")
        async with self._get_user_lock(user_id):
            await self._store.clear(user_id, self.provider)

    async def _execute(
        self, prompt: str, *, user_id: str | None = None, stream: bool = False
    ) -> AsyncGenerator[str, None]:
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        _require_valid_unicode(prompt, "prompt")
        if user_id is not None and (not isinstance(user_id, str) or not user_id):
            raise ValueError("user_id must be a non-empty string when set")
        if user_id is not None:
            _require_valid_unicode(user_id, "user_id")
        if not user_id:
            async with aclosing(self._execute_serialized(prompt, user_id=user_id, stream=stream)) as execution:
                async for chunk in execution:
                    yield chunk
            return

        async with (
            self._get_user_lock(user_id),
            aclosing(self._execute_serialized(prompt, user_id=user_id, stream=stream)) as execution,
        ):
            async for chunk in execution:
                yield chunk

    async def _execute_serialized(self, prompt: str, *, user_id: str | None, stream: bool) -> AsyncGenerator[str, None]:
        pool = self._get_pool()

        # Session store lookup
        session_id: str | None = None
        if self._store and user_id:
            try:
                session_id = await self._store.get_session_id(user_id, self.provider)
            except Exception as e:
                logger.debug(
                    "Session store lookup failed for provider=%s: %s",
                    self.provider,
                    exception_summary(e),
                )

        max_attempts = max(1, self._config.max_retries)
        base_delay = self._config.retry_base_delay
        resume_reset = False
        primary_err: Exception | None = None
        attempt = 1

        while attempt <= max_attempts:
            prompt_started = False
            try:
                text: str | None = None
                actual_session_id: str | None = None
                async with pool.acquire(session_id=session_id) as (client, sid):
                    # Session changed (resume failed)
                    if session_id and sid != session_id:
                        logger.info("Stored session was replaced after resume")

                    if stream:
                        prompt_started = True
                        async with aclosing(client.query_stream(prompt)) as response_stream:
                            async for chunk in response_stream:
                                yield chunk
                        actual_session_id = getattr(client, "session_id", sid)
                    else:
                        prompt_started = True
                        text = await client.query(prompt)
                        actual_session_id = getattr(client, "session_id", sid)
                await self._save_session_id(user_id, self.provider, actual_session_id)
                if text is not None:
                    yield text
                return  # success

            except (ACPAuthenticationError, ACPPoolAcquireTimeoutError, ACPPoolClosedError):
                # Capacity and lifecycle pressure are local pool decisions, not
                # transport faults. Retrying or switching providers hides load.
                raise

            except ACPResponseError:
                # Prompt outcomes are unknown after a response deadline or
                # partial result. Replaying on this or another provider can
                # duplicate output and side effects.
                raise

            except ACPResumeError as resume_error:
                if prompt_started:
                    _raise_private_cause(ACPRequestOutcomeUnknownError(resume_error), resume_error)
                if not resume_reset:
                    logger.warning("Stored session cannot be resumed; clearing it and creating a new session")
                    resume_reset = True
                    session_id = None
                    if self._store and user_id:
                        try:
                            await self._store.clear(user_id, self.provider)
                        except Exception as exc:
                            logger.debug("Session store clear failed: %s", exception_summary(exc))
                    continue
                primary_err = resume_error
                break

            except (ConnectionError, asyncio.TimeoutError) as ce:
                partial_output, partial_output_is_safe = _inspect_partial_output(ce)
                if prompt_started:
                    _raise_private_cause(
                        ACPRequestOutcomeUnknownError(ce, partial_output=partial_output),
                        ce,
                    )
                if not partial_output_is_safe or partial_output:
                    raise
                if attempt < max_attempts:
                    delay = _bounded_backoff(base_delay, attempt, 30.0)
                    logger.warning(
                        "ACP error (attempt %d/%d), retrying in %.1fs: %s",
                        attempt,
                        max_attempts,
                        delay,
                        exception_summary(ce),
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                primary_err = ce
                break

            except Exception as exc:
                if isinstance(exc, RPCError) and exc.method == "session/prompt":
                    # ACP v1 has no prompt idempotency key. This check must
                    # precede legacy string-based overflow detection: an RPC
                    # error does not prove that agent tool side effects never
                    # ran, even when its message mentions a context limit.
                    raise
                partial_output, partial_output_is_safe = _inspect_partial_output(exc)
                if prompt_started:
                    _raise_private_cause(
                        ACPRequestOutcomeUnknownError(exc, partial_output=partial_output),
                        exc,
                    )
                if not partial_output_is_safe or partial_output:
                    raise
                # Retry transient RPC errors using code-aware decision
                if attempt < max_attempts and is_retryable(exc):
                    delay = _bounded_backoff(base_delay, attempt, 30.0)
                    code = exc.code if isinstance(exc, RPCError) else None
                    logger.warning(
                        "ACP retryable error (attempt %d/%d, code=%s), retrying in %.1fs: %s",
                        attempt,
                        max_attempts,
                        code,
                        delay,
                        exception_summary(exc),
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                primary_err = exc
                break

        # Fallback to alternate provider
        if self._config.auth_method_id is not None and primary_err is not None:
            raise primary_err
        if self._config.enable_fallback and primary_err:
            fallback_model = FALLBACK_MAP.get(self.provider)
            if fallback_model:
                fallback_provider = resolve_provider(fallback_model)
                logger.warning(
                    "Primary %s failed (%s), falling back to %s",
                    self.provider,
                    exception_summary(primary_err),
                    fallback_model,
                )
                fallback_pool = self._get_pool(fallback_provider, fallback_model)

                fallback_session_id: str | None = None
                if self._store and user_id:
                    try:
                        fallback_session_id = await self._store.get_session_id(user_id, fallback_provider)
                    except Exception as exc:
                        logger.debug(
                            "Fallback session store lookup failed for provider=%s: %s",
                            fallback_provider,
                            exception_summary(exc),
                        )

                fallback_prompt_started = False

                async def _run_fallback(requested_session_id: str | None) -> AsyncGenerator[str, None]:
                    nonlocal fallback_prompt_started
                    text: str | None = None
                    actual_session_id: str | None = None
                    async with fallback_pool.acquire(session_id=requested_session_id) as (client, sid):
                        fallback_prompt_started = True
                        if stream:
                            async with aclosing(client.query_stream(prompt)) as response_stream:
                                async for chunk in response_stream:
                                    yield chunk
                            actual_session_id = getattr(client, "session_id", sid)
                        else:
                            text = await client.query(prompt)
                            actual_session_id = getattr(client, "session_id", sid)
                    await self._save_session_id(user_id, fallback_provider, actual_session_id)
                    if text is not None:
                        yield text

                try:
                    try:
                        async with aclosing(_run_fallback(fallback_session_id)) as fallback_stream:
                            async for chunk in fallback_stream:
                                yield chunk
                    except ACPResumeError:
                        if fallback_prompt_started or fallback_session_id is None:
                            raise
                        if self._store and user_id:
                            try:
                                await self._store.clear(user_id, fallback_provider)
                            except Exception as exc:
                                logger.debug(
                                    "Fallback session store clear failed: %s",
                                    exception_summary(exc),
                                )
                        fallback_prompt_started = False
                        async with aclosing(_run_fallback(None)) as fallback_stream:
                            async for chunk in fallback_stream:
                                yield chunk
                    return
                except Exception as fb_err:
                    logger.error(
                        "Fallback %s also failed: %s",
                        fallback_model,
                        exception_summary(fb_err),
                    )
                    if (
                        fallback_prompt_started
                        and not isinstance(fb_err, ACPResponseError)
                        and not (isinstance(fb_err, RPCError) and fb_err.method == "session/prompt")
                    ):
                        partial_output, _partial_output_is_safe = _inspect_partial_output(fb_err)
                        fb_err = ACPRequestOutcomeUnknownError(
                            fb_err,
                            partial_output=partial_output,
                        )
                    _raise_private_cause(fb_err, primary_err, primary=True)

        if primary_err:
            raise primary_err

    async def close(self) -> None:
        """Close every pool, retaining failed cleanup work for a later retry."""
        self._closed = True
        if self._close_task is None or (self._close_task.done() and (self._pools or not self._store_flush_complete)):
            self._close_task = asyncio.create_task(self._finish_close())
        await self._await_owned_cleanup(self._close_task)

    async def _finish_close(self) -> None:
        pool_items = list(self._pools.items())
        results: list[object] = []
        if pool_items:
            results = await asyncio.gather(
                *(pool.close_all() for _key, pool in pool_items),
                return_exceptions=True,
            )

        for (key, pool), result in zip(pool_items, results, strict=True):
            if not isinstance(result, BaseException) and self._pools.get(key) is pool:
                self._pools.pop(key, None)

        flush_error: BaseException | None = None
        if not self._store_flush_complete:
            flush = getattr(self._store, "flush", None)
            if callable(flush):
                try:
                    await cast(Callable[[], Awaitable[None]], flush)()
                except BaseException as exc:
                    flush_error = exc
                else:
                    self._store_flush_complete = True
            else:
                self._store_flush_complete = True

        for result in results:
            if isinstance(result, BaseException):
                raise result
        if flush_error is not None:
            raise flush_error

    @staticmethod
    async def _await_owned_cleanup(task: asyncio.Task[None]) -> None:
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
                    "Managed session cleanup failed while its caller was cancelled: %s",
                    exception_summary(cleanup_error),
                )
            raise asyncio.CancelledError
        if cleanup_error is not None:
            raise cleanup_error

    async def __aenter__(self) -> ManagedSession:
        if self._closed:
            raise ACPPoolClosedError("managed session is closed")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()
