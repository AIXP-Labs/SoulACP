"""ACP client base class — manages CLI subprocess lifecycle and JSON-RPC communication."""

from __future__ import annotations

import asyncio
import asyncio.proactor_events
import json
import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from soulacp.config import ACPConfig
from soulacp.services.fs_service import FSService
from soulacp.services.terminal_service import TerminalService

logger = logging.getLogger(__name__)


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
            logger.warning("ACP query error (chunks=%d): %s", len(self._chunks), e)
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
        """Send a prompt and yield response chunks as they arrive."""
        self._chunks = []
        self._complete = asyncio.Event()
        self._stream_queue = asyncio.Queue()
        self._streaming = True
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
                # in-flight, the failure is expected (subprocess terminated,
                # ACP server returned 'Internal error' or stdin was closed).
                # Don't pollute logs at error level. disconnect() sets
                # self._connected = False atomically as its first action.
                if not self._connected:
                    logger.debug(
                        "ACP RPC ended after disconnect (expected): %s", e
                    )
                else:
                    logger.error("ACP stream RPC error: %s", e)
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

    async def disconnect(self) -> None:
        """Terminate the CLI subprocess and cancel reader tasks."""
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
        """Send a JSON-RPC request and wait for the response."""
        if not self.is_connected:
            raise ConnectionError("ACP not connected")

        if self.process.stdin is None or self.process.stdin.is_closing():
            self._connected = False
            raise ConnectionError("ACP stdin closed")

        if timeout is None:
            timeout = self.config.timeout_connect

        self._msg_id += 1
        mid = self._msg_id
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
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise

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
        """Read stderr for debug logging."""
        try:
            while self.process and self.process.returncode is None:
                line = await self.process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    noise = ("AttachConsole", "conpty", "node:internal", "TracingChannel")
                    if not any(n in text for n in noise):
                        logger.debug("ACP stderr: %s", text)
        except (ConnectionResetError, OSError, asyncio.CancelledError):
            pass
        except Exception:
            pass

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """Extract a JSON object from a line of text."""
        try:
            if text.startswith("{"):
                return json.loads(text)
            if "{" in text:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end > start:
                    logger.debug("Extracting JSON from non-JSON line: %r", text[:80])
                    return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
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
                if "error" in msg:
                    error = msg["error"]
                    emsg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                    fut.set_exception(Exception(emsg))
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

    def _handle_stream_update(self, msg: dict) -> None:
        """Process session/update notifications for streaming chunks."""
        update = msg.get("params", {}).get("update", {})
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
