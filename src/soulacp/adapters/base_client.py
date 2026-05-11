"""ACP client base class — manages CLI subprocess lifecycle and JSON-RPC communication."""

from __future__ import annotations

import asyncio
import asyncio.proactor_events
import collections
import json
import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from typing import Any

from soulacp.config import ACPConfig
from soulacp.services.fs_service import FSService
from soulacp.services.terminal_service import TerminalService

logger = logging.getLogger(__name__)


class RPCError(Exception):
    """JSON-RPC error returned by the ACP server.

    Captures full error context (code, message, data) plus invocation
    metadata (method, msg_id, elapsed_ms) and a tail of subprocess
    stderr, so opaque "Internal error" log lines can be traced back to
    a specific RPC and the surrounding subprocess output.
    """

    def __init__(
        self,
        code: int | None,
        message: str,
        data: Any = None,
        method: str | None = None,
        msg_id: int | None = None,
        elapsed_ms: float | None = None,
        stderr_tail: list[str] | None = None,
        session_id: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.data = data
        self.method = method
        self.msg_id = msg_id
        self.elapsed_ms = elapsed_ms
        self.stderr_tail = stderr_tail or []
        self.session_id = session_id
        super().__init__(self._format())

    def _format(self) -> str:
        parts = [f"code={self.code}", f"msg={self.message!r}"]
        if self.method:
            parts.append(f"method={self.method}")
        if self.msg_id is not None:
            parts.append(f"id={self.msg_id}")
        if self.elapsed_ms is not None:
            parts.append(f"elapsed_ms={self.elapsed_ms:.0f}")
        if self.session_id:
            parts.append(f"sid={self.session_id}")
        if self.data is not None:
            parts.append(f"data={self.data!r}")
        if self.stderr_tail:
            tail = "\n  ".join(self.stderr_tail[-10:])
            parts.append(f"stderr_tail=\n  {tail}")
        return " | ".join(parts)

    @property
    def is_retryable(self) -> bool:
        """JSON-RPC standard codes considered transient."""
        if self.code is None:
            return False
        # -32603 Internal error, -32000~-32099 server errors are usually transient
        return self.code == -32603 or (-32099 <= self.code <= -32000)


class _NoopCM:
    """Context manager that does nothing — used when OTel is not installed."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: Any) -> bool:
        return False


def _inject_otel_context_into_env(env: dict) -> None:
    """Inject current OTel trace context into *env* as W3C headers.

    SoulBot Doc 22 P2/A1: so long-lived ACP subprocesses (and the python
    tools they spawn as Bash) inherit a parent trace_id and parent_span_id.

    If no OTel span is currently active, a short-lived bootstrap span is
    spun up, its trace context captured into env, then ended immediately.
    This is acceptable because OTel permits children that outlive parents
    — the trace_id is already captured in the env dict and will be
    propagated to all subsequent subprocess descendants.

    Silently no-ops if:
    - opentelemetry SDK not installed
    - OTel TracerProvider not initialized
    - SOULBOT_OTEL_PROPAGATE_ACP env flag not set (caller checks)
    """
    try:
        from opentelemetry import context, trace
        from opentelemetry.propagate import inject
    except ImportError:
        return

    current = trace.get_current_span()
    span_ctx = current.get_span_context()
    if span_ctx.trace_id == 0:
        # No active span — spin up a bootstrap one so inject() has something
        # to emit. End it right after capture to avoid leaking span refs.
        tracer = trace.get_tracer("soulacp.bootstrap")
        bootstrap = tracer.start_span("soulbot.acp.bootstrap")
        ctx = trace.set_span_in_context(bootstrap)
        token = context.attach(ctx)
        try:
            carrier: dict = {}
            inject(carrier)
            for k, v in carrier.items():
                env[k.upper()] = v
        finally:
            context.detach(token)
            bootstrap.end()
    else:
        carrier: dict = {}
        inject(carrier)
        for k, v in carrier.items():
            env[k.upper()] = v

# ---------------------------------------------------------------------------
# Windows ProactorEventLoop bug workaround (Python < 3.12.4)
# https://github.com/python/cpython/issues/120804
# _ProactorReadPipeTransport._force_close accesses _empty_waiter which
# doesn't exist on pipe transports, causing AttributeError on subprocess
# disconnect.  Patch only if the attribute is missing.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    _PipeTransport = asyncio.proactor_events._ProactorReadPipeTransport
    _orig_force_close = _PipeTransport._force_close

    def _patched_force_close(self, exc):
        if not hasattr(self, "_empty_waiter"):
            self._empty_waiter = None
        return _orig_force_close(self, exc)

    _PipeTransport._force_close = _patched_force_close  # type: ignore[assignment]


class ACPClientBase(ABC):
    """Base class for ACP CLI subprocess clients.

    Manages:
    - Subprocess lifecycle (start / terminate)
    - JSON-RPC request/response over stdin/stdout
    - Stream chunk collection
    - Permission auto-approval
    - Idle timeout tracking for pool management

    Subclasses must implement:
    - ``_get_acp_command()`` — CLI command and arguments
    - ``_initialize()`` — protocol handshake + session creation
    """

    # Method alias → (service_type, action) routing table
    METHOD_MAP: dict[str, tuple[str, str]] = {
        # File system
        "fs/read_text_file": ("fs", "read_text_file"),
        "read_text_file": ("fs", "read_text_file"),
        "fs/readTextFile": ("fs", "read_text_file"),
        "fs/write_text_file": ("fs", "write_text_file"),
        "write_text_file": ("fs", "write_text_file"),
        "fs/list_directory": ("fs", "list_directory"),
        "list_directory": ("fs", "list_directory"),
        "fs/exists": ("fs", "exists"),
        "fs/stat": ("fs", "stat"),
        "fs/get_file_info": ("fs", "stat"),
        # Terminal
        "terminal/create": ("terminal", "create"),
        "terminal/wait_for_exit": ("terminal", "wait_for_exit"),
        "terminal/output": ("terminal", "get_output"),
        "terminal/release": ("terminal", "release"),
    }

    def __init__(self, config: ACPConfig) -> None:
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self.session_id: str | None = None

        self._msg_id: int = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._connected: bool = False
        self._last_used: float = time.time()
        # Stream state
        self._chunks: list[str] = []
        self._complete: asyncio.Event | None = None
        self._stream_queue: asyncio.Queue | None = None
        self._streaming: bool = False

        # Reader tasks (stored for cancellation on disconnect)
        self._reader_tasks: list[asyncio.Task] = []

        # Inflight RPC tracking (msg_id -> (method, start_ts)) for error context
        self._inflight: dict[int, tuple[str, float]] = {}
        # Recent stderr lines (ring buffer for error attribution)
        self._stderr_buffer: collections.deque[str] = collections.deque(maxlen=50)
        # Stream error transport — query_stream re-raises after queue drain
        self._stream_error: Exception | None = None

        # Optional callback for session/update events that soulacp does not
        # surface as text chunks (tool_call, tool_call_update, plan, thought,
        # etc.). Consumers needing tool/plan visibility can register a
        # callback via ``set_update_callback()`` to observe the raw update
        # dict without subclassing.
        self._update_callback: Callable[[dict], None] | None = None

        # Host services for CLI subprocess requests
        cwd = config.cwd or os.getcwd()
        self._fs_service = FSService(cwd)
        self._terminal_service = TerminalService(cwd)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected and self.process is not None and self.process.returncode is None

    @property
    def is_idle_timeout(self) -> bool:
        return time.time() - self._last_used > self.config.pool_idle_timeout

    async def ping(self) -> bool:
        """Lightweight health check — verify subprocess is alive and writable."""
        if not self.is_connected:
            return False
        try:
            self.process.stdin.write(b"\n")
            await self.process.stdin.drain()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start the CLI subprocess and initialize a session."""
        cmd = self._get_acp_command()
        if not cmd:
            raise FileNotFoundError("CLI binary not found")

        # Append user-supplied extra CLI args (ACPConfig.extra_args).
        # Adapter-agnostic mechanism for passing ``-c key=value`` overrides
        # (Codex reasoning_effort, sandbox flags, etc.) without subclassing.
        if self.config.extra_args:
            cmd = cmd + list(self.config.extra_args)

        env = os.environ.copy()
        env["HEADLESS"] = "true"
        env["FORCE_COLOR"] = "0"
        env["NO_COLOR"] = "1"

        # SoulBot Doc 22 P2/A1: propagate W3C trace context to subprocess
        # via TRACEPARENT / BAGGAGE env vars. OFF by default — opt in via
        # SOULBOT_OTEL_PROPAGATE_ACP=1 (protects non-SoulBot consumers).
        # Best-effort: silently skipped if opentelemetry is not installed.
        if os.environ.get("SOULBOT_OTEL_PROPAGATE_ACP", "0") == "1":
            _inject_otel_context_into_env(env)

        # Apply user-supplied extra environment variables (ACPConfig.extra_env).
        # Adapter-agnostic mechanism for passing tunables that the CLI reads
        # from env rather than argv (e.g. CLAUDE_CODE_EFFORT_LEVEL — Claude
        # Code does NOT forward CLI flags through claude-code-acp).
        if self.config.extra_env:
            env.update(self.config.extra_env)

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=10 * 1024 * 1024,  # 10 MB – default 64 KB is too small for large JSON-RPC lines
        )
        self._connected = True
        self._last_used = time.time()

        # Start reader tasks (store refs for cancellation on disconnect)
        self._reader_tasks = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
        ]

        # Subclass performs protocol handshake + session creation
        await self._initialize()
        logger.info("ACP connected: session=%s pid=%s", self.session_id, self.process.pid)

    async def resume(self, session_id: str) -> bool:
        """Resume a previous session. Returns True on success.

        Subclasses should override with provider-specific resume logic.
        """
        return False

    async def query(self, prompt: str) -> str:
        """Send a prompt and wait for the complete response."""
        self._chunks = []
        self._complete = asyncio.Event()
        self._streaming = False
        self._stream_queue = None
        self._last_used = time.time()

        try:
            await self._rpc(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": [{"type": "text", "text": prompt}],
                },
                timeout=self.config.timeout_prompt,
            )
        except Exception as e:
            logger.warning(
                "ACP query error: sid=%s chunks=%d err=%s",
                self.session_id, len(self._chunks), e,
            )
            if self._chunks:
                return "".join(self._chunks)
            raise

        # Wait for stream completion
        try:
            await asyncio.wait_for(self._complete.wait(), timeout=self.config.timeout_prompt)
        except asyncio.TimeoutError:
            logger.warning("ACP response timeout, returning partial")

        # Allow remaining chunks to arrive (end signal may precede last
        # text chunks due to translator event ordering — seen with OpenClaw).
        if self._chunks:
            await asyncio.sleep(0.05)

        self._last_used = time.time()
        return "".join(self._chunks)

    async def query_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Send a prompt and yield response chunks as they arrive.

        On RPC error mid-stream the queued chunks are drained first, then
        the underlying error (typically RPCError) is raised so callers can
        distinguish a clean end from a server-side failure.
        """
        self._chunks = []
        self._complete = asyncio.Event()
        self._stream_queue = asyncio.Queue()
        self._streaming = True
        self._stream_error = None
        self._last_used = time.time()

        async def _send() -> None:
            try:
                await self._rpc(
                    "session/prompt",
                    {
                        "sessionId": self.session_id,
                        "prompt": [{"type": "text", "text": prompt}],
                    },
                    timeout=self.config.timeout_prompt,
                )
            except Exception as e:
                # If the caller invoked disconnect() while the RPC was
                # in-flight, the failure is expected (subprocess terminated
                # or stdin was closed). Don't pollute logs at error level.
                if not self._connected:
                    logger.debug(
                        "ACP RPC ended after disconnect (expected): %s", e
                    )
                else:
                    logger.error(
                        "ACP stream RPC error: sid=%s err=%s",
                        self.session_id, e,
                    )
                # Capture so query_stream re-raises after queue drain.
                self._stream_error = e
                if self._stream_queue:
                    self._stream_queue.put_nowait(None)

        send_task = asyncio.create_task(_send())

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(self._stream_queue.get(), timeout=self.config.timeout_stream)
                except asyncio.TimeoutError:
                    break
                if chunk is None:
                    # End signal received, but late chunks may still be in
                    # the stdout pipe (race between end marker and last text
                    # chunks — observed with OpenClaw ACP).  Yield control
                    # briefly so _read_stdout can process remaining lines,
                    # then drain whatever arrived.
                    for _ in range(3):
                        await asyncio.sleep(0.1)
                        drained = False
                        while not self._stream_queue.empty():
                            remaining = self._stream_queue.get_nowait()
                            if remaining is not None:
                                self._chunks.append(remaining)
                                yield remaining
                                drained = True
                        if not drained:
                            break
                    # NOTE: RPC result text recovery is handled in _dispatch
                    # (stopReason path) to avoid double-yielding.
                    break
                yield chunk
        finally:
            self._streaming = False
            self._stream_queue = None
            self._last_used = time.time()
            if not send_task.done():
                send_task.cancel()
        # Re-raise after drain so callers distinguish clean end from error.
        if self._stream_error is not None:
            err = self._stream_error
            self._stream_error = None
            raise err

    async def cancel(self) -> None:
        """Send ``session/cancel`` notification to gracefully abort the
        in-flight prompt without tearing down the subprocess.

        Per ACP 1.0 spec, this is the proper way to interrupt a prompt —
        ``disconnect()`` is meant for connection teardown, not turn abort.
        Best-effort: silently ignored if there is no active session or
        the subprocess is already gone.
        """
        if not self.session_id or not self._connected:
            return
        try:
            await self._send_notification(
                "session/cancel",
                {"sessionId": self.session_id},
            )
        except Exception as e:
            logger.debug("session/cancel notification failed: %s", e)

    async def disconnect(self) -> None:
        """Terminate the CLI subprocess and cancel reader tasks.

        Sends ``session/cancel`` first as a courtesy so the agent can
        flush any in-flight work cleanly before the pipe closes.
        """
        # Best-effort cancel before tearing down — gives agent a chance
        # to record a graceful turn end rather than orphaned state.
        if self._connected and self.session_id:
            try:
                await asyncio.wait_for(self.cancel(), timeout=2.0)
            except Exception:
                pass

        self._connected = False

        # Cancel reader tasks first to avoid pipe warnings
        for task in self._reader_tasks:
            if not task.done():
                task.cancel()
        for task in self._reader_tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._reader_tasks.clear()

        if self.process:
            proc = self.process
            self.process = None

            # Close stdin first to signal EOF to the subprocess
            if proc.stdin and not proc.stdin.is_closing():
                try:
                    proc.stdin.close()
                    await proc.stdin.wait_closed()
                except Exception:
                    pass

            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=3)
                except Exception:
                    pass
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            finally:
                # Close subprocess transport to avoid Windows pipe warnings
                transport = getattr(proc, "_transport", None)
                if transport:
                    try:
                        transport.close()
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Abstract — subclass must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def _get_acp_command(self) -> list[str]:
        """Return the CLI command + arguments to start the subprocess."""
        ...

    @abstractmethod
    async def _initialize(self) -> None:
        """Perform protocol handshake and create a session.

        Must set ``self.session_id`` on success.
        """
        ...

    # ------------------------------------------------------------------
    # JSON-RPC communication
    # ------------------------------------------------------------------

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no ID, no response expected).

        Per JSON-RPC 2.0 spec, notifications omit the 'id' field.
        The server MUST NOT reply to a notification.
        """
        if not self._connected or not self.process or not self.process.stdin:
            raise ConnectionError("Not connected")
        msg = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": method,
                **({"params": params} if params else {}),
            }
        )
        self.process.stdin.write((msg + "\n").encode())
        await self.process.stdin.drain()
        logger.debug("Sent notification: %s", method)

    async def _rpc(self, method: str, params: dict, timeout: int | None = None) -> Any:
        """Send a JSON-RPC request and wait for the response.

        If opentelemetry is installed *and* a tracer provider is configured,
        an ``acp.rpc`` span is emitted with method/session_id/code attrs.
        Soft-noops (no overhead, no exception) when OTel is absent.
        """
        if not self.is_connected:
            raise ConnectionError("ACP not connected")

        if self.process.stdin is None or self.process.stdin.is_closing():
            self._connected = False
            raise ConnectionError("ACP stdin closed")

        if timeout is None:
            timeout = self.config.timeout_connect

        self._msg_id += 1
        mid = self._msg_id

        span_cm = self._otel_rpc_span(method, mid)
        with span_cm as span:
            msg = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "method": method,
                    "params": params,
                }
            )

            self.process.stdin.write((msg + "\n").encode())
            await self.process.stdin.drain()

            future = asyncio.get_running_loop().create_future()
            self._pending[mid] = future
            self._inflight[mid] = (method, time.time())
            try:
                result = await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                self._pending.pop(mid, None)
                self._inflight.pop(mid, None)
                if span is not None:
                    span.set_attribute("acp.timeout", True)
                raise
            except RPCError as rpc_err:
                if span is not None:
                    span.set_attribute("acp.code", rpc_err.code if rpc_err.code is not None else 0)
                    span.set_attribute("acp.error_message", rpc_err.message[:200])
                raise
            finally:
                self._inflight.pop(mid, None)
            return result

    def _otel_rpc_span(self, method: str, msg_id: int):
        """Return an OTel span CM for this RPC, or a no-op CM if unavailable.

        Lazy imports opentelemetry so soulacp has no hard dependency.
        """
        try:
            from opentelemetry import trace
        except ImportError:
            return _NoopCM()
        tracer = trace.get_tracer("soulacp.rpc")
        attrs = {"acp.method": method, "acp.msg_id": msg_id}
        if self.session_id:
            attrs["acp.session_id"] = self.session_id
        provider = getattr(self.config, "provider", None)
        if provider:
            attrs["acp.provider"] = provider
        return tracer.start_as_current_span(f"acp.rpc {method}", attributes=attrs)

    async def _send_result(self, msg_id: int, result: Any) -> None:
        """Send a JSON-RPC success response back to the subprocess."""
        if not self.is_connected:
            return
        resp = json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result})
        self.process.stdin.write((resp + "\n").encode())
        await self.process.stdin.drain()

    async def _send_error(self, msg_id: int, code: int, message: str) -> None:
        """Send a JSON-RPC error response back to the subprocess."""
        if not self.is_connected:
            return
        resp = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": code, "message": message},
            }
        )
        self.process.stdin.write((resp + "\n").encode())
        await self.process.stdin.drain()

    # ------------------------------------------------------------------
    # Stdout reader + message dispatch
    # ------------------------------------------------------------------

    async def _read_stdout(self) -> None:
        """Read and dispatch JSON-RPC messages from stdout."""
        try:
            while self.process and self.process.returncode is None:
                line = await self.process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue

                msg = self._parse_json(text)
                if msg:
                    await self._dispatch(msg)
        except (ConnectionResetError, OSError) as e:
            # Windows ProactorEventLoop raises ConnectionResetError (WinError 995)
            # when the subprocess pipe is closed during I/O — this is expected
            # during normal disconnect and should not be logged as a warning.
            if self._connected:
                logger.debug("ACP stdout pipe closed: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("ACP read loop ended unexpectedly: %s", e)
        finally:
            if self._connected:
                logger.warning(
                    "ACP subprocess disconnected (pending_rpcs=%d, session=%s)",
                    len(self._pending),
                    self.session_id,
                )
            self._connected = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("ACP disconnected"))
            self._pending.clear()
            if self._streaming and self._stream_queue:
                self._stream_queue.put_nowait(None)

    async def _read_stderr(self) -> None:
        """Read stderr for debug logging and ring-buffer retention."""
        noise = ("AttachConsole", "conpty", "node:internal", "TracingChannel")
        try:
            while self.process and self.process.returncode is None:
                line = await self.process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text or any(n in text for n in noise):
                    continue
                # Retain for error attribution (RPCError.stderr_tail).
                self._stderr_buffer.append(text)
                logger.debug("ACP stderr: %s", text)
        except (ConnectionResetError, OSError, asyncio.CancelledError):
            pass
        except Exception:
            pass

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """Extract a JSON object from a line of text.

        Lines that look like JSON (start with ``{``) but fail to parse are
        logged at WARNING — they likely indicate a malformed RPC frame from
        the server. Lines that contain JSON embedded in other text are
        logged at DEBUG (banners, progress output, etc.).
        """
        looks_like_json = text.startswith("{")
        try:
            if looks_like_json:
                return json.loads(text)
            if "{" in text:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end > start:
                    logger.debug("Extracting JSON from non-JSON line: %r", text[:80])
                    return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            if looks_like_json:
                logger.warning(
                    "Malformed JSON-RPC frame: %s | line=%r", e, text[:200],
                )
            else:
                logger.debug("Failed to parse JSON from line: %r", text[:80])
        return None

    async def _dispatch(self, msg: dict) -> None:
        """Route an incoming JSON-RPC message."""
        method = msg.get("method")

        # -- Notifications (no "id") or server→client requests (has "id" + "method") --

        # Stream update notifications
        if method == "session/update":
            self._handle_stream_update(msg)
            return

        # Requests from CLI subprocess (has "id" + "method")
        if method and "id" in msg:
            await self._handle_request(msg)
            return

        # -- RPC responses (has "id", no "method") --
        if "id" in msg and not method:
            mid = msg["id"]
            if mid in self._pending:
                fut = self._pending.pop(mid)
                inflight_method, start_ts = self._inflight.pop(mid, (None, None))
                if "error" in msg:
                    error = msg["error"]
                    if isinstance(error, dict):
                        elapsed_ms = (time.time() - start_ts) * 1000 if start_ts else None
                        rpc_err = RPCError(
                            code=error.get("code"),
                            message=error.get("message", ""),
                            data=error.get("data"),
                            method=inflight_method,
                            msg_id=mid,
                            elapsed_ms=elapsed_ms,
                            stderr_tail=list(self._stderr_buffer),
                            session_id=self.session_id,
                        )
                        fut.set_exception(rpc_err)
                    else:
                        fut.set_exception(Exception(str(error)))
                else:
                    result = msg.get("result", {})
                    fut.set_result(result)
                    # Check stopReason
                    if isinstance(result, dict) and result.get("stopReason"):
                        # Some providers embed final text in the RPC
                        # response.  Extract it before ending the stream.
                        result_text = result.get("text", "")
                        if result_text and self._streaming and self._stream_queue:
                            accumulated = "".join(self._chunks)
                            if len(result_text) > len(accumulated) and result_text.startswith(accumulated):
                                tail = result_text[len(accumulated) :]
                                self._chunks.append(tail)
                                self._stream_queue.put_nowait(tail)
                                logger.debug(
                                    "Recovered %d chars from RPC result",
                                    len(tail),
                                )

                        if self._complete:
                            self._complete.set()
                        if self._streaming and self._stream_queue:
                            self._stream_queue.put_nowait(None)

    # ------------------------------------------------------------------
    # Stream update handling
    # ------------------------------------------------------------------

    def set_update_callback(self, callback: Callable[[dict], None] | None) -> None:
        """Register a callback for raw ``session/update`` events.

        The callback receives the ``update`` dict for EVERY update
        (text chunks, tool_call, tool_call_update, plan, thought, ...)
        in addition to soulacp's built-in text-chunk handling. Pass
        ``None`` to clear.

        Use this when you need observability beyond plain text replies
        (e.g. SoulBot OTel + AISOP execution tracking tool invocations).
        Callback errors are swallowed (logged at debug) so faulty
        consumers can't break streaming.
        """
        self._update_callback = callback

    def _handle_stream_update(self, msg: dict) -> None:
        """Process session/update notifications for streaming chunks.

        Per ACP 1.0 spec, session/update ``updates`` array can contain:
          - text chunks (handled here as agent_message_chunk / *_delta)
          - tool calls + tool call status updates  (NOT exposed as text;
            consumers needing tool visibility should register a callback
            via ``set_update_callback()`` to observe the raw update dict)
          - agent plans / thoughts  (same — accessible via callback only)

        This is a deliberate scope choice — soulacp is a "give me the
        text reply" adapter library, not an agent observability surface.
        Tool / plan visibility belongs to a higher layer (e.g. SoulBot
        OTel + AISOP execution).
        """
        update = msg.get("params", {}).get("update", {})

        # Invoke user-registered callback BEFORE any internal handling
        # so consumers see all updates in order (text + non-text alike).
        if self._update_callback is not None:
            try:
                self._update_callback(update)
            except Exception as e:
                logger.debug("update callback raised: %s", e)

        utype = update.get("sessionUpdate")

        if utype == "agent_message_chunk":
            content = update.get("content", {})
            if isinstance(content, dict) and content.get("type") == "text":
                chunk = content.get("text", "")
                self._chunks.append(chunk)
                if self._streaming and self._stream_queue:
                    self._stream_queue.put_nowait(chunk)

        elif utype in ("text_message_content", "content_block_delta"):
            chunk = None
            if "text" in update:
                chunk = update["text"]
            elif "content" in update:
                c = update["content"]
                if isinstance(c, str):
                    chunk = c
                elif isinstance(c, dict):
                    chunk = c.get("text", "")
            if chunk:
                self._chunks.append(chunk)
                if self._streaming and self._stream_queue:
                    self._stream_queue.put_nowait(chunk)

        elif utype in ("agent_message_end", "session_end", "turn_end"):
            # Some translators (e.g. OpenClaw) embed the final text chunk
            # inside the end event itself.  Extract it before signalling
            # stream completion so the text is not silently dropped.
            end_content = update.get("content", {})
            if isinstance(end_content, dict) and end_content.get("type") == "text":
                tail = end_content.get("text", "")
                if tail:
                    self._chunks.append(tail)
                    if self._streaming and self._stream_queue:
                        self._stream_queue.put_nowait(tail)
            elif isinstance(end_content, str) and end_content:
                self._chunks.append(end_content)
                if self._streaming and self._stream_queue:
                    self._stream_queue.put_nowait(end_content)

            if self._complete:
                self._complete.set()
            if self._streaming and self._stream_queue:
                self._stream_queue.put_nowait(None)

    # ------------------------------------------------------------------
    # Request handling (CLI subprocess → host)
    # ------------------------------------------------------------------

    async def _handle_request(self, msg: dict) -> None:
        """Handle JSON-RPC requests from the CLI subprocess.

        Routes file-system, terminal, and permission requests via
        :attr:`METHOD_MAP`.
        """
        method = msg.get("method", "")
        mid = msg["id"]
        params = msg.get("params", {})

        # Auto-approve permission requests
        if "permission" in method.lower():
            if self.config.auto_approve_permissions:
                options = params.get("options", [])
                if options:
                    await self._send_result(
                        mid, {"outcome": {"outcome": "selected", "optionId": options[0].get("optionId")}}
                    )
                else:
                    await self._send_error(mid, -32603, "No options")
            else:
                await self._send_error(mid, -32603, "Permission denied by config")
            return

        # Dispatch via METHOD_MAP
        entry = self.METHOD_MAP.get(method)
        if not entry:
            await self._send_error(mid, -32601, f"Method not supported: {method}")
            return

        service_type, action = entry
        try:
            if service_type == "fs":
                result = await getattr(self._fs_service, action)(**params)
            elif service_type == "terminal":
                result = await getattr(self._terminal_service, action)(**params)
            else:
                await self._send_error(mid, -32601, f"Unknown service: {service_type}")
                return
            await self._send_result(mid, result)
        except Exception as exc:
            await self._send_error(mid, -32603, str(exc))
