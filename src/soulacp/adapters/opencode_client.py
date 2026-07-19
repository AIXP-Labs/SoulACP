"""OpenCode-specific ACP client with Windows compatibility."""

from __future__ import annotations

import asyncio
import json
import logging
import os

# OpenCode uses an argv-only child-process transport; shell execution is never enabled.
import subprocess  # nosec B404
import sys
import threading
import time
from contextlib import suppress
from typing import Any

from soulacp._async import wait_for_owned
from soulacp._json import strict_json_loads
from soulacp.adapters.base_client import (
    ACPClientBase,
    RPCError,
    _await_owned_cleanup,
    _encode_json_line,
    _finalize_rpc_future,
    _inject_otel_context_into_env,
    _otel_propagation_enabled,
    _SessionStateSnapshot,
    _validate_json_values,
)
from soulacp.binary import find_opencode_binary
from soulacp.config import ACPConfig
from soulacp.environment import (
    build_subprocess_environment,
    get_subprocess_environment_value,
    update_subprocess_environment,
)
from soulacp.errors import ACPProtocolError, ACPRequestOutcomeUnknownError, ACPResponseError
from soulacp.meta import CLIENT_NAME, __version__
from soulacp.privacy import exception_summary, sensitive_logging_enabled

logger: logging.Logger = logging.getLogger(__name__)
MAX_RPC_FRAME_BYTES = 10 * 1024 * 1024
MAX_STDERR_LINE_BYTES = 64 * 1024
MAX_OPENCODE_DISPATCH_BACKLOG = 4096


class OpenCodeACPClient(ACPClientBase):
    """ACP client for OpenCode CLI with Windows compatibility.

    Key differences from Claude/Gemini:
    - Uses ``subprocess.Popen`` (synchronous) + thread-based reader
      for Windows compatibility (asyncio stdin is unreliable on Windows)
    - Model passed via ``OPENCODE_CONFIG_CONTENT`` environment variable
    - Uses ``CREATE_NO_WINDOW`` flag on Windows
    - stdin writes via ``os.write(fileno())`` on Windows
    """

    def __init__(self, config: ACPConfig) -> None:
        super().__init__(config)
        self._popen: subprocess.Popen | None = None
        self._spawn_task: asyncio.Task[subprocess.Popen | None] | None = None
        self._spawn_cleanup_task: asyncio.Task[None] | None = None
        self._spawn_abandoned: threading.Event | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dispatch_queue: asyncio.Queue[dict[str, Any] | BaseException | None] | None = None
        self._dispatch_task: asyncio.Task[None] | None = None
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._write_tasks: set[asyncio.Task[None]] = set()
        self._dispatch_schedule_lock = threading.Lock()
        self._scheduled_dispatches = 0
        self._dispatch_overflow = False

    @property
    def is_connected(self) -> bool:
        if self._popen is not None:
            return self._connected and self._popen.poll() is None
        return super().is_connected

    def _get_acp_command(self) -> list[str]:
        """Return command to start OpenCode CLI in ACP mode."""
        binary = find_opencode_binary()
        if not binary:
            raise FileNotFoundError("OpenCode CLI not found. Install from: https://opencode.ai")
        return [binary, "acp"]

    async def connect(self) -> None:
        """Start and initialize OpenCode under one end-to-end deadline."""
        try:
            await wait_for_owned(self._connect_once(), self.config.timeout_connect)
        except asyncio.TimeoutError as exc:
            try:
                await self.disconnect()
            except asyncio.CancelledError:
                pass
            except BaseException as cleanup_exc:
                logger.error("OpenCode startup-timeout cleanup failed: %s", exception_summary(cleanup_exc))
            raise ConnectionError(f"OpenCode startup exceeded {self.config.timeout_connect:g}s") from exc
        except asyncio.CancelledError as cancelled:
            try:
                await self.disconnect()
            except asyncio.CancelledError:
                pass
            except BaseException as cleanup_exc:
                logger.error("OpenCode startup-cancellation cleanup failed: %s", exception_summary(cleanup_exc))
            raise cancelled

    async def _connect(self) -> None:
        if self.is_connected:
            raise RuntimeError("OpenCode client is already connected")
        if (
            self._popen is not None
            or self._spawn_task is not None
            or self._spawn_cleanup_task is not None
            or self._write_tasks
        ):
            await self._disconnect_without_connect_lock()
        try:
            await self._start_and_initialize()
        except BaseException:
            cleanup = asyncio.create_task(self._disconnect_without_connect_lock())
            with suppress(BaseException):
                await _await_owned_cleanup(cleanup, label="failed OpenCode connect cleanup")
            raise

    async def _start_and_initialize(self) -> None:
        self._reset_protocol_state()
        await self._prepare_host_services()
        cmd = self._get_acp_command()
        self._command_identity = cmd[0]
        if self.config.extra_args:
            cmd = cmd + list(self.config.extra_args)

        env = build_subprocess_environment(self.config.inherit_parent_env)
        update_subprocess_environment(
            env,
            {
                "HEADLESS": "true",
                "FORCE_COLOR": "0",
                "NO_COLOR": "1",
            },
        )
        if _otel_propagation_enabled():
            _inject_otel_context_into_env(env)
        if self.config.extra_env:
            update_subprocess_environment(env, self.config.extra_env)

        # Merge model into existing OPENCODE_CONFIG_CONTENT (don't overwrite)
        oc_config = {}
        existing = get_subprocess_environment_value(env, "OPENCODE_CONFIG_CONTENT")
        if existing:
            try:
                parsed = strict_json_loads(existing)
                if not isinstance(parsed, dict):
                    raise ValueError("root must be an object")
                _validate_json_values(parsed, "OPENCODE_CONFIG_CONTENT")
            except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
                raise ValueError("OPENCODE_CONFIG_CONTENT must be a strict JSON object") from exc
            oc_config = parsed
        model_id = self._configured_model_id()
        if model_id:
            oc_config["model"] = model_id
        update_subprocess_environment(
            env,
            {"OPENCODE_CONFIG_CONTENT": json.dumps(oc_config, ensure_ascii=False, allow_nan=False)},
        )

        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        await self._spawn_popen(cmd, env, kwargs)
        self._connected = True
        self._last_used = time.monotonic()
        self._loop = asyncio.get_running_loop()
        with self._dispatch_schedule_lock:
            self._scheduled_dispatches = 0
            self._dispatch_overflow = False
        self._dispatch_queue = asyncio.Queue()
        self._dispatch_task = asyncio.create_task(self._dispatch_messages())

        # Start synchronous reader threads only after process ownership transfers.
        self._reader_thread = threading.Thread(target=self._sync_read_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._sync_read_stderr, daemon=True)
        self._stderr_thread.start()

        await self._initialize()
        if not self.is_connected:
            raise ConnectionError("OpenCode subprocess disconnected during initialization")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ACPProtocolError("OpenCode initialization did not establish a non-empty sessionId")
        logger.info("OpenCode session established")

    async def _spawn_popen(self, cmd: list[str], env: dict[str, str], kwargs: dict[str, Any]) -> subprocess.Popen:
        """Spawn off-loop and reap a process that completes after cancellation."""
        abandoned = threading.Event()
        self._spawn_abandoned = abandoned

        def _spawn() -> subprocess.Popen:
            # cmd is an argv list and subprocess keeps its default shell=False.
            return subprocess.Popen(  # nosec B603
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self.config.cwd or None,
                **kwargs,
            )

        task = asyncio.create_task(asyncio.to_thread(_spawn))
        self._spawn_task = task

        def _cleanup_late_spawn(done: asyncio.Task[subprocess.Popen]) -> None:
            async def _claim_and_reap() -> None:
                cleanup_task = asyncio.current_task()
                try:
                    proc = done.result()
                except BaseException:
                    if self._spawn_task is done:
                        self._spawn_task = None
                    if self._spawn_abandoned is abandoned:
                        self._spawn_abandoned = None
                    if self._spawn_cleanup_task is cleanup_task:
                        self._spawn_cleanup_task = None
                    return
                self._popen = proc
                try:
                    await asyncio.to_thread(self._terminate_popen_sync, proc)
                except BaseException as exc:
                    # Keep the handle on the client. A pool-owned disconnect
                    # retry can then reap it instead of losing ownership in a
                    # detached daemon thread.
                    logger.error(
                        "Late OpenCode subprocess cleanup failed: %s",
                        exception_summary(exc),
                    )
                else:
                    if self._popen is proc:
                        self._popen = None
                finally:
                    if self._spawn_task is done:
                        self._spawn_task = None
                    if self._spawn_abandoned is abandoned:
                        self._spawn_abandoned = None
                    if self._spawn_cleanup_task is cleanup_task:
                        self._spawn_cleanup_task = None

            cleanup = asyncio.create_task(_claim_and_reap())
            self._spawn_cleanup_task = cleanup

            def _consume_cleanup_result(cleanup_done: asyncio.Task[None]) -> None:
                if not cleanup_done.cancelled():
                    with suppress(Exception):
                        cleanup_done.exception()

            cleanup.add_done_callback(_consume_cleanup_result)

        try:
            proc = await asyncio.shield(task)
        except BaseException:
            abandoned.set()
            task.add_done_callback(_cleanup_late_spawn)
            raise

        self._spawn_task = None
        self._spawn_abandoned = None
        if abandoned.is_set():
            await asyncio.to_thread(self._terminate_popen_sync, proc)
            raise ConnectionError("OpenCode startup was cancelled before process ownership transferred")

        # No await between the cancellation check and assignment: disconnect()
        # therefore either marks this spawn abandoned or owns this process.
        self._popen = proc
        return proc

    @staticmethod
    def _terminate_popen_sync(proc: subprocess.Popen) -> None:
        """Terminate, reap, and close a synchronous Popen without blocking the loop."""
        termination_error: BaseException | None = None
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception as exc:
                termination_error = exc
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception as exc:
                termination_error = exc
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream:
                with suppress(Exception):
                    stream.close()
        if proc.poll() is None:
            try:
                proc.wait(timeout=1)
            except Exception as exc:
                termination_error = exc
        if proc.poll() is None:
            raise RuntimeError("OpenCode subprocess did not exit after terminate/kill") from termination_error

    async def _initialize(self) -> None:
        """Perform OpenCode protocol handshake and create a session."""
        await self._rpc(
            "initialize",
            {
                "clientInfo": {"name": CLIENT_NAME, "version": __version__},
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                    "terminal": True,
                },
            },
            timeout=self.config.timeout_connect,
        )

        res = await self._rpc(
            "session/new",
            {
                "cwd": self.config.cwd or os.getcwd(),
                "mcpServers": [],
            },
            timeout=self.config.timeout_connect,
        )
        self.session_id = res.get("sessionId") or res.get("session_id")
        if not self.session_id:
            raise ConnectionError("session/new did not return sessionId")

    async def resume(self, session_id: str) -> bool:
        """Resume a previous OpenCode session via session/load.

        OpenCode supports session/load in ACP 1.0 protocol. Returns True
        on success, False if the session can't be restored (e.g. expired
        or unknown to the backend). Transport and protocol failures raise.
        """
        return await self._resume_if_available("session/load", session_id, agent_name="OpenCode")

    async def ping(self) -> bool:
        """Check local Popen state without writing a non-ACP probe frame."""
        proc = self._popen
        stdin = proc.stdin if proc is not None else None
        return self.is_connected and stdin is not None and not getattr(stdin, "closed", False)

    async def _rpc(self, method: str, params: dict, timeout: float | None = None) -> Any:
        """Send JSON-RPC via synchronous stdin write."""
        if not self.is_connected:
            raise ConnectionError("OpenCode not connected")

        params = self._normalize_rpc_params(method, params)
        params = self._apply_configured_session_params(method, params)

        if timeout is None:
            timeout = self.config.timeout_connect

        self._msg_id += 1
        mid = self._msg_id
        msg = _encode_json_line(
            {
                "jsonrpc": "2.0",
                "id": mid,
                "method": method,
                "params": params,
            }
        )

        future = asyncio.get_running_loop().create_future()
        with self._pending_lock:
            self._pending[mid] = future
            self._inflight[mid] = (method, time.monotonic())
        prompt_write_started = False
        transition_id: str | None = None
        previous_session_state: _SessionStateSnapshot | None = None
        transition_succeeded = False
        request_write_started = False
        with self._otel_rpc_span(method, mid):
            try:
                transition_id, previous_session_state = self._begin_rpc_lifecycle(method, params)
                prompt_write_started = method == "session/prompt"
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout
                request_write_started = True
                await self._write_with_timeout(msg, timeout)
                result = await self._await_rpc_future(future, deadline)
                self._complete_rpc_lifecycle(method, result, transition_id)
                transition_succeeded = True
                return result
            except asyncio.CancelledError:
                await self._notify_request_cancel(mid)
                if request_write_started:
                    await self._invalidate_uncertain_rpc_transport(method)
                raise
            except asyncio.TimeoutError:
                await self._notify_request_cancel(mid)
                if request_write_started:
                    await self._invalidate_uncertain_rpc_transport(method)
                raise
            except RPCError:
                raise
            except ACPResponseError:
                if request_write_started:
                    await self._invalidate_uncertain_rpc_transport(method)
                raise
            except Exception as exc:
                if request_write_started:
                    await self._invalidate_uncertain_rpc_transport(method)
                if prompt_write_started:
                    raise ACPRequestOutcomeUnknownError(exc) from exc
                raise
            finally:
                self._finish_rpc_lifecycle(transition_id, previous_session_state, transition_succeeded)
                _finalize_rpc_future(future)
                with self._pending_lock:
                    self._pending.pop(mid, None)
                    self._inflight.pop(mid, None)

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        if not self.is_connected:
            raise ConnectionError("OpenCode not connected")
        msg = _encode_json_line(
            {
                "jsonrpc": "2.0",
                "method": method,
                **({"params": params} if params else {}),
            }
        )
        await self._write_with_timeout(msg, self.config.timeout_connect)

    async def _write_with_timeout(self, data: bytes, timeout: float) -> None:
        """Bound a pipe write while retaining ownership of its worker thread."""
        task = asyncio.create_task(asyncio.to_thread(self._sync_write, data))
        self._write_tasks.add(task)

        def _write_done(done: asyncio.Task[None]) -> None:
            self._write_tasks.discard(done)
            if not done.cancelled():
                with suppress(Exception):
                    done.exception()

        task.add_done_callback(_write_done)
        done, _pending = await asyncio.wait({task}, timeout=timeout)
        if task not in done:
            # Cancelling asyncio.to_thread() cannot stop the running thread.
            # Keep the task tracked so disconnect can terminate the pipe owner
            # and verify that the physical write has actually returned.
            raise asyncio.TimeoutError
        task.result()

    def _sync_write(self, data: bytes) -> None:
        """Write to stdin using os.write on Windows for reliability."""
        with self._write_lock:
            proc = self._popen
            if not self._connected or proc is None or proc.poll() is not None or not proc.stdin:
                raise ConnectionError("OpenCode stdin is unavailable")
            if sys.platform == "win32":
                view = memoryview(data)
                while view:
                    written = os.write(proc.stdin.fileno(), view)
                    if written <= 0:
                        raise BrokenPipeError("OpenCode stdin accepted no data")
                    view = view[written:]
            else:
                view = memoryview(data)
                while view:
                    written = proc.stdin.write(view)
                    if written is None or written <= 0:
                        raise BrokenPipeError("OpenCode stdin accepted no data")
                    view = view[written:]
                proc.stdin.flush()

    def _sync_read_loop(self) -> None:
        """Synchronous stdout reader running in a thread."""
        failure: BaseException | None = None
        try:
            proc = self._popen
            if proc is None or proc.stdout is None:
                return
            while True:
                line = proc.stdout.readline(MAX_RPC_FRAME_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_RPC_FRAME_BYTES:
                    failure = ACPProtocolError(f"OpenCode JSON-RPC frame exceeds the {MAX_RPC_FRAME_BYTES}-byte limit")
                    break
                try:
                    text = line.decode("utf-8").strip()
                except UnicodeDecodeError as exc:
                    raise ACPProtocolError("OpenCode stdout frame is not valid UTF-8") from exc
                if not text:
                    continue

                msg = self._parse_json(text)
                if msg is not None:
                    self._schedule_dispatch(msg)
        except Exception as e:
            failure = (
                e
                if isinstance(e, ACPResponseError)
                else ACPProtocolError(f"OpenCode read loop failed: {exception_summary(e, include_sensitive=False)}")
            )
            logger.warning("OpenCode read loop ended unexpectedly: %s", exception_summary(e))
        finally:
            if failure is not None:
                self._schedule_dispatch(failure)
            self._schedule_dispatch(None)

    def _schedule_dispatch(self, message: dict[str, Any] | BaseException | None) -> None:
        """Append a frame to the event-loop queue without unbounded backlog."""
        loop = self._loop
        queue = self._dispatch_queue
        if loop is None or loop.is_closed() or queue is None:
            return

        scheduled_message = message
        with self._dispatch_schedule_lock:
            if self._dispatch_overflow:
                return
            if self._scheduled_dispatches >= MAX_OPENCODE_DISPATCH_BACKLOG:
                self._dispatch_overflow = True
                scheduled_message = ACPProtocolError(
                    f"OpenCode dispatch backlog exceeds the {MAX_OPENCODE_DISPATCH_BACKLOG}-frame limit"
                )
            self._scheduled_dispatches += 1

        try:
            loop.call_soon_threadsafe(self._enqueue_dispatch, queue, scheduled_message)
        except RuntimeError:
            self._mark_dispatch_consumed()

    def _enqueue_dispatch(
        self,
        expected_queue: asyncio.Queue[dict[str, Any] | BaseException | None],
        message: dict[str, Any] | BaseException | None,
    ) -> None:
        if self._dispatch_queue is not expected_queue:
            self._mark_dispatch_consumed()
            return
        expected_queue.put_nowait(message)

    def _mark_dispatch_consumed(self) -> None:
        with self._dispatch_schedule_lock:
            self._scheduled_dispatches = max(0, self._scheduled_dispatches - 1)

    async def _dispatch_messages(self) -> None:
        """Dispatch reader-thread frames serially; EOF follows prior frames."""
        failure: BaseException | None = None
        try:
            while self._dispatch_queue is not None:
                message = await self._dispatch_queue.get()
                self._mark_dispatch_consumed()
                if message is None:
                    break
                if isinstance(message, BaseException):
                    failure = message
                    break
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = exc
            logger.warning("OpenCode dispatch loop ended unexpectedly: %s", exception_summary(exc))
        finally:
            self._handle_reader_disconnect(failure)

    def _handle_reader_disconnect(self, failure: BaseException | None = None) -> None:
        """Resolve reader shutdown on the event-loop thread."""
        if self._connected:
            logger.warning(
                "OpenCode subprocess disconnected (pending_rpcs=%d, session_present=%s)",
                len(self._pending),
                bool(self.session_id),
            )
        self._connected = False
        with self._pending_lock:
            prompt_was_pending = any(method == "session/prompt" for method, _started in self._inflight.values())
            pending = list(self._pending.values())
            self._pending.clear()
            self._inflight.clear()
        for future in pending:
            if not future.done():
                future.set_exception(failure or ConnectionError("OpenCode disconnected"))
        if prompt_was_pending and self._streaming and self._stream_queue:
            if self._stream_error is None:
                self._stream_error = failure or ConnectionError("OpenCode disconnected during stream")
            self._stream_queue.put_nowait(None)
        if not self._disconnect_lock.locked():
            self._schedule_host_cleanup()
            self._schedule_process_cleanup()

    def _transport_identity(self) -> object | None:
        return self._popen

    def _sync_read_stderr(self) -> None:
        """Synchronous stderr reader running in a thread."""
        try:
            proc = self._popen
            if proc is None or proc.stderr is None:
                return
            while True:
                line = proc.stderr.readline(MAX_STDERR_LINE_BYTES + 1)
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self._stderr_buffer.append(text)
                    if sensitive_logging_enabled():
                        logger.debug("OpenCode stderr: %s", text)
        except Exception as exc:
            logger.debug(
                "OpenCode stderr reader stopped unexpectedly: %s",
                exception_summary(exc),
                exc_info=sensitive_logging_enabled(),
            )

    async def _disconnect_locked(self) -> None:
        """Terminate the OpenCode subprocess under the shared cleanup owner."""
        if self._spawn_abandoned is not None:
            self._spawn_abandoned.set()
        if self._connected and self.session_id:
            with suppress(Exception):
                await asyncio.wait_for(self.cancel(), timeout=2.0)
        self._connected = False

        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
            self._inflight.clear()
        for future in pending:
            if not future.done():
                future.set_exception(ConnectionError("OpenCode disconnected"))

        host_cleanup_error: BaseException | None = None
        try:
            await self._close_host_services()
        except BaseException as exc:
            host_cleanup_error = exc

        termination_error: BaseException | None = None
        spawn_cleanup_pending = self._spawn_task is not None or (
            self._spawn_cleanup_task is not None and self._spawn_cleanup_task is not asyncio.current_task()
        )
        proc = None if spawn_cleanup_pending else self._popen
        if spawn_cleanup_pending:
            termination_error = RuntimeError("OpenCode subprocess spawn cleanup is still pending")
        elif proc is not None:
            try:
                await asyncio.to_thread(self._terminate_popen_sync, proc)
            except BaseException as exc:
                termination_error = exc
            else:
                if self._popen is proc:
                    self._popen = None

        write_tasks = set(self._write_tasks)
        if write_tasks:
            _done, write_tasks = await asyncio.wait(write_tasks, timeout=3.0)
        if write_tasks and termination_error is None:
            termination_error = RuntimeError("OpenCode write worker did not exit after subprocess cleanup")

        reader_thread = self._reader_thread
        stderr_thread = self._stderr_thread
        for thread in (reader_thread, stderr_thread):
            if thread and thread.is_alive():
                await asyncio.to_thread(thread.join, 3)
        self._reader_thread = reader_thread if reader_thread and reader_thread.is_alive() else None
        self._stderr_thread = stderr_thread if stderr_thread and stderr_thread.is_alive() else None
        if self._reader_thread is not None or self._stderr_thread is not None:
            thread_error = RuntimeError("OpenCode reader thread did not exit after subprocess cleanup")
            if termination_error is None:
                termination_error = thread_error

        dispatch_task = self._dispatch_task
        self._dispatch_task = None
        if dispatch_task is not None and dispatch_task is not asyncio.current_task():
            if not dispatch_task.done():
                dispatch_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await dispatch_task
        self._dispatch_queue = None
        self._loop = None
        with self._dispatch_schedule_lock:
            self._scheduled_dispatches = 0
            self._dispatch_overflow = False
        if termination_error is not None:
            if host_cleanup_error is not None:
                logger.error(
                    "OpenCode host service cleanup also failed: %s",
                    exception_summary(host_cleanup_error),
                )
            raise termination_error
        if host_cleanup_error is not None:
            raise host_cleanup_error

    async def _send_result(self, msg_id: int | str | None, result: Any) -> None:
        """Send result via synchronous write."""
        if not self.is_connected:
            return
        resp = _encode_json_line({"jsonrpc": "2.0", "id": msg_id, "result": result})
        await self._write_with_timeout(resp, self.config.timeout_connect)

    async def _send_error(self, msg_id: int | str | None, code: int, message: str) -> None:
        """Send error via synchronous write."""
        if not self.is_connected:
            return
        resp = _encode_json_line(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": code, "message": message},
            }
        )
        await self._write_with_timeout(resp, self.config.timeout_connect)
