"""ACP client base class — manages CLI subprocess lifecycle and JSON-RPC communication."""

from __future__ import annotations

import asyncio
import asyncio.proactor_events
import collections
import copy
import json
import logging
import math
import os
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from soulacp._async import wait_for_owned
from soulacp._json import strict_json_loads
from soulacp.config import (
    ACPConfig,
    _is_absolute_path,
    _is_finite_number,
    _model_id_for_provider,
    _require_valid_unicode,
)
from soulacp.environment import (
    build_subprocess_environment,
    get_subprocess_environment_value,
    update_subprocess_environment,
)
from soulacp.errors import (
    ACPAuthenticationError,
    ACPPartialResponseError,
    ACPPromptTimeoutError,
    ACPProtocolError,
    ACPRequestOutcomeUnknownError,
    ACPResponseError,
    ACPStreamTimeoutError,
    ACPUnsupportedCapabilityError,
)
from soulacp.privacy import _bind_private_cause, _raise_private_cause, exception_summary, sensitive_logging_enabled
from soulacp.services.fs_service import (
    FSService,
    HostResourceLimitError,
    TextEncodingError,
    UnsupportedTextResourceError,
)
from soulacp.services.terminal_service import TerminalNotFoundError, TerminalService
from soulacp.telemetry import telemetry_provider_name

from ._session_updates import validate_permission_tool_call, validate_stable_session_update

logger: logging.Logger = logging.getLogger(__name__)


def _process_group_kwargs() -> dict[str, Any]:
    """Spawn keywords that make the CLI's tools reachable for termination.

    POSIX: the CLI gets its own session / process group, so ``os.killpg`` can
    reach tools still in that group after the CLI itself has exited. Windows: the
    descendants are recorded with a Toolhelp32 snapshot before the CLI is told to
    stop (see ``_snapshot_descendants``), so no creation flag is needed.
    This is best-effort cleanup, not containment or proof of whole-tree exit.
    """
    if sys.platform == "win32":
        return {}
    return {"start_new_session": True}


def _win_parent_map() -> dict[int, int]:
    """pid -> parent pid for every process, from a Toolhelp32 snapshot (Windows only)."""
    import ctypes
    import ctypes.wintypes as wt

    class _Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wt.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    kernel32 = cast(Any, ctypes).windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.Process32First.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Entry)]
    kernel32.Process32Next.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Entry)]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise OSError("CreateToolhelp32Snapshot failed")
    parents: dict[int, int] = {}
    try:
        entry = _Entry()
        entry.dwSize = ctypes.sizeof(_Entry)
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return parents


def _snapshot_descendants(pid: int | None) -> set[int]:
    """Return Windows descendants observed in a snapshot, or empty on failure.

    Empty is not proof of whole-tree exit. POSIX uses the process group instead.
    """
    if sys.platform != "win32" or not pid:
        return set()
    try:
        parents = _win_parent_map()
    except OSError:
        return set()
    found: set[int] = set()
    frontier = {int(pid)}
    while frontier:
        nxt = {child for child, parent in parents.items() if parent in frontier and child not in found and child != pid}
        found |= nxt
        frontier = nxt
    return found


def _win_terminate_descendants(pids: set[int], root: int) -> list[int]:
    """Terminate the recorded descendants that still belong to the CLI's chain (Windows only).

    A recorded pid is terminated only while its current parent is the CLI or another
    recorded pid. This reduces accidental targeting, but a parent-PID snapshot
    is not a race-free process identity or lifetime proof.
    """
    import ctypes

    kernel32 = cast(Any, ctypes).windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        parents = _win_parent_map()
    except OSError:
        return []
    chain = set(pids) | {int(root)}
    killed: list[int] = []
    for pid in sorted(pids):
        if parents.get(pid) not in chain:
            continue
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if not handle:
            continue
        try:
            if kernel32.TerminateProcess(handle, 1):
                killed.append(pid)
        finally:
            kernel32.CloseHandle(handle)
    return killed


async def _terminate_process_tree(
    proc: asyncio.subprocess.Process,
    descendants: set[int] | None = None,
) -> str | None:
    """Best-effort termination of the CLI's tools (shell -> long command).

    Terminating only the CLI leaves the tools it spawned running as orphans:
    ``TerminateProcess`` never touches children on Windows and ``SIGTERM`` to the
    parent alone does not either on POSIX. Windows terminates the descendants
    recorded before the CLI was told to stop (or a fresh snapshot while the CLI is
    still alive, or after it exited: Windows keeps an orphan's parent id, so the
    walk can still find recorded tools); POSIX signals the CLI's
    process group, which can outlive the CLI. Detached processes, snapshot/PID
    races, permissions, and unobserved exits prevent a general guarantee.
    Only a real
    ``asyncio.subprocess.Process`` is handled; test fakes are skipped. Returns a
    short description (pids and counts only, never a command line) or ``None`` when
    nothing was attempted. Expected OS termination errors are handled locally;
    the returned summary is not independent proof that all descendants exited.
    """
    if not isinstance(proc, asyncio.subprocess.Process):
        return None
    pid = proc.pid
    try:
        if sys.platform == "win32":
            pids = set(descendants or ())
            if not pids:
                pids = _snapshot_descendants(pid)
            if not pids:
                return None
            killed = _win_terminate_descendants(pids, pid)
            return f"terminated {len(killed)} of {len(pids)} recorded descendants of pid {pid}"
        import signal

        # spawned with _process_group_kwargs the CLI leads its own group (pgid == pid),
        # which outlives the CLI itself, so an exited parent still names the group
        pgid = os.getpgid(pid) if proc.returncode is None else int(pid)
        if pgid == os.getpgid(0):
            # not in its own group (spawned without _process_group_kwargs):
            # never signal the group we belong to ourselves
            return None
        os.killpg(pgid, signal.SIGTERM)
        return f"SIGTERM sent to process group {pgid} of pid {pid}"
    except ProcessLookupError:
        # the CLI and its process group are already gone: nothing left to terminate
        return None
    except (PermissionError, OSError) as exc:
        return f"process tree termination failed for pid {pid}: {exception_summary(exc)}"


_STREAM_ACTIVITY = object()
MAX_RESPONSE_CHARS = 10 * 1024 * 1024
MAX_RESPONSE_CHUNKS = 65_536
MAX_HOST_REQUEST_TASKS = 64
MAX_TERMINAL_WAIT_REQUESTS = 32
MAX_STDERR_LINE_CHARS = 64 * 1024
RPC_PROCESS_POLL_INTERVAL = 0.1
RPC_EXIT_DRAIN_TIMEOUT = 1.0
ACP_PROTOCOL_VERSION = 1
ACP_PROMPT_STOP_REASONS = frozenset({"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"})
_UNSUPPORTED_LEGACY_MODEL_METHODS: set[tuple[type, str]] = set()
_MISSING_SESSION_MARKERS = (
    "unknown session",
    "session not found",
    "no such session",
    "session does not exist",
)


@dataclass(frozen=True, slots=True)
class _SessionStateSnapshot:
    session_id: str | None
    config_options: list[dict[str, Any]]
    mode_state: dict[str, Any] | None
    info: dict[str, Any]
    usage: dict[str, Any] | None
    available_commands: list[dict[str, Any]]


@dataclass(slots=True)
class _RPCLifecycle:
    """One request's shared reader/waiter state, without retaining its payload."""

    transition_id: str | None
    previous_state: _SessionStateSnapshot | None
    mode_id: str | None = None
    mode_session_id: str | None = None
    mode_state: dict[str, Any] | None = None
    config_session_id: str | None = None
    config_session_generation: int | None = None
    applied: bool = False
    abandoned: bool = False
    finished: bool = False


@dataclass(slots=True)
class _PermissionTurnState:
    cancelled: bool = False
    active: bool = False


def _encode_json_line(payload: dict[str, Any]) -> bytes:
    """Encode one strict UTF-8 newline-delimited JSON-RPC frame."""
    content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return (content + "\n").encode("utf-8")


def _validate_json_values(value: Any, label: str) -> None:
    """Reject escaped invalid Unicode and non-finite numbers in parsed JSON."""
    pending: list[Any] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            _require_valid_unicode(item, label)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{label} numbers must be finite")
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


class RPCError(Exception):
    """JSON-RPC error returned by the ACP server.

    Raw provider fields remain available as attributes for programmatic
    inspection. The default string is privacy-safe; callers must explicitly
    request ``format_diagnostic(include_sensitive=True)`` to render raw data.
    """

    code: int | None
    message: str
    data: Any
    method: str | None
    msg_id: int | None
    elapsed_ms: float | None
    stderr_tail: list[str]
    session_id: str | None
    primary_cause: BaseException | None = None

    _STANDARD_MESSAGES = frozenset(
        {
            "parse error",
            "invalid request",
            "method not found",
            "invalid params",
            "internal error",
            "request cancelled",
            "authentication required",
            "resource not found",
        }
    )

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
        super().__init__(self.format_diagnostic(include_sensitive=False))

    def format_diagnostic(self, *, include_sensitive: bool = False) -> str:
        """Render this error, optionally including provider-controlled data."""
        if include_sensitive:
            message = self.message
        else:
            stripped = self.message.strip()
            message = stripped if stripped.lower() in self._STANDARD_MESSAGES else "<redacted>"
        parts = [f"code={self.code}", f"msg={message!r}"]
        if self.method:
            parts.append(f"method={self.method}")
        if self.msg_id is not None:
            parts.append(f"id={self.msg_id}")
        if self.elapsed_ms is not None:
            parts.append(f"elapsed_ms={self.elapsed_ms:.0f}")
        if self.session_id:
            parts.append(f"sid={self.session_id}" if include_sensitive else "sid=<redacted>")
        if self.data is not None:
            parts.append(f"data={self.data!r}" if include_sensitive else "data=<redacted>")
        if self.stderr_tail:
            if include_sensitive:
                tail = "\n  ".join(self.stderr_tail[-10:])
                parts.append(f"stderr_tail=\n  {tail}")
            else:
                parts.append(f"stderr_tail=<redacted:{min(len(self.stderr_tail), 10)} lines>")
        return " | ".join(parts)

    # Subprocess fatal-exit codes (OOM / corruption / fast-fail), retained for
    # diagnostics. They do not prove that replaying a prompt is idempotent.
    _FATAL_SUBPROCESS_EXIT_CODES = (
        "3221226505",
        "0xc0000409",  # Windows STATUS_STACK_BUFFER_OVERRUN / __fastfail (common V8 OOM)
        "3221225477",
        "0xc0000005",  # Windows access violation
        "134",
        "137",
        "139",  # POSIX SIGABRT(abort/OOM) / SIGKILL(OOM killer) / SIGSEGV
    )

    def _is_fatal_subprocess_exit(self) -> bool:
        """True if data.details reports a fatal subprocess exit code."""
        if not isinstance(self.data, dict):
            return False
        details = str(self.data.get("details", "")).lower()
        if "exited with code" in details or "process exited" in details:
            return any(c in details for c in self._FATAL_SUBPROCESS_EXIT_CODES)
        return False

    @property
    def is_retryable(self) -> bool:
        """Return whether this control-plane RPC can be retried safely enough.

        ACP v1 has no idempotency key for ``session/prompt``. Even an internal
        server error may arrive after tool side effects, so prompt RPCs are
        never automatically replayed. Transient codes remain retryable for
        initialization and other control-plane methods.
        """
        if isinstance(self.code, bool) or not isinstance(self.code, int):
            return False
        if self.method in {"session/prompt", "authenticate"}:
            return False
        standard_message = self.message.strip().lower()
        if (self.code, standard_message) in {
            (-32000, "authentication required"),
            (-32002, "resource not found"),
        }:
            return False
        # Internal error and undefined provider server errors may be transient.
        return self.code == -32603 or (-32099 <= self.code <= -32000)


class _NoopCM:
    """Context manager that does nothing — used when OTel is not installed."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: Any) -> bool:
        return False


class _SafeSpanCM:
    """Keep an optional OTel context manager from affecting RPC behavior."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self._entered = False
        self._span: Any = None

    def __enter__(self) -> Any | None:
        try:
            span = self._delegate.__enter__()
        except Exception:
            with suppress(Exception):
                self._delegate.__exit__(None, None, None)
            return None
        self._entered = True
        self._span = span
        return span

    def __exit__(self, *args: Any) -> bool:
        if not self._entered:
            return False
        if len(args) > 1 and args[1] is not None:
            with suppress(Exception):
                from opentelemetry.trace import Status, StatusCode  # pyright: ignore[reportMissingImports]

                self._span.set_status(Status(StatusCode.ERROR))
                self._span.set_attribute(
                    "error.type", "cancelled" if isinstance(args[1], asyncio.CancelledError) else "error"
                )
        with suppress(Exception):
            # SDK context managers otherwise record the original exception and
            # its raw cause/stack automatically, bypassing diagnostic redaction.
            self._delegate.__exit__(None, None, None)
        # Telemetry must never suppress an exception from the RPC body, even
        # if a third-party context manager incorrectly returns a truthy value.
        return False


def _otel_propagation_enabled() -> bool:
    """Return whether generic ACP subprocess context propagation is enabled."""
    return os.environ.get("ACP_OTEL_PROPAGATE", "0") == "1"


def _inject_otel_context_into_env(env: dict[str, str]) -> None:
    """Inject an existing valid OTel context into subprocess startup env.

    This is opt-in process-start propagation, not per-prompt propagation.
    No synthetic trace is created when the caller has no active span.

    Silently no-ops if:
    - opentelemetry API not installed
    - OTel TracerProvider not initialized
    - propagation env flag not set (caller checks)
    """
    try:
        from opentelemetry import trace  # pyright: ignore[reportMissingImports]
        from opentelemetry.propagate import inject  # pyright: ignore[reportMissingImports]

        current = trace.get_current_span()
        span_ctx = current.get_span_context()
        if span_ctx.trace_id == 0:
            return
        carrier: dict[str, str] = {}
        inject(carrier)
        for key, value in carrier.items():
            normalized_key = key.lower()
            if normalized_key not in {"traceparent", "tracestate"}:
                continue
            if not isinstance(value, str) or "\x00" in value:
                continue
            try:
                encoded_value = value.encode("utf-8")
            except UnicodeEncodeError:
                continue
            if len(encoded_value) > 1024:
                continue
            update_subprocess_environment(env, {normalized_key.upper(): value})
    except Exception:
        return


async def _await_owned_cleanup(task: asyncio.Task[None], *, label: str) -> None:
    """Finish owned cleanup before propagating cancellation from its caller."""
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
                "%s failed while its caller was cancelled: %s",
                label,
                exception_summary(cleanup_error),
            )
        raise asyncio.CancelledError
    if cleanup_error is not None:
        raise cleanup_error


def _finalize_rpc_future(future: asyncio.Future[Any]) -> None:
    """Cancel an unfinished RPC future or retrieve its terminal exception."""
    if not future.done():
        future.cancel()
    elif not future.cancelled():
        future.exception()


# ---------------------------------------------------------------------------
# Windows ProactorEventLoop bug workaround
# https://github.com/python/cpython/issues/87694
# _ProactorReadPipeTransport._force_close accesses _empty_waiter which
# doesn't exist on pipe transports, causing AttributeError on subprocess
# disconnect. Patch the class once; the guard also makes module reload safe.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    _PipeTransport = asyncio.proactor_events._ProactorReadPipeTransport
    _force_close = getattr(_PipeTransport, "_force_close")  # noqa: B009 - private asyncio compatibility hook
    if not getattr(_force_close, "_soulacp_empty_waiter_guard", False):
        _orig_force_close = _force_close

        def _patched_force_close(self, exc, _delegate=_orig_force_close):
            if not hasattr(self, "_empty_waiter"):
                setattr(self, "_empty_waiter", None)  # noqa: B010 - private asyncio compatibility hook
            return _delegate(self, exc)

        setattr(  # noqa: B010 - marker on a dynamically patched private method
            _patched_force_close, "_soulacp_empty_waiter_guard", True
        )
        setattr(_PipeTransport, "_force_close", _patched_force_close)  # noqa: B010 - private asyncio hook


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

    config: ACPConfig
    process: asyncio.subprocess.Process | None
    agent_capabilities: dict[str, Any]
    agent_info: dict[str, Any] | None
    auth_methods: list[dict[str, Any]]
    session_config_options: list[dict[str, Any]]
    session_mode_state: dict[str, Any] | None
    session_info: dict[str, Any]
    session_usage: dict[str, Any] | None
    available_commands: list[dict[str, Any]]

    # Method alias → (service_type, action) routing table
    METHOD_MAP: ClassVar[dict[str, tuple[str, str]]] = {
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
        "terminal/kill": ("terminal", "kill"),
        "terminal/release": ("terminal", "release"),
    }

    def __init__(self, config: ACPConfig) -> None:
        self.config = config
        self._auth_method_id = config.auth_method_id
        self._auth_provider = config.provider
        self._explicit_auth_attempted = False
        self._explicit_auth_succeeded = False
        self.process: asyncio.subprocess.Process | None = None
        self._command_identity: str | None = None
        self._session_id: str | None = None
        self.agent_capabilities: dict[str, Any] = {}
        self.agent_info: dict[str, Any] | None = None
        self.auth_methods: list[dict[str, Any]] = []
        self.session_config_options: list[dict[str, Any]] = []
        self.session_mode_state: dict[str, Any] | None = None
        self.session_info: dict[str, Any] = {}
        self.session_usage: dict[str, Any] | None = None
        self.available_commands: list[dict[str, Any]] = []
        self._session_transition_id: str | None = None
        self._session_transition_active = False
        self._mode_state_revision = 0
        self._session_state_generation = 0
        self._rpc_lifecycles: dict[int, _RPCLifecycle] = {}

        self._msg_id: int = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._connected: bool = False
        self._pool_reusable: bool = True
        self._last_used: float = time.monotonic()
        # Stream state
        self._chunks: list[str] = []
        self._chunk_chars = 0
        self._complete: asyncio.Event | None = None
        self._stream_queue: asyncio.Queue[object] | None = None
        self._streaming: bool = False
        self._activity_queued = False
        self._query_lock = asyncio.Lock()
        self._permission_turn = _PermissionTurnState()
        self._permission_request_turns: dict[int | str | None, _PermissionTurnState] = {}
        self._connect_lock = asyncio.Lock()
        self._disconnect_lock = asyncio.Lock()
        self._disconnect_task: asyncio.Task[None] | None = None
        self._host_cleanup_lock = asyncio.Lock()
        self._host_cleanup_task: asyncio.Task[None] | None = None
        self._host_services_need_cleanup = False
        self._process_cleanup_task: asyncio.Task[None] | None = None
        self._async_spawn_task: asyncio.Task[asyncio.subprocess.Process] | None = None
        self._async_spawn_cleanup_task: asyncio.Task[None] | None = None

        # Reader tasks (stored for cancellation on disconnect)
        self._reader_tasks: list[asyncio.Task] = []
        self._request_tasks: set[asyncio.Task[None]] = set()
        self._request_tasks_by_id: dict[int | str | None, asyncio.Task[None]] = {}
        self._request_reply_started: set[int | str | None] = set()
        self._terminal_wait_request_ids: set[int | str | None] = set()

        # Inflight RPC tracking (msg_id -> (method, start_ts)) for error context
        self._inflight: dict[int, tuple[str, float]] = {}
        # Recent stderr lines (ring buffer for error attribution)
        self._stderr_buffer: collections.deque[str] = collections.deque(maxlen=50)
        # Stream error transport — query_stream re-raises after queue drain
        self._stream_error: BaseException | None = None

        # Optional callback for session/update events that soulacp does not
        # surface as text chunks (tool_call, tool_call_update, plan, thought,
        # etc.). Consumers needing tool/plan visibility can register a
        # callback via ``set_update_callback()`` to observe the raw update
        # dict without subclassing.
        self._update_callback: Callable[[dict], None] | None = None

        # Host services for CLI subprocess requests
        cwd = config.cwd or os.getcwd()
        self._fs_service = FSService(cwd, config.additional_directories)
        self._terminal_service = TerminalService(
            cwd,
            config.additional_directories,
            inherit_parent_env=config.inherit_parent_env,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        """Return the logical ACP session currently bound to this transport."""
        return self._session_id

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        self._session_id = value

    @property
    def is_connected(self) -> bool:
        return self._connected and self.process is not None and self.process.returncode is None

    @property
    def is_idle_timeout(self) -> bool:
        return time.monotonic() - self._last_used > self.config.pool_idle_timeout

    @property
    def is_pool_reusable(self) -> bool:
        """Whether a pool may safely retain this logical transport."""
        return self._pool_reusable

    async def ping(self) -> bool:
        """Check local transport state without writing a non-ACP probe frame."""
        if not self.is_connected:
            return False
        process = self.process
        stdin = process.stdin if process is not None else None
        return stdin is not None and not stdin.is_closing()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start and initialize the CLI under one end-to-end deadline."""
        try:
            await wait_for_owned(self._connect_once(), self.config.timeout_connect)
        except asyncio.TimeoutError as exc:
            try:
                await self.disconnect()
            except asyncio.CancelledError:
                pass
            except BaseException as cleanup_exc:
                logger.error("ACP startup-timeout cleanup failed: %s", exception_summary(cleanup_exc))
            if self._auth_method_id is not None and self._explicit_auth_attempted:
                _raise_private_cause(ACPAuthenticationError(), exc)
            raise ConnectionError(f"ACP startup exceeded {self.config.timeout_connect:g}s") from exc
        except asyncio.CancelledError as cancelled:
            try:
                await self.disconnect()
            except asyncio.CancelledError:
                pass
            except BaseException as cleanup_exc:
                logger.error("ACP startup-cancellation cleanup failed: %s", exception_summary(cleanup_exc))
            raise cancelled

    async def _connect_once(self) -> None:
        """Serialize process creation so concurrent callers cannot leak a transport."""
        async with self._connect_lock:
            await self._connect()

    async def _connect(self) -> None:
        if self.is_connected:
            raise RuntimeError("ACP client is already connected")
        if self._async_spawn_task is not None:
            raise RuntimeError("ACP subprocess spawn cleanup is still pending")
        if self.process is not None:
            await self._disconnect_without_connect_lock()
        try:
            await self._start_and_initialize()
        except BaseException:
            cleanup = asyncio.create_task(self._disconnect_without_connect_lock())
            with suppress(BaseException):
                await _await_owned_cleanup(cleanup, label="failed ACP connect cleanup")
            raise

    async def _start_and_initialize(self) -> None:
        # A reconnect owns a new logical transport.  Never carry session or
        # diagnostic state over from the previous subprocess.
        self._reset_protocol_state()
        self._explicit_auth_attempted = False
        await self._prepare_host_services()
        cmd = self._get_acp_command()
        if not cmd:
            raise FileNotFoundError("CLI binary not found")
        self._command_identity = cmd[0]

        # Append user-supplied extra CLI args (ACPConfig.extra_args).
        # Adapter-agnostic mechanism for passing ``-c key=value`` overrides
        # (Codex reasoning_effort, sandbox flags, etc.) without subclassing.
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
        update_subprocess_environment(env, self._get_acp_env())

        # Best-effort W3C trace context propagation to the subprocess.
        if _otel_propagation_enabled():
            _inject_otel_context_into_env(env)

        # Apply user-supplied extra environment variables (ACPConfig.extra_env).
        # Adapter-agnostic mechanism for tunables that the CLI reads from env
        # rather than argv. ACP bridges do not consistently forward provider
        # CLI flags (for example, CLAUDE_CODE_EFFORT_LEVEL).
        if self.config.extra_env:
            update_subprocess_environment(env, self.config.extra_env)

        self.process = await self._spawn_async_subprocess(cmd, env)
        self._connected = True
        self._last_used = time.monotonic()

        # Start reader tasks (store refs for cancellation on disconnect)
        self._reader_tasks = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
        ]

        await self._initialize()
        process = self.process
        if not self._connected or process is None or process.returncode is not None:
            raise ConnectionError("ACP subprocess disconnected during initialization")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ACPProtocolError("ACP initialization did not establish a non-empty sessionId")
        logger.info("ACP session established")

    async def _spawn_async_subprocess(
        self,
        cmd: list[str],
        env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        """Spawn without losing ownership if cancellation wins the await race."""
        task = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.config.cwd or None,
                env=env,
                limit=10 * 1024 * 1024,  # 10 MB; the default 64 KB is too small for large JSON-RPC lines.
                **_process_group_kwargs(),
            )
        )
        self._async_spawn_task = task
        try:
            process = await asyncio.shield(task)
        except BaseException:
            cleanup = asyncio.create_task(self._claim_and_reap_late_async_spawn(task))
            self._async_spawn_cleanup_task = cleanup

            def _consume_cleanup_result(done: asyncio.Task[None]) -> None:
                if not done.cancelled():
                    with suppress(Exception):
                        done.exception()

            cleanup.add_done_callback(_consume_cleanup_result)
            raise

        if self._async_spawn_task is task:
            self._async_spawn_task = None
        return process

    async def _claim_and_reap_late_async_spawn(
        self,
        task: asyncio.Task[asyncio.subprocess.Process],
    ) -> None:
        """Claim a process returned after its connect attempt was cancelled."""
        try:
            process = await asyncio.shield(task)
        except BaseException as exc:
            if not isinstance(exc, asyncio.CancelledError):
                logger.debug(
                    "Late ACP subprocess spawn failed: %s",
                    exception_summary(exc),
                    exc_info=sensitive_logging_enabled(),
                )
            if self._async_spawn_task is task:
                self._async_spawn_task = None
            if self._async_spawn_cleanup_task is asyncio.current_task():
                self._async_spawn_cleanup_task = None
            return

        self.process = process
        try:
            await self._disconnect_without_connect_lock()
        except BaseException as exc:
            # Keep ``self.process`` on failure so a pool-owned disconnect can
            # retry instead of losing a live transport handle.
            logger.error("Late ACP subprocess cleanup failed: %s", exception_summary(exc))
            raise
        finally:
            if self._async_spawn_task is task:
                self._async_spawn_task = None
            if self._async_spawn_cleanup_task is asyncio.current_task():
                self._async_spawn_cleanup_task = None

    def _reset_protocol_state(self) -> None:
        """Clear metadata owned by a previous logical transport."""
        self._retire_rpc_lifecycles()
        self.session_id = None
        self.agent_capabilities = {}
        self.agent_info = None
        self.auth_methods = []
        self._explicit_auth_succeeded = False
        self._clear_session_state()
        self._permission_turn.active = False
        self._permission_turn = _PermissionTurnState()
        self._session_transition_id = None
        self._session_transition_active = False
        self._pool_reusable = True
        self._stderr_buffer.clear()

    def _clear_session_state(self) -> None:
        """Clear metadata owned by the current logical session."""
        self._session_state_generation += 1
        self.session_config_options = []
        self.session_mode_state = None
        self.session_info = {}
        self.session_usage = None
        self.available_commands = []

    def _begin_permission_turn(self) -> None:
        # Queued requests retain the old object's cancellation state.
        self._permission_turn.active = False
        self._permission_turn = _PermissionTurnState(active=True)

    def _snapshot_session_state(self) -> _SessionStateSnapshot:
        return _SessionStateSnapshot(
            session_id=self.session_id,
            config_options=copy.deepcopy(self.session_config_options),
            mode_state=copy.deepcopy(self.session_mode_state),
            info=copy.deepcopy(self.session_info),
            usage=copy.deepcopy(self.session_usage),
            available_commands=copy.deepcopy(self.available_commands),
        )

    def _restore_session_state(self, snapshot: _SessionStateSnapshot) -> None:
        self._session_state_generation += 1
        self.session_id = snapshot.session_id
        self.session_config_options = snapshot.config_options
        self.session_mode_state = snapshot.mode_state
        self.session_info = snapshot.info
        self.session_usage = snapshot.usage
        self.available_commands = snapshot.available_commands

    async def resume(self, session_id: str) -> bool:
        """Resume a previous session. Returns True on success.

        Subclasses should override with provider-specific resume logic.
        """
        return False

    def supports_session_capability(self, capability: str) -> bool:
        """Return whether initialization advertised a session capability."""
        if not isinstance(capability, str) or not capability:
            raise ValueError("capability must be a non-empty string")
        session_capabilities = self.agent_capabilities.get("sessionCapabilities")
        return isinstance(session_capabilities, dict) and isinstance(session_capabilities.get(capability), dict)

    def supports_auth_capability(self, capability: str) -> bool:
        """Return whether initialization advertised an authentication capability."""
        if not isinstance(capability, str) or not capability:
            raise ValueError("capability must be a non-empty string")
        auth_capabilities = self.agent_capabilities.get("auth")
        return isinstance(auth_capabilities, dict) and isinstance(auth_capabilities.get(capability), dict)

    async def authenticate(self, method_id: str) -> dict[str, Any]:
        """Authenticate with one method advertised during initialization."""
        self._check_explicit_authentication(method_id)
        if not isinstance(method_id, str) or not method_id:
            raise ValueError("method_id must be a non-empty string")
        _require_valid_unicode(method_id, "method_id")
        if method_id not in {method.get("id") for method in self.auth_methods if isinstance(method, dict)}:
            raise ACPUnsupportedCapabilityError(f"authentication method {method_id}")
        result = await self._rpc(
            "authenticate",
            {"methodId": method_id},
            timeout=self.config.timeout_connect,
        )
        return self._validate_object_result(result, method="authenticate")

    def _check_explicit_authentication(self, method_id: object) -> None:
        if self._auth_method_id is None:
            return
        if self.config.provider != self._auth_provider or method_id != self._auth_method_id:
            raise ACPAuthenticationError()
        matches = [method for method in self.auth_methods if method.get("id") == method_id]
        if len(matches) != 1 or len({method["id"] for method in self.auth_methods}) != len(self.auth_methods):
            raise ACPAuthenticationError()

    async def _authenticate_explicitly(self) -> None:
        if self._auth_method_id is not None:
            self._check_explicit_authentication(self._auth_method_id)
            await self.authenticate(self._auth_method_id)

    def _check_authentication_rpc(self, method: str, params: dict) -> None:
        if self._auth_method_id is None:
            return
        if self.config.provider != self._auth_provider:
            raise ACPAuthenticationError()
        if method == "authenticate":
            self._check_explicit_authentication(params.get("methodId"))
        elif (
            method in {"session/new", "session/load", "session/resume", "session/prompt"}
            and not self._explicit_auth_succeeded
        ):
            raise ACPAuthenticationError()

    async def logout(self) -> dict[str, Any]:
        """End authenticated state when the agent advertises ``auth.logout``."""
        if not self.supports_auth_capability("logout"):
            raise ACPUnsupportedCapabilityError("auth.logout")
        result = await self._rpc("logout", {}, timeout=self.config.timeout_connect)
        validated = self._validate_object_result(result, method="logout")
        self._pool_reusable = False
        return validated

    async def close_session(self, session_id: str | None = None) -> dict[str, Any]:
        """Close an active logical session when the agent advertises support.

        Closing the client's current session clears its local session metadata.
        Pool users should let the lease retire this client instead of issuing
        further requests on the now sessionless transport.
        """
        if not self.supports_session_capability("close"):
            raise ACPUnsupportedCapabilityError("sessionCapabilities.close")
        target = self.session_id if session_id is None else session_id
        if not isinstance(target, str) or not target:
            raise ValueError("session_id must be a non-empty string or an active session must exist")
        _require_valid_unicode(target, "session_id")
        result = await self._rpc(
            "session/close",
            {"sessionId": target},
            timeout=self.config.timeout_connect,
        )
        validated = self._validate_object_result(result, method="session/close")
        if target == self.session_id:
            self.session_id = None
            self._clear_session_state()
            self._pool_reusable = False
        return validated

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Return one capability-gated ``session/list`` page."""
        if not self.supports_session_capability("list"):
            raise ACPUnsupportedCapabilityError("sessionCapabilities.list")
        if cwd is not None:
            if not isinstance(cwd, str) or not cwd or "\x00" in cwd:
                raise ValueError("cwd must be a non-empty string without NUL bytes when provided")
            _require_valid_unicode(cwd, "cwd")
            if not _is_absolute_path(cwd):
                raise ValueError("cwd must be an absolute path when provided")
        if cursor is not None and not isinstance(cursor, str):
            raise ValueError("cursor must be a string when provided")
        if cursor is not None:
            _require_valid_unicode(cursor, "cursor")

        params: dict[str, str] = {}
        if cwd is not None:
            params["cwd"] = cwd
        if cursor is not None:
            params["cursor"] = cursor
        result = await self._rpc("session/list", params, timeout=self.config.timeout_connect)
        return self._validate_session_list_result(result)

    async def delete_session(self, session_id: str) -> None:
        """Delete a non-active session from an agent's session history."""
        if not self.supports_session_capability("delete"):
            raise ACPUnsupportedCapabilityError("sessionCapabilities.delete")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        _require_valid_unicode(session_id, "session_id")
        if session_id == self.session_id:
            raise ValueError("cannot delete the active ACP session")
        result = await self._rpc(
            "session/delete",
            {"sessionId": session_id},
            timeout=self.config.timeout_connect,
        )
        self._validate_object_result(result, method="session/delete")

    async def set_config_option(self, config_id: str, value: str | bool) -> list[dict[str, Any]]:
        """Set an advertised session option and return the complete new state."""
        if self._session_transition_active:
            raise ACPProtocolError("Cannot set config during session transition")
        if not self.session_id:
            raise RuntimeError("No active ACP session")
        if not isinstance(config_id, str) or not config_id:
            raise ValueError("config_id must be a non-empty string")
        if not isinstance(value, (str, bool)):
            raise TypeError("config option value must be a string or boolean")
        _require_valid_unicode(config_id, "config_id")
        if isinstance(value, str):
            _require_valid_unicode(value, "config option value")

        option = next((item for item in self.session_config_options if item.get("id") == config_id), None)
        if option is None:
            raise ACPUnsupportedCapabilityError(
                f"session config option {config_id}", public_capability="missing session config option"
            )
        option_type = option.get("type")
        if option_type == "boolean" and not isinstance(value, bool):
            raise TypeError("config option requires a boolean value")
        if option_type == "select":
            if not isinstance(value, str):
                raise TypeError("config option requires a string value")
            choices = self._select_option_values(option.get("options"), config_id=config_id)
            if value not in choices:
                raise ValueError("invalid value for session config option")

        params: dict[str, Any] = {
            "sessionId": self.session_id,
            "configId": config_id,
            "value": value,
        }
        if isinstance(value, bool):
            params["type"] = "boolean"
        generation = self._session_state_generation
        session_id = self.session_id
        await self._rpc("session/set_config_option", params, timeout=self.config.timeout_connect)
        if self.session_id != session_id or self._session_state_generation != generation:
            raise ACPProtocolError("Session changed during config request")
        return copy.deepcopy(self.session_config_options)

    async def set_mode(self, mode_id: str) -> dict[str, Any]:
        """Set an advertised legacy mode; prefer a mode config option when available."""
        if not self.session_id:
            raise RuntimeError("No active ACP session")
        if not isinstance(mode_id, str) or not mode_id:
            raise ValueError("mode_id must be a non-empty string")
        _require_valid_unicode(mode_id, "mode_id")
        state = self.session_mode_state
        if state is None:
            raise ACPUnsupportedCapabilityError("session modes")
        available = state.get("availableModes", [])
        if mode_id not in {mode.get("id") for mode in available if isinstance(mode, dict)}:
            raise ValueError("mode was not advertised for this session")

        revision = self._mode_state_revision
        session_id = self.session_id
        result = await self._rpc(
            "session/set_mode",
            {"sessionId": self.session_id, "modeId": mode_id},
            timeout=self.config.timeout_connect,
        )
        self._validate_object_result(result, method="session/set_mode")
        if self.session_id != session_id or self.session_mode_state is not state:
            raise ACPProtocolError("Session changed during mode request")
        # Preserve custom/mock _rpc implementations, without overwriting a
        # real response or later current_mode_update already applied by reader.
        if self._mode_state_revision == revision:
            state["currentModeId"] = mode_id
            self._mode_state_revision += 1
        return copy.deepcopy(state)

    async def query(self, prompt: str) -> str:
        """Send a prompt under one end-to-end deadline."""
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        _require_valid_unicode(prompt, "prompt")
        if not self.session_id:
            raise RuntimeError("No active ACP session")
        async with self._query_lock:
            return await self._query_locked(prompt)

    async def _query_locked(self, prompt: str) -> str:
        session_id = self.session_id
        if not session_id:
            raise RuntimeError("No active ACP session")
        self._begin_permission_turn()
        permission_turn = self._permission_turn
        self._chunks = []
        self._chunk_chars = 0
        self._complete = asyncio.Event()
        self._streaming = False
        self._stream_queue = None
        self._activity_queued = False
        self._last_used = time.monotonic()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.timeout_prompt

        try:
            try:
                result = await self._rpc(
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": prompt}],
                    },
                    timeout=max(0.0, deadline - loop.time()),
                )
                self._require_prompt_stop_reason(result)
                self._complete.set()
            except asyncio.TimeoutError as exc:
                await self._cleanup_after_prompt_failure("prompt RPC timeout")
                _raise_private_cause(
                    ACPPromptTimeoutError(
                        self.config.timeout_prompt,
                        partial_output="".join(self._chunks),
                    ),
                    exc,
                )
            except ACPProtocolError as exc:
                await self._cleanup_after_prompt_failure("invalid prompt response")
                raise self._with_partial_output(exc) from None
            except ACPRequestOutcomeUnknownError as exc:
                await self._cleanup_after_prompt_failure("unknown prompt outcome")
                wrapped = self._with_partial_output(exc)
                if wrapped is exc:
                    raise
                raise wrapped from wrapped.__cause__
            except Exception as exc:
                logger.warning(
                    "ACP query error: chunks=%d err=%s",
                    len(self._chunks),
                    exception_summary(exc),
                )
                wrapped = self._with_partial_output(exc)
                if wrapped is exc:
                    raise
                raise wrapped from wrapped.__cause__

            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                await wait_for_owned(self._complete.wait(), remaining)
            except asyncio.TimeoutError as exc:
                await self._cleanup_after_prompt_failure("prompt completion timeout")
                raise ACPPromptTimeoutError(
                    self.config.timeout_prompt,
                    partial_output="".join(self._chunks),
                ) from exc

            # Some translators emit the end marker just before the final chunk.
            remaining = deadline - loop.time()
            if self._chunks and remaining > 0:
                await asyncio.sleep(min(0.05, remaining))
            return "".join(self._chunks)
        except asyncio.CancelledError as cancelled:
            try:
                await self.disconnect()
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                logger.error("ACP query cancellation cleanup failed: %s", exception_summary(exc))
            raise cancelled
        except ACPPromptTimeoutError:
            raise
        except asyncio.TimeoutError as exc:
            await self._cleanup_after_prompt_failure("prompt deadline exceeded")
            raise ACPPromptTimeoutError(
                self.config.timeout_prompt,
                partial_output="".join(self._chunks),
            ) from exc
        finally:
            permission_turn.active = False
            self._last_used = time.monotonic()

    async def query_stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Stream chunks while enforcing prompt and ACP-activity timeouts."""
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        _require_valid_unicode(prompt, "prompt")
        if not self.session_id:
            raise RuntimeError("No active ACP session")
        async with self._query_lock:
            stream = self._query_stream_locked(prompt)
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.aclose()

    async def _query_stream_locked(self, prompt: str) -> AsyncGenerator[str, None]:
        session_id = self.session_id
        if not session_id:
            raise RuntimeError("No active ACP session")
        self._begin_permission_turn()
        permission_turn = self._permission_turn
        self._chunks = []
        self._chunk_chars = 0
        complete = asyncio.Event()
        self._complete = complete
        self._stream_queue = asyncio.Queue()
        self._streaming = True
        self._activity_queued = False
        self._stream_error = None
        self._last_used = time.monotonic()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.timeout_prompt

        async def _send() -> None:
            try:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                result = await self._rpc(
                    "session/prompt",
                    {
                        "sessionId": session_id,
                        "prompt": [{"type": "text", "text": prompt}],
                    },
                    timeout=remaining,
                )
                self._require_prompt_stop_reason(result)
                complete.set()
                if self._stream_queue:
                    self._stream_queue.put_nowait(None)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                self._stream_error = ACPPromptTimeoutError(
                    self.config.timeout_prompt,
                    partial_output="".join(self._chunks),
                )
                _bind_private_cause(self._stream_error, exc)
                if self._stream_queue:
                    self._stream_queue.put_nowait(None)
            except Exception as exc:
                # If the caller invoked disconnect() while the RPC was
                # in-flight, the failure is expected (subprocess terminated
                # or stdin was closed). Don't pollute logs at error level.
                if not self._connected:
                    logger.debug(
                        "ACP RPC ended after disconnect (expected): %s",
                        exception_summary(exc),
                    )
                else:
                    logger.error(
                        "ACP stream RPC error: %s",
                        exception_summary(exc),
                    )
                self._stream_error = self._with_partial_output(exc)
                if self._stream_queue:
                    self._stream_queue.put_nowait(None)
            finally:
                permission_turn.active = False

        send_task = asyncio.create_task(_send())
        completed = False
        terminal_error: BaseException | None = None
        active_error: BaseException | None = None

        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    terminal_error = ACPPromptTimeoutError(
                        self.config.timeout_prompt,
                        partial_output="".join(self._chunks),
                    )
                    break
                stream_timeout = float(self.config.timeout_stream)
                prompt_deadline_is_first = remaining <= stream_timeout
                wait_timeout = min(stream_timeout, remaining)
                try:
                    item = await wait_for_owned(self._stream_queue.get(), wait_timeout)
                except asyncio.TimeoutError:
                    if prompt_deadline_is_first:
                        terminal_error = ACPPromptTimeoutError(
                            self.config.timeout_prompt,
                            partial_output="".join(self._chunks),
                        )
                    else:
                        terminal_error = ACPStreamTimeoutError(
                            self.config.timeout_stream,
                            partial_output="".join(self._chunks),
                        )
                    break
                if item is _STREAM_ACTIVITY:
                    self._activity_queued = False
                    continue
                if item is None:
                    # The sentinel is emitted only after the correlated
                    # session/prompt response carries its required stopReason.
                    while not self._stream_queue.empty():
                        queued = self._stream_queue.get_nowait()
                        if queued is not _STREAM_ACTIVITY and queued is not None:
                            yielded_at = loop.time()
                            try:
                                yield str(queued)
                            finally:
                                # Provider deadlines must not include time the
                                # downstream consumer spends between chunks.
                                deadline += loop.time() - yielded_at
                    completed = self._stream_error is None
                    break
                yielded_at = loop.time()
                try:
                    yield str(item)
                finally:
                    deadline += loop.time() - yielded_at
        except BaseException as exc:
            active_error = exc
            raise
        finally:
            cleanup_error: BaseException | None = None
            try:
                if not completed:
                    await self._cancel_after_timeout()
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                cleanup_error = exc
            finally:
                self._streaming = False
                self._activity_queued = False
                self._stream_queue = None
                self._last_used = time.monotonic()
                if not completed and not send_task.done():
                    send_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await send_task

            if cleanup_error is not None:
                primary_error = None if isinstance(active_error, GeneratorExit) else active_error
                primary_error = primary_error or terminal_error or self._stream_error
                if primary_error is None:
                    raise cleanup_error
                logger.error(
                    "ACP stream cleanup failed after prompt failure: %s",
                    exception_summary(cleanup_error),
                )

        if terminal_error is not None:
            raise terminal_error
        if self._stream_error is not None:
            err = self._stream_error
            self._stream_error = None
            raise err

    def _with_partial_output(self, error: Exception) -> Exception:
        partial = "".join(self._chunks)
        if not partial:
            return error
        try:
            error.partial_output = partial  # type: ignore[attr-defined]
            return error
        except Exception:
            return ACPPartialResponseError(error, partial_output=partial)

    @classmethod
    def _require_prompt_stop_reason(cls, result: Any) -> str:
        if isinstance(result, dict):
            cls._validate_meta(result, source="session/prompt response")
            stop_reason = result.get("stopReason")
            if stop_reason in ACP_PROMPT_STOP_REASONS:
                return stop_reason
            if isinstance(stop_reason, str):
                raise ACPProtocolError("session/prompt returned an unknown stopReason")
        raise ACPProtocolError("session/prompt response is missing a valid stopReason")

    @classmethod
    def _validate_initialize_result(cls, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ACPProtocolError("initialize response result must be an object")
        version = result.get("protocolVersion")
        if isinstance(version, bool) or version != ACP_PROTOCOL_VERSION:
            raise ACPProtocolError(
                f"ACP protocol version mismatch: expected {ACP_PROTOCOL_VERSION}", details={"protocolVersion": version}
            )
        cls._validate_meta(result, source="initialize response")
        return result

    @classmethod
    def _validate_object_result(cls, result: Any, *, method: str) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ACPProtocolError(f"{method} response result must be an object")
        cls._validate_meta(result, source=f"{method} response")
        return copy.deepcopy(result)

    @classmethod
    def _validate_agent_capabilities(cls, value: Any) -> dict[str, Any]:
        source = "initialize agentCapabilities"
        if not isinstance(value, dict):
            raise ACPProtocolError(f"{source} must be an object")
        cls._validate_meta(value, source=source)

        if "loadSession" in value and not isinstance(value["loadSession"], bool):
            raise ACPProtocolError(f"{source} loadSession must be a boolean")

        for field, boolean_fields in (
            ("promptCapabilities", ("image", "audio", "embeddedContext")),
            ("mcpCapabilities", ("http", "sse")),
        ):
            if field not in value:
                continue
            nested = value[field]
            if not isinstance(nested, dict):
                raise ACPProtocolError(f"{source} {field} must be an object")
            cls._validate_meta(nested, source=f"{source} {field}")
            for boolean_field in boolean_fields:
                if boolean_field in nested and not isinstance(nested[boolean_field], bool):
                    raise ACPProtocolError(f"{source} {field}.{boolean_field} must be a boolean")

        session_capabilities = value.get("sessionCapabilities")
        if "sessionCapabilities" in value:
            if not isinstance(session_capabilities, dict):
                raise ACPProtocolError(f"{source} sessionCapabilities must be an object")
            cls._validate_meta(session_capabilities, source=f"{source} sessionCapabilities")
            for capability in ("list", "delete", "additionalDirectories", "resume", "close"):
                if capability not in session_capabilities:
                    continue
                advertised = session_capabilities[capability]
                if advertised is not None and not isinstance(advertised, dict):
                    raise ACPProtocolError(f"{source} sessionCapabilities.{capability} must be an object or null")
                if isinstance(advertised, dict):
                    cls._validate_meta(
                        advertised,
                        source=f"{source} sessionCapabilities.{capability}",
                    )

        auth_capabilities = value.get("auth")
        if "auth" in value:
            if not isinstance(auth_capabilities, dict):
                raise ACPProtocolError(f"{source} auth must be an object")
            cls._validate_meta(auth_capabilities, source=f"{source} auth")
            if "logout" in auth_capabilities:
                logout = auth_capabilities["logout"]
                if logout is not None and not isinstance(logout, dict):
                    raise ACPProtocolError(f"{source} auth.logout must be an object or null")
                if isinstance(logout, dict):
                    cls._validate_meta(logout, source=f"{source} auth.logout")

        return copy.deepcopy(value)

    def _capture_initialize_metadata(self, result: Any) -> None:
        result = self._validate_initialize_result(result)

        capabilities = self._validate_agent_capabilities(result.get("agentCapabilities", {}))
        agent_info = result.get("agentInfo")
        if agent_info is not None and not isinstance(agent_info, dict):
            raise ACPProtocolError("initialize agentInfo must be an object")
        if isinstance(agent_info, dict):
            self._validate_meta(agent_info, source="initialize agentInfo")
            if not isinstance(agent_info.get("name"), str) or not agent_info["name"]:
                raise ACPProtocolError("initialize agentInfo requires a non-empty name")
            if not isinstance(agent_info.get("version"), str) or not agent_info["version"]:
                raise ACPProtocolError("initialize agentInfo requires a non-empty version")
            title = agent_info.get("title")
            if title is not None and not isinstance(title, str):
                raise ACPProtocolError("initialize agentInfo title must be a string or null")
        auth_methods = result.get("authMethods", [])
        if not isinstance(auth_methods, list) or any(not isinstance(item, dict) for item in auth_methods):
            raise ACPProtocolError("initialize authMethods must be an array of objects")
        if any(not isinstance(item.get("id"), str) or not item["id"] for item in auth_methods):
            raise ACPProtocolError("initialize authMethods entries require a non-empty id")
        if any(not isinstance(item.get("name"), str) or not item["name"] for item in auth_methods):
            raise ACPProtocolError("initialize authMethods entries require a non-empty name")
        if any(
            item.get("description") is not None and not isinstance(item["description"], str) for item in auth_methods
        ):
            raise ACPProtocolError("initialize authMethods descriptions must be strings or null")
        for index, item in enumerate(auth_methods):
            self._validate_meta(item, source=f"initialize authMethods entry {index}")

        self.agent_capabilities = capabilities
        self.agent_info = copy.deepcopy(agent_info)
        self.auth_methods = copy.deepcopy(auth_methods)

    @staticmethod
    def _validate_config_options(value: Any, *, source: str) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ACPProtocolError(f"{source} configOptions must be an array")
        options: list[dict[str, Any]] = []
        config_ids: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                raise ACPProtocolError(f"{source} configOptions entries must be objects")
            config_id = item.get("id")
            if not isinstance(config_id, str) or not config_id:
                raise ACPProtocolError(f"{source} config option requires a non-empty id")
            if config_id in config_ids:
                raise ACPProtocolError(
                    f"{source} configOptions contains duplicate id", details={"config_id": config_id}
                )
            config_ids.add(config_id)
            name = item.get("name")
            if not isinstance(name, str) or not name:
                raise ACPProtocolError(
                    f"{source} config option requires a non-empty name", details={"config_id": config_id}
                )
            description = item.get("description")
            if description is not None and not isinstance(description, str):
                raise ACPProtocolError(
                    f"{source} config option description must be a string or null", details={"config_id": config_id}
                )
            category = item.get("category")
            if category is not None and not isinstance(category, str):
                raise ACPProtocolError(
                    f"{source} config option category must be a string or null", details={"config_id": config_id}
                )
            ACPClientBase._validate_meta(item, source=f"{source} config option", details={"config_id": config_id})

            option_type = item.get("type")
            current_value = item.get("currentValue")
            if option_type == "boolean":
                if not isinstance(current_value, bool):
                    raise ACPProtocolError(
                        f"{source} boolean config option requires a boolean currentValue",
                        details={"config_id": config_id},
                    )
            elif option_type == "select":
                if not isinstance(current_value, str) or not current_value:
                    raise ACPProtocolError(
                        f"{source} select config option requires a non-empty currentValue",
                        details={"config_id": config_id},
                    )
                choices = ACPClientBase._select_option_values(item.get("options"), config_id=config_id)
                if current_value not in choices:
                    raise ACPProtocolError(
                        f"{source} config option currentValue was not advertised in options",
                        details={"config_id": config_id},
                    )
            else:
                raise ACPProtocolError(
                    f"{source} config option requires type 'select' or 'boolean'", details={"config_id": config_id}
                )
            options.append(copy.deepcopy(item))
        return options

    @staticmethod
    def _select_option_values(value: Any, *, config_id: str) -> set[str]:
        """Return values from either ACP flat or grouped select options."""
        if not isinstance(value, list):
            raise ACPProtocolError("config option options must be an array", details={"config_id": config_id})
        shape: str | None = None
        values: set[str] = set()
        group_ids: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                raise ACPProtocolError("config option options must contain objects", details={"config_id": config_id})
            if "value" in item:
                if shape == "grouped":
                    raise ACPProtocolError(
                        "config option cannot mix flat and grouped options", details={"config_id": config_id}
                    )
                shape = "flat"
                candidates = [item]
            else:
                if shape == "flat":
                    raise ACPProtocolError(
                        "config option cannot mix flat and grouped options", details={"config_id": config_id}
                    )
                shape = "grouped"
                group_id = item.get("group")
                group_name = item.get("name")
                candidates = item.get("options")
                if not isinstance(group_id, str) or not group_id or not isinstance(group_name, str) or not group_name:
                    raise ACPProtocolError(
                        "config option group requires non-empty group and name", details={"config_id": config_id}
                    )
                if group_id in group_ids:
                    raise ACPProtocolError(
                        "config option contains duplicate group", details={"config_id": config_id, "group_id": group_id}
                    )
                group_ids.add(group_id)
                ACPClientBase._validate_meta(
                    item, source="config option group", details={"config_id": config_id, "group_id": group_id}
                )
                if not isinstance(candidates, list):
                    raise ACPProtocolError(
                        "config option group options must be an array",
                        details={"config_id": config_id, "group_id": group_id},
                    )
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    raise ACPProtocolError(
                        "config option options must contain objects", details={"config_id": config_id}
                    )
                option_value = candidate.get("value")
                option_name = candidate.get("name")
                if not isinstance(option_value, str) or not option_value:
                    raise ACPProtocolError(
                        "config option value requires a non-empty string", details={"config_id": config_id}
                    )
                if not isinstance(option_name, str) or not option_name:
                    raise ACPProtocolError(
                        "config option value requires a non-empty name",
                        details={"config_id": config_id, "value": option_value},
                    )
                option_description = candidate.get("description")
                if option_description is not None and not isinstance(option_description, str):
                    raise ACPProtocolError(
                        "config option value description must be a string or null",
                        details={"config_id": config_id, "value": option_value},
                    )
                ACPClientBase._validate_meta(
                    candidate,
                    source="config option value",
                    details={"config_id": config_id, "value": option_value},
                )
                if option_value in values:
                    raise ACPProtocolError(
                        "config option contains duplicate value",
                        details={"config_id": config_id, "value": option_value},
                    )
                values.add(option_value)
        return values

    @staticmethod
    def _validate_session_modes(value: Any, *, source: str) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ACPProtocolError(f"{source} modes must be an object or null")
        ACPClientBase._validate_meta(value, source=f"{source} modes")
        current = value.get("currentModeId")
        available = value.get("availableModes")
        if not isinstance(current, str) or not current:
            raise ACPProtocolError(f"{source} modes require a non-empty currentModeId")
        if not isinstance(available, list):
            raise ACPProtocolError(f"{source} modes require an availableModes array")
        mode_ids: set[str] = set()
        for mode in available:
            if not isinstance(mode, dict):
                raise ACPProtocolError(f"{source} availableModes entries must be objects")
            mode_id = mode.get("id")
            name = mode.get("name")
            if not isinstance(mode_id, str) or not mode_id or not isinstance(name, str) or not name:
                raise ACPProtocolError(f"{source} availableModes entries require non-empty id and name")
            description = mode.get("description")
            if description is not None and not isinstance(description, str):
                raise ACPProtocolError(f"{source} availableModes descriptions must be strings or null")
            ACPClientBase._validate_meta(mode, source=f"{source} available mode", details={"mode_id": mode_id})
            if mode_id in mode_ids:
                raise ACPProtocolError(f"{source} availableModes contains duplicate id", details={"mode_id": mode_id})
            mode_ids.add(mode_id)
        if current not in mode_ids:
            raise ACPProtocolError(f"{source} currentModeId was not advertised in availableModes")
        return copy.deepcopy(value)

    @staticmethod
    def _validate_meta(value: dict[str, Any], *, source: str, details: Any = None) -> dict[str, Any] | None:
        meta = value.get("_meta")
        if meta is not None and not isinstance(meta, dict):
            raise ACPProtocolError(f"{source} _meta must be an object or null", details=details)
        return copy.deepcopy(meta)

    def _capture_session_info_update(self, update: dict[str, Any]) -> None:
        source = "session_info_update"
        self._validate_meta(update, source=source)
        next_info = copy.deepcopy(self.session_info)
        for field in ("title", "updatedAt"):
            if field not in update:
                continue
            value = update[field]
            if value is not None and not isinstance(value, str):
                raise ACPProtocolError(f"{source} {field} must be a string or null")
            if value is None:
                next_info.pop(field, None)
            else:
                next_info[field] = value
        if "_meta" in update:
            meta = self._validate_meta(update, source=source)
            if meta is None:
                next_info.pop("_meta", None)
            else:
                next_info["_meta"] = meta
        self.session_info = next_info

    @classmethod
    def _validate_usage_update(cls, update: dict[str, Any]) -> dict[str, Any]:
        source = "usage_update"
        maximum = (1 << 64) - 1
        state: dict[str, Any] = {}
        for field in ("used", "size"):
            value = update.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ACPProtocolError(f"{source} {field} must be a uint64 integer")
            state[field] = value

        if "cost" in update:
            cost = update["cost"]
            if cost is None:
                state["cost"] = None
            else:
                if not isinstance(cost, dict):
                    raise ACPProtocolError(f"{source} cost must be an object or null")
                amount = cost.get("amount")
                currency = cost.get("currency")
                if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not _is_finite_number(amount):
                    raise ACPProtocolError(f"{source} cost.amount must be a finite number")
                if not isinstance(currency, str):
                    raise ACPProtocolError(f"{source} cost.currency must be a string")
                cls._validate_meta(cost, source=f"{source} cost")
                state["cost"] = copy.deepcopy(cost)

        if "_meta" in update:
            state["_meta"] = cls._validate_meta(update, source=source)
        return state

    @classmethod
    def _validate_available_commands_update(cls, update: dict[str, Any]) -> list[dict[str, Any]]:
        source = "available_commands_update"
        commands = update.get("availableCommands")
        if not isinstance(commands, list):
            raise ACPProtocolError(f"{source} requires availableCommands")
        validated: list[dict[str, Any]] = []
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                raise ACPProtocolError(f"{source} entries must be objects")
            if not isinstance(command.get("name"), str) or not isinstance(command.get("description"), str):
                raise ACPProtocolError(f"{source} entry {index} requires string name and description")
            cls._validate_meta(command, source=f"{source} entry {index}")
            command_input = command.get("input")
            if command_input is not None:
                if not isinstance(command_input, dict) or not isinstance(command_input.get("hint"), str):
                    raise ACPProtocolError(f"{source} entry {index} input requires a string hint")
                cls._validate_meta(command_input, source=f"{source} entry {index} input")
            validated.append(copy.deepcopy(command))
        cls._validate_meta(update, source=source)
        return validated

    @classmethod
    def _validate_session_list_result(cls, result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ACPProtocolError("session/list response result must be an object")
        cls._validate_meta(result, source="session/list response")
        sessions = result.get("sessions")
        if not isinstance(sessions, list):
            raise ACPProtocolError("session/list response requires a sessions array")
        for item in sessions:
            if not isinstance(item, dict):
                raise ACPProtocolError("session/list entries must be objects")
            if not isinstance(item.get("sessionId"), str) or not item["sessionId"]:
                raise ACPProtocolError("session/list entry requires a non-empty sessionId")
            cwd = item.get("cwd")
            if not isinstance(cwd, str) or not cwd or not _is_absolute_path(cwd):
                raise ACPProtocolError("session/list entry requires an absolute cwd")
            try:
                _require_valid_unicode(cwd, "session/list cwd")
            except ValueError as exc:
                _raise_private_cause(ACPProtocolError(str(exc)), exc)
            if "additionalDirectories" in item:
                directories = item["additionalDirectories"]
                if not isinstance(directories, list) or any(
                    not isinstance(path, str) or not path or not _is_absolute_path(path) for path in directories
                ):
                    raise ACPProtocolError("session/list additionalDirectories must contain absolute paths")
                try:
                    for path in directories:
                        _require_valid_unicode(path, "session/list additional directory")
                except ValueError as exc:
                    _raise_private_cause(ACPProtocolError(str(exc)), exc)
            for optional_string in ("title", "updatedAt"):
                value = item.get(optional_string)
                if value is not None and not isinstance(value, str):
                    raise ACPProtocolError(f"session/list {optional_string} must be a string or null")
            cls._validate_meta(item, source="session/list entry")
        next_cursor = result.get("nextCursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ACPProtocolError("session/list nextCursor must be a string or null")
        return copy.deepcopy(result)

    def _capture_session_config(self, method: str, result: Any, *, apply: bool = True) -> None:
        if method not in {"session/new", "session/load", "session/resume", "session/set_config_option"}:
            return
        if not isinstance(result, dict):
            raise ACPProtocolError(f"{method} response result must be an object")
        self._validate_meta(result, source=f"{method} response")

        if method == "session/set_config_option":
            if "configOptions" not in result:
                raise ACPProtocolError("session/set_config_option response requires configOptions")
            next_options = self._validate_config_options(result["configOptions"], source=method)
            if apply:
                self.session_config_options = next_options
            return

        has_config_options = "configOptions" in result
        next_options: list[dict[str, Any]] | None = None
        if has_config_options:
            config_options = result["configOptions"]
            next_options = (
                [] if config_options is None else self._validate_config_options(config_options, source=method)
            )

        has_modes = "modes" in result
        next_modes: dict[str, Any] | None = None
        if has_modes:
            next_modes = self._validate_session_modes(result["modes"], source=method)

        if has_config_options:
            self.session_config_options = next_options or []
        if has_modes:
            self.session_mode_state = next_modes
            self._mode_state_revision += 1

    def _register_rpc_lifecycle(self, mid: int, method: str, params: dict[str, Any]) -> _RPCLifecycle:
        if method == "session/set_config_option" and self._session_transition_active:
            # A committed transition still owns its state until its waiter
            # finishes. Reject before any transport writes, including raw RPCs.
            raise ACPProtocolError("Cannot set config during session transition")
        transition_id, previous = self._begin_rpc_lifecycle(method, params)
        context = _RPCLifecycle(transition_id, previous)
        if method == "session/set_mode" and isinstance(params.get("modeId"), str):
            context.mode_id = params["modeId"]
            context.mode_session_id = params.get("sessionId")
            context.mode_state = self.session_mode_state
        if method == "session/set_config_option":
            context.config_session_id = params.get("sessionId")
            context.config_session_generation = self._session_state_generation
        self._rpc_lifecycles[mid] = context
        return context

    def _apply_rpc_lifecycle(self, method: str, result: Any, context: _RPCLifecycle) -> None:
        if context.applied or context.abandoned or context.finished:
            return
        if method == "session/set_config_option" and (
            context.config_session_id != self.session_id
            or context.config_session_generation != self._session_state_generation
        ):
            # This is still the old request's response, so validate and deliver
            # it without applying its snapshot to another logical session.
            # Public set_config_option rejects the changed ownership on return.
            self._capture_session_config(method, result, apply=False)
            context.applied = True
            return
        self._complete_rpc_lifecycle(method, result, context.transition_id)
        if (
            method == "session/set_mode"
            and context.mode_id is not None
            and context.mode_session_id == self.session_id
            and context.mode_state is self.session_mode_state
            and self.session_mode_state is not None
        ):
            self.session_mode_state["currentModeId"] = context.mode_id
            self._mode_state_revision += 1
        context.applied = True

    def _finish_pending_rpc(self, mid: int, context: _RPCLifecycle | None, succeeded: bool) -> None:
        if context is not None and not context.finished:
            # A validated response is wire-confirmed even when cancellation
            # reaches its waiter before that coroutine has resumed.
            self._finish_rpc_lifecycle(context.transition_id, context.previous_state, succeeded or context.applied)
            context.finished = True
        if self._rpc_lifecycles.get(mid) is context:
            self._rpc_lifecycles.pop(mid, None)

    def _retire_rpc_lifecycles(self) -> None:
        # Finish synchronously before another transport can replace the state.
        # Old waiters keep their context and cannot later roll back a new one.
        for mid, context in list(self._rpc_lifecycles.items()):
            context.abandoned = True
            self._finish_pending_rpc(mid, context, False)

    def _begin_rpc_lifecycle(
        self,
        method: str,
        params: dict[str, Any],
    ) -> tuple[str | None, _SessionStateSnapshot | None]:
        """Prepare shared session state before any ACP transport writes."""
        if method not in {"session/new", "session/load", "session/resume"}:
            return None, None
        if self._session_transition_active:
            raise RuntimeError("another ACP session transition is already in progress")

        candidate: str | None = None
        if method != "session/new":
            candidate = params.get("sessionId")
            if not isinstance(candidate, str) or not candidate:
                raise ValueError(f"{method} requires a non-empty sessionId")
            _require_valid_unicode(candidate, f"{method} sessionId")

        previous = self._snapshot_session_state()
        self._clear_session_state()
        self._session_transition_id = candidate
        self._session_transition_active = True
        return candidate, previous

    def _complete_rpc_lifecycle(self, method: str, result: Any, transition_id: str | None) -> None:
        """Validate shared response metadata and commit a session transition."""
        new_session_id: str | None = None
        if method == "initialize":
            self._capture_initialize_metadata(result)
        elif method == "session/prompt":
            self._require_prompt_stop_reason(result)
        elif method == "session/list":
            self._validate_session_list_result(result)
        elif method in {"authenticate", "logout", "session/delete", "session/close", "session/set_mode"}:
            self._validate_object_result(result, method=method)
            if method == "authenticate" and self._auth_method_id is not None:
                self._explicit_auth_succeeded = True
            elif method == "logout":
                self._explicit_auth_succeeded = False
        if method in {"session/new", "session/load", "session/resume"}:
            if not isinstance(result, dict):
                raise ACPProtocolError(f"{method} response result must be an object")
            if method == "session/new":
                session_id = result.get("sessionId")
                if not isinstance(session_id, str) or not session_id:
                    raise ACPProtocolError("session/new response requires a non-empty sessionId")
                try:
                    _require_valid_unicode(session_id, "session/new sessionId")
                except ValueError as exc:
                    _raise_private_cause(ACPProtocolError(str(exc)), exc)
                new_session_id = session_id
        self._capture_session_config(method, result)
        if method in {"session/new", "session/load", "session/resume"}:
            if method == "session/new":
                self.session_id = new_session_id
            self._pool_reusable = True
        if transition_id is not None:
            self.session_id = transition_id

    def _finish_rpc_lifecycle(
        self,
        transition_id: str | None,
        previous_session_state: _SessionStateSnapshot | None,
        succeeded: bool,
    ) -> None:
        """Release transition ownership and restore state after failure."""
        del transition_id
        if previous_session_state is None:
            return
        self._session_transition_id = None
        self._session_transition_active = False
        if not succeeded:
            self._restore_session_state(previous_session_state)

    async def _cancel_after_timeout(self) -> None:
        if not self._connected:
            cleanup = self._process_cleanup_task
            if cleanup is not None and not cleanup.done():
                await _await_owned_cleanup(cleanup, label="interrupted ACP RPC cleanup")
            return
        # A cancellation notification has no acknowledgement in ACP. Close the
        # transport as well so a late response cannot contaminate the next turn
        # when callers catch the timeout inside an active pool lease.
        await self.disconnect()

    async def _cleanup_after_prompt_failure(self, context: str) -> None:
        """Retire a failed prompt transport without masking its primary error."""
        try:
            await self._cancel_after_timeout()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "ACP cleanup failed after %s: %s",
                context,
                exception_summary(exc),
            )

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
        self._permission_turn.cancelled = True
        self._permission_turn.active = False
        try:
            await wait_for_owned(
                self._send_notification(
                    "session/cancel",
                    {"sessionId": self.session_id},
                ),
                min(1.0, self.config.timeout_connect),
            )
        except Exception as e:
            logger.debug("session/cancel notification failed: %s", exception_summary(e))

    async def disconnect(self) -> None:
        """Terminate the CLI subprocess and cancel reader tasks.

        Sends ``session/cancel`` first as a courtesy so the agent can
        flush any in-flight work cleanly before the pipe closes.
        """
        self._permission_turn.active = False
        task = self._disconnect_task
        if task is None or task.done():
            task = asyncio.create_task(self._run_disconnect())
            self._disconnect_task = task
        await _await_owned_cleanup(task, label="ACP client cleanup")

    async def _run_disconnect(self) -> None:
        async with self._connect_lock:
            await self._disconnect_without_connect_lock()

    async def _disconnect_without_connect_lock(self) -> None:
        """Disconnect while the caller already owns, or does not need, the connect lock."""
        async with self._disconnect_lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        # Record the CLI's tools before it is told to stop: the CLI exits on
        # stdin EOF (and may on session/cancel), and a dead parent leaves no
        # tree to walk. The recorded pids are terminated further down.
        early_proc = self.process
        descendants = (
            _snapshot_descendants(early_proc.pid) if isinstance(early_proc, asyncio.subprocess.Process) else set()
        )
        if self._connected and self.session_id:
            with suppress(Exception):
                await asyncio.wait_for(self.cancel(), timeout=2.0)

        self._connected = False
        self._session_transition_id = None
        self._session_transition_active = False
        self._retire_rpc_lifecycles()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("ACP disconnected"))
        self._pending.clear()
        self._inflight.clear()

        current_task = asyncio.current_task()
        reader_tasks = [task for task in self._reader_tasks if task is not current_task]
        for task in reader_tasks:
            if not task.done():
                task.cancel()
        for task in reader_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug(
                    "ACP reader failed during disconnect: %s",
                    exception_summary(exc),
                    exc_info=sensitive_logging_enabled(),
                )
        self._reader_tasks.clear()

        # Reap the CLI before waiting for owned file workers. They may still
        # need to finish, but cannot prevent transport termination.
        termination_error: BaseException | None = None
        try:
            await self._reap_disconnected_transport(descendants)
        except BaseException as exc:
            termination_error = exc
        host_cleanup_error: BaseException | None = None
        try:
            await self._close_host_services()
        except BaseException as exc:
            host_cleanup_error = exc
        if termination_error is not None:
            if host_cleanup_error is not None:
                logger.error("ACP host service cleanup also failed: %s", exception_summary(host_cleanup_error))
            raise termination_error
        if host_cleanup_error is not None:
            raise host_cleanup_error

    async def _reap_disconnected_transport(self, descendants: set[int]) -> None:
        proc = self.process
        spawn_cleanup_pending = (
            self._async_spawn_task is not None and self._async_spawn_cleanup_task is not asyncio.current_task()
        )
        if spawn_cleanup_pending:
            raise RuntimeError("ACP subprocess spawn cleanup is still pending")
        if proc is None:
            return

        if proc.stdin and not proc.stdin.is_closing():
            try:
                proc.stdin.close()
                await asyncio.wait_for(proc.stdin.wait_closed(), timeout=1.0)
            except Exception as exc:
                logger.debug(
                    "Failed to close ACP stdin cleanly: %s",
                    exception_summary(exc),
                    exc_info=sensitive_logging_enabled(),
                )

        termination_error: BaseException | None = None
        # The CLI's tools (shell -> long command) would outlive it as orphans;
        # terminate the ones recorded at the start of this disconnect.
        tree_outcome = await _terminate_process_tree(proc, descendants)
        if tree_outcome is not None:
            logger.debug("ACP process tree termination: %s", tree_outcome)
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

        # Proactor transports can retain Windows pipe handles after wait().
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
            error = RuntimeError("ACP subprocess did not exit after terminate/kill")
            if termination_error is not None:
                _raise_private_cause(error, termination_error)
            raise error from None
        if self.process is proc:
            self.process = None

    async def _prepare_host_services(self) -> None:
        cleanup = self._host_cleanup_task
        if cleanup is not None and cleanup is not asyncio.current_task():
            try:
                await _await_owned_cleanup(cleanup, label="ACP host service cleanup")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(
                    "Previous ACP host service cleanup failed: %s",
                    exception_summary(exc),
                    exc_info=sensitive_logging_enabled(),
                )
        self._host_cleanup_task = None
        if self._host_services_need_cleanup:
            # A direct disconnect can reap the ACP parent while terminal
            # cleanup still fails. Retry before replacing the old service so
            # process ownership cannot be lost during reconnect.
            await self._close_host_services()
        cwd = self.config.cwd or os.getcwd()
        self._fs_service = FSService(cwd, self.config.additional_directories)
        self._terminal_service = TerminalService(
            cwd,
            self.config.additional_directories,
            inherit_parent_env=self.config.inherit_parent_env,
        )
        self._host_services_need_cleanup = True

    async def _close_host_services(self) -> None:
        async with self._host_cleanup_lock:
            request_tasks = [task for task in self._request_tasks if task is not asyncio.current_task()]
            for task in request_tasks:
                task.cancel()
            await self._terminal_service.close()
            if request_tasks:
                await asyncio.gather(*request_tasks, return_exceptions=True)
            self._request_tasks.clear()
            self._request_tasks_by_id.clear()
            self._request_reply_started.clear()
            self._terminal_wait_request_ids.clear()
            self._host_services_need_cleanup = False

    def _schedule_host_cleanup(self) -> None:
        if self._host_cleanup_task is None or self._host_cleanup_task.done():
            task = asyncio.create_task(self._close_host_services())
            self._host_cleanup_task = task

            def _consume_result(done: asyncio.Task[None]) -> None:
                if not done.cancelled():
                    with suppress(Exception):
                        done.exception()

            task.add_done_callback(_consume_result)

    def _schedule_process_cleanup(self) -> None:
        expected_transport = self._transport_identity()
        if expected_transport is None:
            return
        if self._process_cleanup_task is None or self._process_cleanup_task.done():
            task = asyncio.create_task(self._cleanup_transport_if_current(expected_transport))
            self._process_cleanup_task = task

            def _consume_result(done: asyncio.Task[None]) -> None:
                if not done.cancelled():
                    with suppress(Exception):
                        done.exception()

            task.add_done_callback(_consume_result)

    def _transport_identity(self) -> object | None:
        """Return the physical transport object used to reject stale cleanup."""
        return self.process

    async def _cleanup_transport_if_current(self, expected_transport: object) -> None:
        async with self._connect_lock:
            if self._transport_identity() is not expected_transport:
                return
            await self._disconnect_without_connect_lock()

    # ------------------------------------------------------------------
    # Abstract — subclass must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def _get_acp_command(self) -> list[str]:
        """Return the CLI command + arguments to start the subprocess."""
        ...

    def _get_acp_env(self) -> dict[str, str]:
        """Return adapter defaults for the child environment only."""
        return {}

    def _child_env_value(self, name: str, default: str | None = None) -> str | None:
        """Resolve a user-controlled value as the child environment will."""
        configured = get_subprocess_environment_value(self.config.extra_env, name)
        if configured is not None:
            return configured

        if self.config.inherit_parent_env:
            return get_subprocess_environment_value(os.environ, name, default)
        return default

    def _configured_model_id(self) -> str:
        """Return an explicit model ID, or empty when the provider owns selection."""
        model_id = _model_id_for_provider(self.config.model, self.config.provider)
        return "" if model_id.lower() == "default" else model_id

    async def _authenticate_if_advertised(self, initialize_result: dict, *, agent_name: str) -> bool:
        """Try the first advertised auth method without masking transport faults."""
        if self._auth_method_id is not None:
            if not self._explicit_auth_succeeded:
                raise ACPAuthenticationError()
            return True
        auth_methods = initialize_result.get("authMethods", [])
        if not auth_methods:
            return False
        method_id = auth_methods[0]["id"]
        try:
            await self._rpc(
                "authenticate",
                {"methodId": method_id},
                timeout=self.config.timeout_connect,
            )
        except RPCError as exc:
            # Some agents advertise interactive setup even when their inherited
            # credentials are already usable. An explicit RPC rejection is the
            # only safe failure to treat as optional here.
            logger.warning(
                "%s authenticate rejected; continuing with inherited credentials: %s",
                agent_name,
                exception_summary(exc),
            )
            return False
        return True

    async def _resume_if_available(
        self,
        method: str,
        session_id: str,
        *,
        agent_name: str,
        extra_params: dict[str, Any] | None = None,
    ) -> bool:
        """Resume a session while preserving transport and protocol failures."""
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        _require_valid_unicode(session_id, "session_id")
        if method not in {"session/load", "session/resume"}:
            raise ValueError("resume method must be 'session/load' or 'session/resume'")

        # Prompt assembly and session transitions share mutable per-session
        # state. Keep resume/load behind the same lock as query/query_stream so
        # a finishing old prompt cannot overwrite the newly selected session.
        async with self._query_lock:
            if method == "session/load":
                if self.agent_capabilities.get("loadSession") is not True:
                    logger.debug("%s does not advertise loadSession", agent_name)
                    return False
            elif not self.supports_session_capability("resume"):
                logger.debug("%s does not advertise sessionCapabilities.resume", agent_name)
                return False

            params: dict[str, Any] = {
                "sessionId": session_id,
                "cwd": self.config.cwd or os.getcwd(),
                "mcpServers": [],
            }
            if extra_params:
                params.update(extra_params)
            try:
                await self._rpc(method, params, timeout=self.config.timeout_connect)
            except RPCError as exc:
                # Only an explicit missing-resource outcome invalidates the stored
                # mapping. Authentication, internal, rate-limit, and protocol
                # failures may be transient and must remain visible to the pool.
                message = exc.message.lower()[:512]
                missing = exc.code == -32002 or any(marker in message for marker in _MISSING_SESSION_MARKERS)
                if not missing:
                    raise
                logger.debug("%s session is no longer available: %s", agent_name, exception_summary(exc))
                return False
            self.session_id = session_id
            logger.info("%s session resumed", agent_name)
            return True

    async def _set_model_if_supported(self, model_id: str) -> bool:
        """Select a model through advertised config before legacy fallback.

        ``session/set_model`` is not part of stable ACP v1 and has no
        capability bit. Current agents expose model selection as a session
        config option instead. Keep the legacy request only for agents that do
        not advertise a model option at all.
        """
        model_option = next(
            (
                option
                for option in self.session_config_options
                if option.get("type") == "select" and option.get("category") == "model"
            ),
            None,
        )
        if model_option is None:
            # ``category`` is optional in ACP. These conventional IDs retain
            # compatibility without guessing from localized display names.
            model_option = next(
                (
                    option
                    for option in self.session_config_options
                    if option.get("type") == "select" and option.get("id") in {"model", "models"}
                ),
                None,
            )

        if model_option is not None:
            config_id = model_option["id"]
            current = model_option["currentValue"]
            if current == model_id:
                logger.debug("Configured ACP model is already selected")
                return True

            choices = self._select_option_values(model_option["options"], config_id=config_id)
            if model_id not in choices:
                logger.warning("Configured ACP model was not advertised; using the current provider model")
                return False

            try:
                await self.set_config_option(config_id, model_id)
            except RPCError as exc:
                logger.warning(
                    "session/set_config_option rejected the configured model; using the current provider model: %s",
                    exception_summary(exc),
                )
                return False
            logger.info("ACP model selection applied")
            return True

        cache_key = (type(self), self._command_identity) if self._command_identity is not None else None
        if cache_key is not None and cache_key in _UNSUPPORTED_LEGACY_MODEL_METHODS:
            logger.debug("Skipping unsupported legacy session/set_model")
            return False

        try:
            await self._rpc(
                "session/set_model",
                {"sessionId": self.session_id, "modelId": model_id},
                timeout=self.config.timeout_connect,
            )
        except RPCError as exc:
            if exc.code == -32601 and cache_key is not None:
                first_rejection = cache_key not in _UNSUPPORTED_LEGACY_MODEL_METHODS
                _UNSUPPORTED_LEGACY_MODEL_METHODS.add(cache_key)
                log = logger.warning if first_rejection else logger.debug
                log(
                    "session/set_model rejected; using provider default: %s",
                    exception_summary(exc),
                )
            else:
                logger.warning(
                    "session/set_model rejected; using provider default: %s",
                    exception_summary(exc),
                )
            return False
        logger.info("ACP model selection applied")
        return True

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
        process = self.process
        stdin = process.stdin if process is not None else None
        if not self._connected or stdin is None:
            raise ConnectionError("Not connected")
        msg = _encode_json_line(
            {
                "jsonrpc": "2.0",
                "method": method,
                **({"params": params} if params else {}),
            }
        )
        stdin.write(msg)
        await wait_for_owned(stdin.drain(), self.config.timeout_connect)
        logger.debug("Sent notification: %s", method)

    async def _notify_request_cancel(self, request_id: int) -> None:
        """Best-effort ACP cancellation for one outbound JSON-RPC request."""
        if not self._connected:
            return
        try:
            await wait_for_owned(
                self._send_notification("$/cancel_request", {"requestId": request_id}),
                min(1.0, self.config.timeout_connect),
            )
        except asyncio.CancelledError:
            # The caller still propagates its original cancellation. A repeated
            # cancellation must not strand request bookkeeping in this helper.
            return
        except Exception as exc:
            logger.debug(
                "$/cancel_request notification failed for id=%s: %s",
                request_id,
                exception_summary(exc),
            )

    async def _invalidate_uncertain_rpc_transport(self, method: str) -> None:
        """Retire a transport after an interrupted request may have been sent."""
        self._pool_reusable = False
        if self._connected:
            self._connected = False
            logger.debug("Retiring ACP transport after interrupted RPC method=%s", method)
            self._schedule_host_cleanup()
            self._schedule_process_cleanup()

        cleanup = self._process_cleanup_task
        # initialize RPCs run while connect owns this lock; their outer
        # lifecycle performs the same cleanup after _rpc returns.
        if cleanup is not None and not cleanup.done() and not self._connect_lock.locked():
            try:
                await _await_owned_cleanup(cleanup, label="interrupted ACP RPC cleanup")
            except BaseException as exc:
                logger.error(
                    "ACP transport cleanup failed after interrupted method=%s: %s",
                    method,
                    exception_summary(exc),
                )

    async def _await_rpc_future(self, future: asyncio.Future[Any], deadline: float) -> Any:
        """Wait for one response while detecting a dead subprocess promptly."""
        loop = asyncio.get_running_loop()
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            done, _pending = await asyncio.wait(
                {future},
                timeout=min(RPC_PROCESS_POLL_INTERVAL, remaining),
            )
            if future in done:
                return future.result()
            if self.is_connected:
                continue

            # Process exit can become visible before a final buffered frame is
            # dispatched. Preserve a short absolute drain window, then stop
            # waiting on a pipe that a descendant may still have inherited.
            remaining = deadline - loop.time()
            if remaining > 0:
                done, _pending = await asyncio.wait(
                    {future},
                    timeout=min(RPC_EXIT_DRAIN_TIMEOUT, remaining),
                )
                if future in done:
                    return future.result()
            raise ConnectionError("ACP subprocess exited before completing the RPC")

    async def _rpc(self, method: str, params: dict, timeout: float | None = None) -> Any:
        """Send a JSON-RPC request and wait for the response.

        If opentelemetry is installed *and* a tracer provider is configured,
        an ``acp.rpc`` span is emitted with low-risk method/provider/code attrs.
        Soft-noops (no overhead, no exception) when OTel is absent.
        """
        if not self.is_connected:
            raise ConnectionError("ACP not connected")

        self._check_authentication_rpc(method, params)

        params = self._normalize_rpc_params(method, params)
        params = self._apply_configured_session_params(method, params)

        process = self.process
        stdin = process.stdin if process is not None else None
        if stdin is None or stdin.is_closing():
            self._connected = False
            raise ConnectionError("ACP stdin closed")

        if timeout is None:
            timeout = self.config.timeout_connect

        self._msg_id += 1
        mid = self._msg_id
        lifecycle: _RPCLifecycle | None = None
        transition_succeeded = False
        request_write_started = False

        span_cm = self._otel_rpc_span(method, mid)
        with span_cm as span:
            msg = _encode_json_line(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "method": method,
                    "params": params,
                }
            )

            future = asyncio.get_running_loop().create_future()
            self._pending[mid] = future
            self._inflight[mid] = (method, time.monotonic())
            prompt_write_started = False
            try:
                lifecycle = self._register_rpc_lifecycle(mid, method, params)

                # Register pending before writing: a fast subprocess may answer
                # before drain() returns.
                prompt_write_started = method == "session/prompt"
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout
                request_write_started = True
                if method == "authenticate" and self._auth_method_id is not None:
                    self._explicit_auth_attempted = True
                    self._explicit_auth_succeeded = False
                stdin.write(msg)
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                await wait_for_owned(stdin.drain(), remaining)
                result = await self._await_rpc_future(future, deadline)
                self._apply_rpc_lifecycle(method, result, lifecycle)
                transition_succeeded = True
            except asyncio.CancelledError:
                if lifecycle is not None:
                    lifecycle.abandoned = True
                await self._notify_request_cancel(mid)
                if request_write_started:
                    await self._invalidate_uncertain_rpc_transport(method)
                raise
            except asyncio.TimeoutError as exc:
                if lifecycle is not None:
                    lifecycle.abandoned = True
                await self._notify_request_cancel(mid)
                if request_write_started:
                    await self._invalidate_uncertain_rpc_transport(method)
                if span is not None:
                    with suppress(Exception):
                        span.set_attribute("acp.timeout", True)
                if method == "authenticate" and self._auth_method_id is not None:
                    _raise_private_cause(ACPAuthenticationError(), exc)
                raise
            except RPCError as rpc_err:
                if span is not None:
                    with suppress(Exception):
                        span.set_attribute("acp.code", rpc_err.code if rpc_err.code is not None else 0)
                if method == "authenticate" and self._auth_method_id is not None:
                    self._pool_reusable = False
                    _raise_private_cause(ACPAuthenticationError(), rpc_err)
                raise
            except ACPResponseError as exc:
                if lifecycle is not None:
                    lifecycle.abandoned = True
                if request_write_started:
                    await self._invalidate_uncertain_rpc_transport(method)
                if method == "authenticate" and self._auth_method_id is not None:
                    _raise_private_cause(ACPAuthenticationError(), exc)
                raise
            except Exception as exc:
                if lifecycle is not None:
                    lifecycle.abandoned = True
                if request_write_started:
                    await self._invalidate_uncertain_rpc_transport(method)
                if prompt_write_started:
                    _raise_private_cause(ACPRequestOutcomeUnknownError(exc), exc)
                if method == "authenticate" and self._auth_method_id is not None:
                    _raise_private_cause(ACPAuthenticationError(), exc)
                raise
            finally:
                self._finish_pending_rpc(mid, lifecycle, transition_succeeded)
                _finalize_rpc_future(future)
                self._pending.pop(mid, None)
                self._inflight.pop(mid, None)
            if method == "initialize":
                await self._authenticate_explicitly()
            return result

    @staticmethod
    def _normalize_rpc_params(method: str, params: dict) -> dict:
        """Apply common ACP v1 request requirements once for every adapter."""
        if method == "initialize":
            normalized = copy.deepcopy(params)
            capabilities = normalized.setdefault("clientCapabilities", {})
            if not isinstance(capabilities, dict):
                return normalized
            session = capabilities.setdefault("session", {})
            if not isinstance(session, dict):
                return normalized
            config_options = session.setdefault("configOptions", {})
            if isinstance(config_options, dict):
                config_options.setdefault("boolean", {})
            return normalized
        if method in {"session/new", "session/load", "session/resume"} and "mcpServers" not in params:
            return {**params, "mcpServers": []}
        return params

    def _apply_configured_session_params(self, method: str, params: dict) -> dict:
        """Inject validated ACP session roots and MCP definitions centrally."""
        if method not in {"session/new", "session/load", "session/resume"}:
            return params
        if not self.config.additional_directories and not self.config.mcp_servers:
            return params

        normalized = copy.deepcopy(params)
        if self.config.additional_directories:
            if not self.supports_session_capability("additionalDirectories"):
                raise ACPUnsupportedCapabilityError("sessionCapabilities.additionalDirectories")
            normalized["additionalDirectories"] = copy.deepcopy(self.config.additional_directories)

        if self.config.mcp_servers:
            mcp_capabilities = self.agent_capabilities.get("mcpCapabilities")
            mcp_capabilities = mcp_capabilities if isinstance(mcp_capabilities, dict) else {}
            for server in self.config.mcp_servers:
                transport = server.get("type", "stdio")
                if transport in {"http", "sse"} and mcp_capabilities.get(transport) is not True:
                    raise ACPUnsupportedCapabilityError(f"mcpCapabilities.{transport}")
            normalized["mcpServers"] = copy.deepcopy(self.config.mcp_servers)

        return normalized

    def _otel_rpc_span(self, method: str, msg_id: int):
        """Return an OTel span CM for this RPC, or a no-op CM if unavailable.

        Lazy imports opentelemetry so soulacp has no hard dependency.
        """
        try:
            from opentelemetry import trace  # pyright: ignore[reportMissingImports]

            tracer = trace.get_tracer("soulacp.rpc")
            attrs = {"acp.method": method, "acp.msg_id": msg_id}
            attrs["acp.provider"] = telemetry_provider_name(getattr(self.config, "provider", None))
            return _SafeSpanCM(tracer.start_as_current_span(f"acp.rpc {method}", attributes=attrs))
        except Exception:
            return _NoopCM()

    async def _send_result(self, msg_id: int | str | None, result: Any) -> None:
        """Send a JSON-RPC success response back to the subprocess."""
        process = self.process
        stdin = process.stdin if process is not None else None
        if not self.is_connected or stdin is None:
            return
        resp = _encode_json_line({"jsonrpc": "2.0", "id": msg_id, "result": result})
        stdin.write(resp)
        await wait_for_owned(stdin.drain(), self.config.timeout_connect)

    async def _send_error(self, msg_id: int | str | None, code: int, message: str) -> None:
        """Send a JSON-RPC error response back to the subprocess."""
        process = self.process
        stdin = process.stdin if process is not None else None
        if not self.is_connected or stdin is None:
            return
        resp = _encode_json_line(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": code, "message": message},
            }
        )
        stdin.write(resp)
        await wait_for_owned(stdin.drain(), self.config.timeout_connect)

    # ------------------------------------------------------------------
    # Stdout reader + message dispatch
    # ------------------------------------------------------------------

    async def _read_stdout(self) -> None:
        """Read and dispatch JSON-RPC messages from stdout."""
        failure: Exception | None = None
        try:
            process = self.process
            if process is None or process.stdout is None:
                return
            # returncode can become visible before the pipe's buffered tail is
            # consumed, especially on Windows. EOF, not process state, is the
            # authoritative end of the JSON-RPC stream.
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    text = line.decode("utf-8").strip()
                except UnicodeDecodeError as exc:
                    _raise_private_cause(ACPProtocolError("ACP stdout frame is not valid UTF-8"), exc)
                if not text:
                    continue

                msg = self._parse_json(text)
                if msg is not None:
                    await self._dispatch(msg)
        except (ConnectionResetError, OSError) as e:
            # Windows ProactorEventLoop raises ConnectionResetError (WinError 995)
            # when the subprocess pipe is closed during I/O — this is expected
            # during normal disconnect and should not be logged as a warning.
            if self._connected:
                logger.debug("ACP stdout pipe closed: %s", exception_summary(e))
                failure = ConnectionError(f"ACP stdout pipe closed: {exception_summary(e, include_sensitive=False)}")
        except asyncio.CancelledError:
            raise
        except ACPProtocolError as exc:
            failure = exc
            logger.warning("ACP protocol stream failed: %s", exception_summary(exc))
        except Exception as e:
            failure = ACPProtocolError(f"ACP stdout reader failed: {exception_summary(e, include_sensitive=False)}")
            logger.warning("ACP read loop ended unexpectedly: %s", exception_summary(e))
        finally:
            if self._connected:
                process = self.process
                logger.warning(
                    "ACP subprocess disconnected (returncode=%s, pending_rpcs=%d, session_present=%s, stderr_lines=%d)",
                    process.returncode if process is not None else None,
                    len(self._pending),
                    bool(self.session_id),
                    len(self._stderr_buffer),
                )
            self._connected = False
            self._retire_rpc_lifecycles()
            pending_error = failure or ConnectionError("ACP disconnected")
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(pending_error)
            self._pending.clear()
            self._inflight.clear()
            if self._streaming and self._stream_queue:
                if self._stream_error is None:
                    self._stream_error = failure or ConnectionError("ACP disconnected during stream")
                self._stream_queue.put_nowait(None)
            if not self._disconnect_lock.locked():
                self._schedule_host_cleanup()
                self._schedule_process_cleanup()

    async def _read_stderr(self) -> None:
        """Read stderr for debug logging and ring-buffer retention."""
        noise = ("AttachConsole", "conpty", "node:internal", "TracingChannel")
        try:
            process = self.process
            if process is None or process.stderr is None:
                return
            while True:
                try:
                    line = await process.stderr.readline()
                except ValueError:
                    # StreamReader.readline raises after consuming an
                    # over-limit line. Keep draining so the child cannot
                    # deadlock on a full stderr pipe.
                    self._stderr_buffer.append("[stderr line exceeded transport limit]")
                    logger.debug("Discarded an over-limit ACP stderr line")
                    continue
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text or any(n in text for n in noise):
                    continue
                if len(text) > MAX_STDERR_LINE_CHARS:
                    text = text[:MAX_STDERR_LINE_CHARS] + " [... truncated ...]"
                # Retain for error attribution (RPCError.stderr_tail).
                self._stderr_buffer.append(text)
                if sensitive_logging_enabled():
                    logger.debug("ACP stderr: %s", text)
        except asyncio.CancelledError:
            raise
        except (ConnectionResetError, OSError):
            pass
        except Exception as exc:
            logger.debug(
                "ACP stderr reader stopped unexpectedly: %s",
                exception_summary(exc),
                exc_info=sensitive_logging_enabled(),
            )

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """Extract a JSON object from a line of text.

        Lines that look like JSON objects (start with ``{``) but fail to parse
        raise :class:`ACPProtocolError`. Lines that contain JSON embedded in
        other text remain a compatibility path for banners and progress output.
        """
        looks_like_frame = text.startswith(("{", "["))
        try:
            if text.startswith("{"):
                parsed = strict_json_loads(text)
                return ACPClientBase._validate_json_frame(parsed)
            if text.startswith("["):
                parsed = strict_json_loads(text)
                return ACPClientBase._validate_json_frame(parsed)
            if "{" in text:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end > start:
                    if sensitive_logging_enabled():
                        logger.debug("Extracting JSON from non-JSON line: %r", text[:80])
                    else:
                        logger.debug("Extracting JSON from a non-JSON line (%d chars)", len(text))
                    parsed = strict_json_loads(text[start : end + 1])
                    return ACPClientBase._validate_json_frame(parsed)
        except (json.JSONDecodeError, ValueError, RecursionError) as e:
            if looks_like_frame:
                _raise_private_cause(ACPProtocolError("Malformed JSON-RPC frame"), e)
            if sensitive_logging_enabled():
                logger.debug("Failed to parse JSON from line: %r", text[:80])
            else:
                logger.debug("Failed to parse a non-protocol stdout line (%d chars)", len(text))
        return None

    @staticmethod
    def _validate_json_frame(parsed: Any) -> dict:
        if not isinstance(parsed, dict):
            raise ACPProtocolError("JSON-RPC frame must be an object")
        try:
            _validate_json_values(parsed, "JSON-RPC")
        except ValueError as exc:
            _raise_private_cause(ACPProtocolError(str(exc)), exc)
        return parsed

    async def _dispatch(self, msg: dict) -> None:
        """Route an incoming JSON-RPC 2.0 message."""
        if msg.get("jsonrpc") != "2.0":
            raise ACPProtocolError("JSON-RPC frame must declare version 2.0")
        method = msg.get("method")
        if "method" in msg and (not isinstance(method, str) or not method):
            raise ACPProtocolError("JSON-RPC method must be a non-empty string")
        if "method" in msg and ("result" in msg or "error" in msg):
            raise ACPProtocolError("JSON-RPC request or notification must not contain result or error")

        # disconnect() clears this before cancelling readers.  Ignore any
        # already-buffered notification/request rather than executing it.
        if method and not self._connected:
            return

        # -- Notifications (no "id") or server→client requests (has "id" + "method") --

        if method == "$/cancel_request":
            if "id" in msg:
                raise ACPProtocolError("$/cancel_request must be a notification without an id")
            params = msg.get("params")
            if not isinstance(params, dict) or "requestId" not in params:
                raise ACPProtocolError("$/cancel_request requires requestId params")
            self._validate_meta(params, source="$/cancel_request params")
            request_id = params["requestId"]
            if not self._is_request_id(request_id):
                raise ACPProtocolError("$/cancel_request requestId must be null, an int64, or a string")
            task = self._request_tasks_by_id.get(request_id)
            if task is not None and request_id not in self._request_reply_started:
                task.cancel()
            return

        # Stream update notifications
        if method == "session/update":
            if "id" in msg:
                raise ACPProtocolError("session/update must be a notification without an id")
            params = msg.get("params")
            if not isinstance(params, dict):
                raise ACPProtocolError("session/update params must be an object")
            meta = params.get("_meta")
            if meta is not None and not isinstance(meta, dict):
                raise ACPProtocolError("session/update _meta must be an object or null")
            update_session_id = params.get("sessionId")
            if not isinstance(update_session_id, str) or not update_session_id:
                raise ACPProtocolError("session/update requires a non-empty sessionId")
            if update_session_id not in {self.session_id, self._session_transition_id}:
                raise ACPProtocolError("session/update belongs to a different session")
            self._handle_stream_update(msg)
            return

        # Requests from CLI subprocess (has "id" + "method")
        if method and "id" in msg:
            request_id = msg["id"]
            if not self._is_request_id(request_id):
                raise ACPProtocolError("JSON-RPC request id must be null, an int64, or a string")
            if request_id in self._request_tasks_by_id:
                raise ACPProtocolError("duplicate in-flight JSON-RPC request id")
            if method == "session/request_permission" or method in self.METHOD_MAP:
                params = msg.get("params")
                request_session_id = params.get("sessionId") if isinstance(params, dict) else None
                if not isinstance(request_session_id, str) or not request_session_id:
                    await self._send_error(request_id, -32602, f"{method} requires a non-empty sessionId")
                    return
                if request_session_id not in {self.session_id, self._session_transition_id}:
                    await self._send_error(request_id, -32602, f"{method} belongs to a different session")
                    return
            if (
                method == "terminal/wait_for_exit"
                and len(self._terminal_wait_request_ids) >= MAX_TERMINAL_WAIT_REQUESTS
            ):
                await self._send_error(request_id, 10004, "Too many concurrent terminal wait requests")
                return
            if len(self._request_tasks) >= MAX_HOST_REQUEST_TASKS:
                await self._send_error(request_id, 10004, "Too many concurrent ACP host requests")
                return
            if method == "session/request_permission":
                self._permission_request_turns[request_id] = self._permission_turn
            task = asyncio.create_task(self._run_request(msg))
            self._request_tasks.add(task)
            self._request_tasks_by_id[request_id] = task
            if method == "terminal/wait_for_exit":
                self._terminal_wait_request_ids.add(request_id)
            task.add_done_callback(lambda done, request_id=request_id: self._request_done(request_id, done))
            return

        # -- RPC responses (has "id", no "method") --
        if "id" in msg and not method:
            mid = msg["id"]
            if isinstance(mid, bool) or not isinstance(mid, int) or not -(2**63) <= mid < 2**63:
                raise ACPProtocolError("JSON-RPC response id must be an integer in the int64 range")
            if ("result" in msg) == ("error" in msg):
                raise ACPProtocolError("JSON-RPC response must contain exactly one of result or error")
            error = msg.get("error")
            if "error" in msg:
                if not isinstance(error, dict):
                    raise ACPProtocolError("JSON-RPC error response must contain an error object")
                code = error.get("code")
                if isinstance(code, bool) or not isinstance(code, int) or not -(2**31) <= code < 2**31:
                    raise ACPProtocolError("JSON-RPC error code must be an integer in the int32 range")
                message = error.get("message")
                if not isinstance(message, str):
                    raise ACPProtocolError("JSON-RPC error message must be a string")
            if mid in self._pending:
                fut = self._pending[mid]
                inflight_method, start_ts = self._inflight.get(mid, (None, None))
                lifecycle = self._rpc_lifecycles.get(mid)
                try:
                    if fut.done() or (lifecycle is not None and (lifecycle.abandoned or lifecycle.finished)):
                        return
                    if "error" in msg:
                        if not isinstance(error, dict):
                            raise ACPProtocolError("JSON-RPC error response must contain an error object")
                        code = error["code"]
                        message = error["message"]
                        elapsed_ms = (time.monotonic() - start_ts) * 1000 if start_ts else None
                        fut.set_exception(
                            RPCError(
                                code=code,
                                message=message,
                                data=error.get("data"),
                                method=inflight_method,
                                msg_id=mid,
                                elapsed_ms=elapsed_ms,
                                stderr_tail=list(self._stderr_buffer),
                                session_id=self.session_id,
                            )
                        )
                    else:
                        result = msg.get("result", {})
                        if lifecycle is not None and inflight_method == "session/prompt":
                            self._require_prompt_stop_reason(result)
                        # Some providers embed final text in the RPC response.
                        # Validate and append it before publishing success to the
                        # waiting coroutine so protocol failures cannot race a
                        # successful future result.
                        if (
                            inflight_method == "session/prompt"
                            and isinstance(result, dict)
                            and result.get("stopReason")
                        ):
                            result_text = result.get("text", "")
                            if result_text and not isinstance(result_text, str):
                                raise ACPProtocolError("JSON-RPC result text must be a string")
                            if isinstance(result_text, str) and result_text:
                                accumulated = "".join(self._chunks)
                                if accumulated and not result_text.startswith(accumulated):
                                    raise ACPProtocolError(
                                        "JSON-RPC result text disagrees with streamed assistant text",
                                        partial_output=accumulated,
                                    )
                                if len(result_text) > len(accumulated):
                                    tail = result_text[len(accumulated) :]
                                    self._append_chunk(tail)
                                    logger.debug("Recovered %d chars from RPC result", len(tail))
                        # Commit in receive order, before waking the waiter or
                        # consuming another buffered session/update. The local
                        # context survives pending-map removal for exactly-once
                        # application and cancellation/rollback ownership.
                        if lifecycle is not None and inflight_method is not None:
                            self._apply_rpc_lifecycle(inflight_method, result, lifecycle)
                        fut.set_result(result)
                except Exception as exc:
                    if not fut.done():
                        protocol_error = (
                            exc if isinstance(exc, ACPResponseError) else ACPProtocolError("Malformed RPC response")
                        )
                        fut.set_exception(protocol_error)
                finally:
                    if inflight_method == "session/prompt":
                        self._permission_turn.active = False
                    self._pending.pop(mid, None)
                    self._inflight.pop(mid, None)
            return

        if not method and "id" not in msg:
            raise ACPProtocolError("JSON-RPC frame must be a request, response, or notification")

    # ------------------------------------------------------------------
    # Stream update handling
    # ------------------------------------------------------------------

    @staticmethod
    def _is_request_id(value: Any) -> bool:
        return not isinstance(value, bool) and (
            value is None or isinstance(value, str) or (isinstance(value, int) and -(2**63) <= value < 2**63)
        )

    def _request_done(self, request_id: int | str | None, task: asyncio.Task[None]) -> None:
        self._request_tasks.discard(task)
        if self._request_tasks_by_id.get(request_id) is task:
            self._request_tasks_by_id.pop(request_id, None)
            self._permission_request_turns.pop(request_id, None)
        self._request_reply_started.discard(request_id)
        self._terminal_wait_request_ids.discard(request_id)
        if not task.cancelled():
            try:
                error = task.exception()
            except Exception:
                error = None
            if error is not None:
                logger.warning("ACP host request task failed: %s", exception_summary(error))
                self._schedule_process_cleanup()

    def set_update_callback(self, callback: Callable[[dict], None] | None) -> None:
        """Register a callback for raw ``session/update`` events.

        The callback receives the ``update`` dict for EVERY update
        (text chunks, tool_call, tool_call_update, plan, thought, ...)
        in addition to soulacp's built-in text-chunk handling. Pass
        ``None`` to clear.

        Use this when you need observability beyond plain text replies, such
        as execution tracking or custom telemetry.
        Callback errors are swallowed (logged at debug) so faulty
        consumers can't break streaming.
        """
        self._update_callback = callback

    def _handle_stream_update(self, msg: dict) -> None:
        """Process one ``session/update`` notification.

        Text chunks feed the query result. Stable session metadata, usage,
        commands, config options, and mode updates also maintain local state.
        Every update remains available to higher-level consumers through
        ``set_update_callback()``; tool calls, plans, and thoughts are not
        flattened into response text.
        """
        params = msg.get("params", {})
        if not isinstance(params, dict):
            raise ACPProtocolError("session/update params must be an object")
        update = params.get("update", {})
        if not isinstance(update, dict):
            raise ACPProtocolError("session/update update must be an object")

        utype = update.get("sessionUpdate")
        if not isinstance(utype, str) or not utype:
            raise ACPProtocolError("session/update requires a non-empty sessionUpdate type")

        validate_stable_session_update(update)

        # Every valid session update resets stream inactivity, including tool,
        # plan, and thought updates that do not contain text.
        if self._streaming and self._stream_queue and not self._activity_queued:
            self._stream_queue.put_nowait(_STREAM_ACTIVITY)
            self._activity_queued = True

        if utype == "agent_message_chunk":
            content = update.get("content", {})
            if isinstance(content, dict) and content.get("type") == "text":
                chunk = content.get("text", "")
                self._accept_session_text(chunk)

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
                self._accept_session_text(chunk)

        elif utype in ("agent_message_end", "session_end", "turn_end"):
            # Some translators (e.g. OpenClaw) embed the final text chunk
            # inside the end event itself.  Extract it before signalling
            # stream completion so the text is not silently dropped.
            end_content = update.get("content", {})
            if isinstance(end_content, dict) and end_content.get("type") == "text":
                tail = end_content.get("text", "")
                if tail:
                    self._accept_session_text(tail)
            elif isinstance(end_content, str) and end_content:
                self._accept_session_text(end_content)

            # These non-standard compatibility updates may delimit a message,
            # but ACP v1 completes a turn only with the correlated
            # session/prompt response carrying stopReason.

        elif utype == "config_option_update":
            if "configOptions" not in update:
                raise ACPProtocolError("config_option_update requires configOptions")
            self.session_config_options = self._validate_config_options(
                update["configOptions"],
                source="config_option_update",
            )

        elif utype == "current_mode_update":
            current = update.get("currentModeId")
            if not isinstance(current, str) or not current:
                raise ACPProtocolError("current_mode_update requires a non-empty currentModeId")
            if self.session_mode_state is not None:
                available = self.session_mode_state.get("availableModes", [])
                if current not in {mode.get("id") for mode in available if isinstance(mode, dict)}:
                    raise ACPProtocolError("current_mode_update selected a mode that was not advertised")
                self.session_mode_state["currentModeId"] = current
                self._mode_state_revision += 1

        elif utype == "session_info_update":
            self._capture_session_info_update(update)

        elif utype == "usage_update":
            self.session_usage = self._validate_usage_update(update)

        elif utype == "available_commands_update":
            self.available_commands = self._validate_available_commands_update(update)

        # Internal handling runs first so a consumer cannot mutate the shared
        # event object and alter stream semantics. Callback ordering still
        # matches receive ordering across events.
        if self._update_callback is not None:
            try:
                self._update_callback(update)
            except Exception as e:
                logger.debug("update callback raised: %s", exception_summary(e))

    def _accept_session_text(self, chunk: Any) -> None:
        """Collect prompt text while ignoring history replay during transitions."""
        if not isinstance(chunk, str):
            raise ACPProtocolError(
                "ACP text update must contain string text",
                partial_output="".join(self._chunks),
            )
        if self._session_transition_id is None:
            self._append_chunk(chunk)

    def _append_chunk(self, chunk: Any) -> None:
        if not isinstance(chunk, str):
            raise ACPProtocolError(
                "ACP text update must contain string text",
                partial_output="".join(self._chunks),
            )
        if not chunk:
            return
        if len(self._chunks) >= MAX_RESPONSE_CHUNKS:
            raise ACPProtocolError(
                f"ACP response exceeds the {MAX_RESPONSE_CHUNKS}-chunk client limit",
                partial_output="".join(self._chunks),
            )
        if self._chunk_chars + len(chunk) > MAX_RESPONSE_CHARS:
            raise ACPProtocolError(
                f"ACP response exceeds the {MAX_RESPONSE_CHARS}-character client limit",
                partial_output="".join(self._chunks),
            )
        self._chunks.append(chunk)
        self._chunk_chars += len(chunk)
        if self._streaming and self._stream_queue:
            self._stream_queue.put_nowait(chunk)

    # ------------------------------------------------------------------
    # Request handling (CLI subprocess → host)
    # ------------------------------------------------------------------

    async def _run_request(self, msg: dict) -> None:
        """Run one host request and acknowledge ACP cancellation after cleanup."""
        request_id = msg["id"]
        try:
            await self._handle_request(msg)
        except asyncio.CancelledError:
            if self._connected and request_id not in self._request_reply_started:
                self._request_reply_started.add(request_id)
                with suppress(asyncio.CancelledError, Exception):
                    await self._send_error(request_id, -32800, "Request cancelled")
            raise

    async def _send_host_result(self, request_id: int | str | None, result: Any) -> None:
        self._request_reply_started.add(request_id)
        await self._send_result(request_id, result)

    async def _send_host_error(self, request_id: int | str | None, code: int, message: str) -> None:
        self._request_reply_started.add(request_id)
        await self._send_error(request_id, code, message)

    async def _handle_request(self, msg: dict) -> None:
        """Handle JSON-RPC requests from the CLI subprocess.

        Routes file-system, terminal, and permission requests via
        :attr:`METHOD_MAP`.
        """
        if not self._connected:
            return
        method = msg.get("method", "")
        mid = msg["id"]
        params = msg.get("params", {})
        if not isinstance(method, str) or not method:
            await self._send_host_error(mid, -32600, "Invalid JSON-RPC request method")
            return
        if not isinstance(params, dict):
            await self._send_host_error(mid, -32602, "JSON-RPC params must be an object")
            return
        try:
            self._validate_meta(params, source=f"{method} params")
        except ACPProtocolError:
            await self._send_host_error(mid, -32602, "Invalid host request metadata")
            return

        # Auto-approve permission requests
        if method == "session/request_permission":
            options = params.get("options", [])
            if not isinstance(options, list) or not options:
                await self._send_host_error(mid, -32602, "Permission request has no options")
                return

            metadata_values = [("toolCall", params.get("toolCall"))]
            metadata_values.extend((f"option {index}", option) for index, option in enumerate(options))
            try:
                for source, value in metadata_values:
                    if isinstance(value, dict):
                        self._validate_meta(value, source=f"permission request {source}")
            except ACPProtocolError:
                await self._send_host_error(mid, -32602, "Invalid permission request metadata")
                return

            legacy_id_only = all(isinstance(option, dict) and "kind" not in option for option in options)
            option_ids: set[str] = set()
            for option in options:
                option_id = option.get("optionId") if isinstance(option, dict) else None
                if not isinstance(option_id, str) or not option_id or option_id in option_ids:
                    await self._send_host_error(mid, -32602, "Permission option IDs must be non-empty and unique")
                    return
                option_ids.add(option_id)
            if not legacy_id_only:
                try:
                    validate_permission_tool_call(params.get("toolCall"))
                    for option in options:
                        if (
                            not isinstance(option, dict)
                            or not isinstance(option.get("optionId"), str)
                            or not option["optionId"]
                            or not isinstance(option.get("name"), str)
                            or not option["name"]
                            or option.get("kind") not in {"allow_once", "allow_always", "reject_once", "reject_always"}
                        ):
                            raise ACPProtocolError("permission option is malformed")
                except ACPProtocolError:
                    await self._send_host_error(mid, -32602, "Invalid permission request parameters")
                    return

            turn = self._permission_request_turns.get(mid, self._permission_turn)
            if turn.cancelled or not turn.active:
                await self._send_host_result(mid, {"outcome": {"outcome": "cancelled"}})
                return

            preferred_kinds = (
                ("allow_once", "allow_always")
                if self.config.auto_approve_permissions
                else ("reject_once", "reject_always")
            )
            selected = next(
                (
                    option
                    for kind in preferred_kinds
                    for option in options
                    if isinstance(option, dict)
                    and option.get("kind") == kind
                    and isinstance(option.get("optionId"), str)
                    and option["optionId"]
                ),
                None,
            )
            if selected is None and self.config.auto_approve_permissions and legacy_id_only:
                # Compatibility for pre-v1 adapters that supplied only IDs.
                # Rejection mode cannot safely infer which legacy ID rejects.
                selected = next(
                    (
                        option
                        for option in options
                        if isinstance(option, dict) and isinstance(option.get("optionId"), str) and option["optionId"]
                    ),
                    None,
                )
            if selected is None:
                await self._send_host_error(mid, -32602, "Permission request has no compatible option")
                return

            await self._send_host_result(
                mid,
                {"outcome": {"outcome": "selected", "optionId": selected["optionId"]}},
            )
            return

        # Dispatch via METHOD_MAP
        entry = self.METHOD_MAP.get(method)
        if not entry:
            await self._send_host_error(mid, -32601, f"Method not supported: {method}")
            return

        service_type, action = entry
        try:
            if service_type == "fs":
                result = await getattr(self._fs_service, action)(**params)
            elif service_type == "terminal":
                if action != "create":
                    names = [name for name in ("terminalId", "terminal_id") if name in params]
                    if not names or any(not isinstance(params[name], str) for name in names):
                        raise ValueError("terminal handle must be a string")
                result = await getattr(self._terminal_service, action)(**params)
            else:
                await self._send_host_error(mid, -32601, f"Unknown service: {service_type}")
                return
        except (HostResourceLimitError, TextEncodingError, UnsupportedTextResourceError) as exc:
            if isinstance(exc, HostResourceLimitError):
                code, message = 10001, "Host file resource limit exceeded"
            elif isinstance(exc, TextEncodingError):
                code, message = 10002, "Unsupported text encoding"
            else:
                code, message = 10003, "Unsupported text resource"
            await self._send_host_error(mid, code, message)
        except (TypeError, ValueError) as exc:
            logger.debug(
                "ACP host request rejected (%s/%s): %s",
                service_type,
                action,
                exception_summary(exc),
            )
            await self._send_host_error(mid, -32602, "Invalid host request parameters")
        except Exception as exc:
            logger.warning(
                "ACP host request failed (%s/%s): %s",
                service_type,
                action,
                exception_summary(exc),
            )
            if (service_type == "fs" and action == "read_text_file" and isinstance(exc, FileNotFoundError)) or (
                service_type == "terminal" and isinstance(exc, TerminalNotFoundError)
            ):
                await self._send_host_error(mid, -32002, "Resource not found")
            elif (
                service_type == "fs"
                and action in {"read_text_file", "write_text_file"}
                and isinstance(exc, IsADirectoryError)
            ):
                await self._send_host_error(mid, 10003, "Unsupported text resource")
            else:
                await self._send_host_error(mid, -32603, "Host service request failed")
        else:
            await self._send_host_result(mid, result)
