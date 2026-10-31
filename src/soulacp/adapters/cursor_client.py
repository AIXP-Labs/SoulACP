"""Cursor CLI client using one cancellable subprocess per query."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import AsyncGenerator, Awaitable
from contextlib import suppress
from typing import cast

from soulacp._async import wait_for_owned
from soulacp._json import strict_json_loads
from soulacp.adapters.base_client import (
    _await_owned_cleanup,
    _inject_otel_context_into_env,
    _otel_propagation_enabled,
    _validate_json_values,
)
from soulacp.binary import find_cursor_binary
from soulacp.config import ACPConfig, _model_id_for_provider, _require_valid_unicode
from soulacp.environment import build_subprocess_environment, update_subprocess_environment
from soulacp.errors import (
    ACPPartialResponseError,
    ACPPromptTimeoutError,
    ACPProtocolError,
    ACPProviderError,
    ACPRequestOutcomeUnknownError,
    ACPResponseError,
    ACPStreamTimeoutError,
)
from soulacp.privacy import _raise_private_cause, exception_summary

logger: logging.Logger = logging.getLogger(__name__)
MAX_CURSOR_STDOUT_BYTES = 10 * 1024 * 1024
MAX_CURSOR_STDERR_BYTES = 1024 * 1024
MAX_CURSOR_RESPONSE_CHARS = 10 * 1024 * 1024
MAX_CURSOR_STREAM_BYTES = 50 * 1024 * 1024
MAX_CURSOR_STREAM_EVENTS = 65_536
MAX_CURSOR_RESPONSE_CHUNKS = 65_536
CURSOR_EXIT_DRAIN_TIMEOUT = 1.0


class CursorCLIClient:
    """Client for the non-ACP ``cursor-agent`` command.

    Cursor CLI has no persistent transport, but the client still exposes the
    same lease interface. Each query subprocess is tracked so pool shutdown or
    cancellation can terminate and await it.
    """

    def __init__(self, config: ACPConfig) -> None:
        self.config = config
        self._binary: str | None = None
        self._session_id: str | None = None
        self._last_used = time.monotonic()
        self._process: asyncio.subprocess.Process | None = None
        self._query_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._disconnect_task: asyncio.Task[None] | None = None
        self._spawn_task: asyncio.Task[asyncio.subprocess.Process] | None = None
        self._spawn_cleanup_task: asyncio.Task[None] | None = None

    @property
    def is_connected(self) -> bool:
        return self._binary is not None

    @property
    def is_idle_timeout(self) -> bool:
        return time.monotonic() - self._last_used > self.config.pool_idle_timeout

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        self._session_id = value

    async def ping(self) -> bool:
        """Return whether the Cursor CLI binary has been resolved."""
        return self._binary is not None

    async def connect(self) -> None:
        """Resolve the Cursor CLI and prepare this client for queries."""
        async with self._lifecycle_lock:
            if self._spawn_task is not None or self._spawn_cleanup_task is not None or self._process is not None:
                raise RuntimeError("Cursor CLI process cleanup is still pending")
            binary = find_cursor_binary()
            if not binary:
                raise FileNotFoundError("Cursor CLI not found. Install from: https://cursor.com")
            self._binary = binary

    async def disconnect(self) -> None:
        """Terminate any owned Cursor process and release client state."""
        task = self._disconnect_task
        if task is None or task.done():
            task = asyncio.create_task(self._run_disconnect())
            self._disconnect_task = task
        await _await_owned_cleanup(task, label="Cursor CLI cleanup")

    async def _run_disconnect(self) -> None:
        while True:
            async with self._lifecycle_lock:
                self._binary = None
                cleanup = self._spawn_cleanup_task
                if cleanup is None:
                    await self._disconnect_locked()
                    return
            await _await_owned_cleanup(cleanup, label="late Cursor CLI spawn cleanup")

    async def _disconnect_locked(self) -> None:
        self._binary = None
        process = self._process
        await self._terminate_owned_process(process)
        if self._process is process:
            self._process = None

    async def query(self, prompt: str) -> str:
        """Run a non-streaming query under the end-to-end prompt deadline."""
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        _require_valid_unicode(prompt, "prompt")
        async with self._query_lock:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.config.timeout_prompt
            cmd, temp_path = self._build_command(prompt, stream=False)
            process: asyncio.subprocess.Process | None = None
            try:
                process = await self._spawn_with_deadline(cmd, deadline)
                await self._write_prompt(process, prompt, deadline)
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise ACPPromptTimeoutError(self.config.timeout_prompt)
                try:
                    stdout, stderr = await wait_for_owned(
                        self._communicate_bounded(process),
                        remaining,
                    )
                except asyncio.TimeoutError as exc:
                    _raise_private_cause(ACPPromptTimeoutError(self.config.timeout_prompt), exc)

                error_text = stderr.decode("utf-8", errors="replace").strip()
                if process.returncode:
                    cause = ACPProviderError(
                        "Cursor CLI",
                        details=error_text,
                        returncode=process.returncode,
                    )
                    _raise_private_cause(ACPPartialResponseError(cause), cause)
                try:
                    output = stdout.decode("utf-8").strip()
                except UnicodeDecodeError as exc:
                    _raise_private_cause(ACPProtocolError("Cursor CLI json output is not valid UTF-8"), exc)
                try:
                    payload = strict_json_loads(output)
                    _validate_json_values(payload, "Cursor CLI JSON")
                except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                    _raise_private_cause(ACPProtocolError("Cursor CLI json output was malformed"), exc)
                if not isinstance(payload, dict) or payload.get("type") != "result":
                    raise ACPProtocolError("Cursor CLI json output is missing the terminal result event")
                self._remember_session(payload)
                if payload.get("is_error") or payload.get("subtype") != "success":
                    cause = ACPProviderError(
                        "Cursor CLI",
                        details=str(payload.get("result") or "Cursor CLI reported failure"),
                    )
                    _raise_private_cause(ACPPartialResponseError(cause), cause)
                result = payload.get("result")
                if not isinstance(result, str):
                    raise ACPProtocolError("Cursor CLI result event is missing string result text")
                return result
            except ACPResponseError:
                raise
            except Exception as exc:
                if process is not None:
                    _raise_private_cause(ACPRequestOutcomeUnknownError(exc), exc)
                raise
            finally:
                primary_error = sys.exc_info()[1]
                try:
                    try:
                        if process is not None:
                            cleanup = asyncio.create_task(self._finish_query_cleanup(process))
                            await _await_owned_cleanup(cleanup, label="Cursor CLI query cleanup")
                    except asyncio.CancelledError:
                        raise
                    except BaseException as exc:
                        if primary_error is None or isinstance(primary_error, GeneratorExit):
                            raise
                        logger.error(
                            "Cursor CLI query cleanup failed after query failure: %s",
                            exception_summary(exc),
                        )
                finally:
                    self._last_used = time.monotonic()
                    self._remove_temp_file(temp_path)

    async def query_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Stream deltas with separate prompt and inactivity deadlines."""
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        _require_valid_unicode(prompt, "prompt")
        async with self._query_lock:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.config.timeout_prompt
            cmd, temp_path = self._build_command(prompt, stream=True)
            process: asyncio.subprocess.Process | None = None
            stderr_task: asyncio.Task[bytes] | None = None
            process_wait_task: asyncio.Task[int] | None = None
            content_chunks: list[str] = []
            content_chars = 0
            stream_bytes = 0
            stream_events = 0
            terminal_error: BaseException | None = None
            saw_terminal_result = False
            saw_timestamped_assistant_delta = False

            def _partial_output() -> str:
                return "".join(content_chunks)

            def _record_delta(delta: str) -> None:
                nonlocal content_chars
                if not delta:
                    return
                if len(content_chunks) >= MAX_CURSOR_RESPONSE_CHUNKS:
                    raise ACPProtocolError(
                        f"Cursor CLI response exceeds the {MAX_CURSOR_RESPONSE_CHUNKS}-chunk limit",
                        partial_output=_partial_output(),
                    )
                if content_chars + len(delta) > MAX_CURSOR_RESPONSE_CHARS:
                    raise ACPProtocolError(
                        f"Cursor CLI response exceeds the {MAX_CURSOR_RESPONSE_CHARS}-character limit",
                        partial_output=_partial_output(),
                    )
                content_chunks.append(delta)
                content_chars += len(delta)

            try:
                process = await self._spawn_with_deadline(cmd, deadline)
                await self._write_prompt(process, prompt, deadline)
                stdout = process.stdout
                stderr = process.stderr
                if stdout is None or stderr is None:
                    raise ACPProtocolError("Cursor CLI subprocess pipes are unavailable")
                stderr_task = asyncio.create_task(self._read_bounded(stderr, MAX_CURSOR_STDERR_BYTES, "stderr"))
                process_wait_task = asyncio.create_task(process.wait())
                exit_drain_budget: float | None = None
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        terminal_error = ACPPromptTimeoutError(
                            self.config.timeout_prompt,
                            partial_output=_partial_output(),
                        )
                        break
                    stream_timeout = float(self.config.timeout_stream)
                    prompt_deadline_is_first = remaining <= stream_timeout
                    read_timeout = min(stream_timeout, remaining)
                    read_task = asyncio.create_task(stdout.readline())
                    try:
                        done, _pending = await asyncio.wait(
                            {read_task, process_wait_task},
                            timeout=read_timeout,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if process_wait_task in done:
                            process_wait_task.result()
                            if exit_drain_budget is None:
                                exit_drain_budget = CURSOR_EXIT_DRAIN_TIMEOUT
                        if read_task not in done and process_wait_task in done:
                            drain_budget = exit_drain_budget
                            if drain_budget is not None and drain_budget > 0:
                                drain_started = loop.time()
                                done, _pending = await asyncio.wait(
                                    {read_task},
                                    timeout=min(drain_budget, deadline - drain_started),
                                )
                                exit_drain_budget = max(0.0, drain_budget - (loop.time() - drain_started))
                        if read_task not in done:
                            if exit_drain_budget is not None:
                                terminal_error = ACPProtocolError(
                                    "Cursor CLI stream pipe remained open after subprocess exit",
                                    partial_output=_partial_output(),
                                )
                            elif prompt_deadline_is_first:
                                terminal_error = ACPPromptTimeoutError(
                                    self.config.timeout_prompt,
                                    partial_output=_partial_output(),
                                )
                            else:
                                terminal_error = ACPStreamTimeoutError(
                                    self.config.timeout_stream,
                                    partial_output=_partial_output(),
                                )
                            break
                        line = read_task.result()
                    finally:
                        if not read_task.done():
                            read_task.cancel()
                        await asyncio.gather(read_task, return_exceptions=True)
                    if terminal_error is not None:
                        break
                    if not line:
                        break

                    stream_bytes += len(line)
                    if stream_bytes > MAX_CURSOR_STREAM_BYTES:
                        terminal_error = ACPProtocolError(
                            f"Cursor CLI stream exceeds the {MAX_CURSOR_STREAM_BYTES}-byte limit",
                            partial_output=_partial_output(),
                        )
                        break

                    try:
                        text = line.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        terminal_error = ACPProtocolError(
                            "Cursor CLI emitted stream JSON that is not valid UTF-8",
                            partial_output=_partial_output(),
                        )
                        break
                    if not text:
                        continue
                    stream_events += 1
                    if stream_events > MAX_CURSOR_STREAM_EVENTS:
                        terminal_error = ACPProtocolError(
                            f"Cursor CLI stream exceeds the {MAX_CURSOR_STREAM_EVENTS}-event limit",
                            partial_output=_partial_output(),
                        )
                        break

                    try:
                        data = strict_json_loads(text)
                        _validate_json_values(data, "Cursor CLI stream JSON")
                    except (json.JSONDecodeError, ValueError, RecursionError):
                        terminal_error = ACPProtocolError(
                            "Cursor CLI emitted malformed stream JSON",
                            partial_output=_partial_output(),
                        )
                        break
                    if not isinstance(data, dict):
                        terminal_error = ACPProtocolError(
                            "Cursor CLI stream event must be a JSON object",
                            partial_output=_partial_output(),
                        )
                        break
                    self._remember_session(data)
                    event_type = data.get("type")
                    if event_type == "result":
                        saw_terminal_result = True
                        if data.get("is_error") or data.get("subtype") != "success":
                            cause = ACPProviderError(
                                "Cursor CLI",
                                details=str(data.get("result") or "Cursor CLI reported failure"),
                            )
                            terminal_error = ACPPartialResponseError(
                                cause,
                                partial_output=_partial_output(),
                            )
                            break
                        final_text = data.get("result")
                        if not isinstance(final_text, str):
                            terminal_error = ACPProtocolError(
                                "Cursor CLI result event is missing string result text",
                                partial_output=_partial_output(),
                            )
                            break

                        full_content = _partial_output()
                        if final_text.startswith(full_content):
                            delta = final_text[len(full_content) :]
                        elif not full_content:
                            delta = final_text
                        else:
                            terminal_error = ACPProtocolError(
                                "Cursor CLI terminal result disagrees with streamed assistant text",
                                partial_output=full_content,
                            )
                            break
                        if delta:
                            _record_delta(delta)
                            yielded_at = loop.time()
                            try:
                                yield delta
                            finally:
                                # Exclude downstream processing time from the
                                # subprocess generation deadline.
                                deadline += loop.time() - yielded_at
                        break

                    if event_type == "assistant":
                        # With --stream-partial-output, Cursor also emits
                        # buffered assistant snapshots before tools and at the
                        # end. Only timestamped, non-model-call events are live
                        # deltas once that event shape has appeared.
                        if "model_call_id" in data:
                            continue
                        if "timestamp_ms" in data:
                            saw_timestamped_assistant_delta = True
                        elif saw_timestamped_assistant_delta:
                            continue

                    accumulated = ""
                    if event_type != "assistant" and isinstance(data.get("content"), str):
                        accumulated = _partial_output()
                    delta = self._extract_assistant_delta(data, accumulated)
                    if delta:
                        _record_delta(delta)
                        yielded_at = loop.time()
                        try:
                            yield delta
                        finally:
                            deadline += loop.time() - yielded_at

                if terminal_error is None and not saw_terminal_result:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        terminal_error = ACPPromptTimeoutError(
                            self.config.timeout_prompt,
                            partial_output=_partial_output(),
                        )
                    else:
                        try:
                            if process_wait_task is None:
                                raise ACPProtocolError("Cursor CLI process wait was not initialized")
                            process_exit_task = cast(asyncio.Task[int], process_wait_task)
                            await wait_for_owned(
                                asyncio.shield(process_exit_task),
                                min(1.0, remaining),
                            )
                        except asyncio.TimeoutError:
                            terminal_error = ACPProtocolError(
                                "Cursor CLI stdout closed before a terminal result event",
                                partial_output=_partial_output(),
                            )

                if terminal_error is not None:
                    raise terminal_error

                if saw_terminal_result:
                    return

                if stderr_task is None:
                    raise ACPProtocolError(
                        "Cursor CLI stderr capture was not initialized",
                        partial_output=_partial_output(),
                    )
                try:
                    stderr_bytes = await stderr_task
                except ACPProtocolError as exc:
                    _raise_private_cause(ACPProtocolError(str(exc), partial_output=_partial_output()), exc)
                stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
                if process.returncode:
                    cause = ACPProviderError(
                        "Cursor CLI",
                        details=stderr,
                        returncode=process.returncode,
                    )
                    _raise_private_cause(ACPPartialResponseError(cause, partial_output=_partial_output()), cause)
                raise ACPProtocolError(
                    "Cursor CLI stream ended without a terminal result event",
                    partial_output=_partial_output(),
                )
            except ACPResponseError:
                raise
            except Exception as exc:
                if process is not None:
                    _raise_private_cause(ACPRequestOutcomeUnknownError(exc, partial_output=_partial_output()), exc)
                raise
            finally:
                primary_error = sys.exc_info()[1]
                try:
                    try:
                        if process is not None or stderr_task is not None:
                            cleanup_wait_task = cast(asyncio.Task[int] | None, process_wait_task)
                            cleanup = asyncio.create_task(
                                self._finish_query_cleanup(process, stderr_task, cleanup_wait_task)
                            )
                            await _await_owned_cleanup(cleanup, label="Cursor CLI stream cleanup")
                    except asyncio.CancelledError:
                        raise
                    except BaseException as exc:
                        if primary_error is None or isinstance(primary_error, GeneratorExit):
                            raise
                        logger.error(
                            "Cursor CLI stream cleanup failed after query failure: %s",
                            exception_summary(exc),
                        )
                finally:
                    self._last_used = time.monotonic()
                    self._remove_temp_file(temp_path)

    async def resume(self, session_id: str) -> bool:
        """Bind the next Cursor query to an existing logical session."""
        if not isinstance(session_id, str) or not session_id:
            return False
        _require_valid_unicode(session_id, "session_id")
        async with self._query_lock:
            self._session_id = session_id
            return True

    def _build_command(self, prompt: str, *, stream: bool) -> tuple[list[str], str | None]:
        # The prompt is sent over stdin so it cannot be read from the process
        # command line by local process-inspection tools.
        del prompt
        binary = self._binary or find_cursor_binary()
        if not binary:
            raise FileNotFoundError("Cursor CLI not found")

        model_id = _model_id_for_provider(self.config.model, self.config.provider)
        output_format = "stream-json" if stream else "json"
        cmd = [binary, "-p"]
        if model_id.lower() != "default":
            cmd.extend(["--model", model_id])
        cmd.extend(["--output-format", output_format])
        if stream:
            cmd.append("--stream-partial-output")
        if (
            self.config.auto_approve_permissions
            and "--force" not in self.config.extra_args
            and "-f" not in self.config.extra_args
        ):
            cmd.append("--force")
        if self.config.extra_args:
            cmd.extend(self.config.extra_args)
        if self._session_id:
            cmd.extend(["--resume", self._session_id])
        return cmd, None

    def _remember_session(self, event: dict) -> None:
        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id:
            self._session_id = session_id

    @staticmethod
    def _extract_assistant_delta(event: dict, accumulated: str) -> str:
        if event.get("type") == "assistant":
            message = event.get("message")
            if not isinstance(message, dict):
                return ""
            content = message.get("content")
            if not isinstance(content, list):
                return ""
            return "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
            )

        # Compatibility with older wrappers that emitted cumulative text at
        # the top level instead of Cursor's documented assistant events.
        content = event.get("content")
        if isinstance(content, str) and content.startswith(accumulated):
            return content[len(accumulated) :]
        return ""

    async def _spawn(self, cmd: list[str]) -> asyncio.subprocess.Process:
        env = build_subprocess_environment(self.config.inherit_parent_env)
        if _otel_propagation_enabled():
            _inject_otel_context_into_env(env)
        if self.config.extra_env:
            update_subprocess_environment(env, self.config.extra_env)
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.cwd or None,
            env=env,
            limit=10 * 1024 * 1024,
        )

    async def _write_prompt(
        self,
        process: asyncio.subprocess.Process,
        prompt: str,
        deadline: float,
    ) -> None:
        stdin = process.stdin
        if stdin is None:
            raise ACPProtocolError("Cursor CLI subprocess stdin is unavailable")
        loop = asyncio.get_running_loop()
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise ACPPromptTimeoutError(self.config.timeout_prompt)
        try:
            stdin.write(prompt.encode("utf-8"))
            await wait_for_owned(stdin.drain(), remaining)
        except asyncio.TimeoutError as exc:
            _raise_private_cause(ACPPromptTimeoutError(self.config.timeout_prompt), exc)
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise ConnectionError("Cursor CLI closed stdin before accepting the prompt") from exc
        finally:
            stdin.close()
            wait_closed = getattr(stdin, "wait_closed", None)
            if callable(wait_closed):
                remaining = deadline - loop.time()
                if remaining > 0:
                    with suppress(asyncio.TimeoutError, BrokenPipeError, ConnectionResetError, OSError):
                        await wait_for_owned(
                            cast(Awaitable[None], wait_closed()),
                            min(1.0, remaining),
                        )

    @staticmethod
    async def _read_bounded(reader: asyncio.StreamReader, limit: int, label: str) -> bytes:
        data = bytearray()
        while True:
            chunk = await reader.read(min(64 * 1024, limit + 1 - len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > limit:
                raise ACPProtocolError(f"Cursor CLI {label} exceeds the {limit}-byte client limit")

    async def _communicate_bounded(self, process: asyncio.subprocess.Process) -> tuple[bytes, bytes]:
        stdout = process.stdout
        stderr = process.stderr
        if stdout is None or stderr is None:
            raise ACPProtocolError("Cursor CLI subprocess pipes are unavailable")
        stdout_task = asyncio.create_task(self._read_bounded(stdout, MAX_CURSOR_STDOUT_BYTES, "stdout"))
        stderr_task = asyncio.create_task(self._read_bounded(stderr, MAX_CURSOR_STDERR_BYTES, "stderr"))
        readers = asyncio.gather(stdout_task, stderr_task)
        wait_task = asyncio.create_task(process.wait())
        try:
            done, _pending = await asyncio.wait({readers, wait_task}, return_when=asyncio.FIRST_COMPLETED)
            if readers in done:
                stdout_bytes, stderr_bytes = readers.result()
                await wait_task
                return stdout_bytes, stderr_bytes

            try:
                stdout_bytes, stderr_bytes = await wait_for_owned(
                    asyncio.shield(readers),
                    CURSOR_EXIT_DRAIN_TIMEOUT,
                )
            except asyncio.TimeoutError as exc:
                _raise_private_cause(
                    ACPProtocolError("Cursor CLI output pipes remained open after subprocess exit"), exc
                )
            return stdout_bytes, stderr_bytes
        finally:
            # gather() does not cancel sibling awaitables when one reader
            # fails. Own the concrete reader tasks as well as the aggregate so
            # a bounded-read error cannot leave the other pipe reader behind.
            for task in (stdout_task, stderr_task, readers, wait_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                stdout_task,
                stderr_task,
                readers,
                wait_task,
                return_exceptions=True,
            )

    async def _spawn_with_deadline(
        self,
        cmd: list[str],
        deadline: float,
    ) -> asyncio.subprocess.Process:
        async with self._lifecycle_lock:
            if self._binary is None:
                raise ConnectionError("Cursor CLI client is disconnected")
            if self._spawn_task is not None or self._spawn_cleanup_task is not None:
                raise RuntimeError("Cursor CLI subprocess spawn cleanup is still pending")
            if self._process is not None:
                raise RuntimeError("Cursor CLI previous query process cleanup is still pending")
            loop = asyncio.get_running_loop()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ACPPromptTimeoutError(self.config.timeout_prompt)
            task = asyncio.create_task(self._spawn(cmd))
            self._spawn_task = task
            try:
                process = await wait_for_owned(
                    asyncio.shield(task),
                    min(self.config.timeout_connect, remaining),
                )
            except asyncio.TimeoutError as exc:
                self._start_late_spawn_cleanup(task)
                if deadline - loop.time() <= 0:
                    _raise_private_cause(ACPPromptTimeoutError(self.config.timeout_prompt), exc)
                raise ConnectionError(f"Cursor CLI startup exceeded {self.config.timeout_connect:g}s") from exc
            except BaseException:
                self._start_late_spawn_cleanup(task)
                raise
            if self._spawn_task is task:
                self._spawn_task = None
            self._process = process
            return process

    def _start_late_spawn_cleanup(self, task: asyncio.Task[asyncio.subprocess.Process]) -> None:
        cleanup = asyncio.create_task(self._claim_and_reap_late_spawn(task))
        self._spawn_cleanup_task = cleanup

        def _consume_cleanup_result(done: asyncio.Task[None]) -> None:
            if not done.cancelled():
                with suppress(Exception):
                    done.exception()

        cleanup.add_done_callback(_consume_cleanup_result)

    async def _claim_and_reap_late_spawn(
        self,
        task: asyncio.Task[asyncio.subprocess.Process],
    ) -> None:
        """Claim a Cursor child that appears after query startup was interrupted."""
        cleanup_task = asyncio.current_task()
        try:
            process = await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.cancelled():
                raise
            if self._spawn_task is task:
                self._spawn_task = None
            if self._spawn_cleanup_task is cleanup_task:
                self._spawn_cleanup_task = None
            return
        except BaseException as exc:
            logger.debug("Late Cursor CLI spawn failed: %s", exception_summary(exc))
            if self._spawn_task is task:
                self._spawn_task = None
            if self._spawn_cleanup_task is cleanup_task:
                self._spawn_cleanup_task = None
            return
        try:
            async with self._lifecycle_lock:
                self._process = process
                await self._terminate_owned_process(process)
                if self._process is process:
                    self._process = None
        except BaseException as exc:
            logger.error("Late Cursor CLI subprocess cleanup failed: %s", exception_summary(exc))
            raise
        finally:
            if self._spawn_task is task:
                self._spawn_task = None
            if self._spawn_cleanup_task is cleanup_task:
                self._spawn_cleanup_task = None

    async def _terminate_process(
        self,
        process: asyncio.subprocess.Process | None = None,
    ) -> None:
        async with self._lifecycle_lock:
            proc = process or self._process
            await self._terminate_owned_process(proc)
            if self._process is proc:
                self._process = None

    async def _finish_query_cleanup(
        self,
        process: asyncio.subprocess.Process | None,
        stderr_task: asyncio.Task[bytes] | None = None,
        wait_task: asyncio.Task[int] | None = None,
    ) -> None:
        """Finish child and reader cleanup even when the query caller is cancelled."""
        try:
            if process is not None:
                await self._terminate_process(process)
        finally:
            if stderr_task is not None:
                if not stderr_task.done():
                    stderr_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await stderr_task
            if wait_task is not None:
                if not wait_task.done():
                    wait_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await wait_task

    @staticmethod
    async def _terminate_owned_process(proc: asyncio.subprocess.Process | None) -> None:
        if proc is None:
            return
        termination_error: BaseException | None = None
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError, PermissionError, OSError) as exc:
                termination_error = exc
        if proc.returncode is None:
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError, PermissionError, OSError) as exc:
                termination_error = exc
        transport = getattr(proc, "_transport", None)
        if transport:
            with suppress(Exception):
                transport.close()
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except (asyncio.TimeoutError, ProcessLookupError, PermissionError, OSError) as exc:
                termination_error = exc
        if proc.returncode is None:
            raise RuntimeError("Cursor CLI subprocess did not exit after terminate/kill") from termination_error

    @staticmethod
    def _remove_temp_file(path: str | None) -> None:
        if path:
            with suppress(OSError):
                os.unlink(path)
