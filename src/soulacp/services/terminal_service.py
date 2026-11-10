"""ACP terminal service with bounded output and explicit process ownership."""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from collections.abc import Coroutine, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from soulacp.binary import find_binary_in_environment
from soulacp.environment import build_subprocess_environment, update_subprocess_environment
from soulacp.privacy import _raise_private_cause, exception_summary

DEFAULT_OUTPUT_BYTE_LIMIT = 1_048_576
MAX_OUTPUT_BYTE_LIMIT = 10 * 1_048_576
MAX_TERMINALS = 32
MAX_COMMAND_BYTES = 32 * 1024
MAX_ARGUMENTS = 1024
MAX_ARGV_BYTES = 1_048_576
MAX_ENV_ENTRIES = 1024
MAX_ENV_BYTES = 1_048_576
CAPTURE_JOIN_TIMEOUT = 1.0

logger: logging.Logger = logging.getLogger(__name__)


class TerminalNotFoundError(KeyError):
    """A terminal handle is absent from this service, not an internal lookup bug."""


@dataclass(slots=True)
class _Terminal:
    process: asyncio.subprocess.Process
    output_byte_limit: int
    output: bytearray = field(default_factory=bytearray)
    truncated: bool = False
    exit_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    terminate_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TerminalService:
    """Execute and manage ACP terminal subprocesses inside one workspace."""

    def __init__(
        self,
        cwd: str,
        additional_directories: Sequence[str] = (),
        *,
        inherit_parent_env: bool = True,
    ) -> None:
        self.cwd: str = str(Path(cwd).resolve())
        self.additional_directories: tuple[str, ...] = tuple(
            str(Path(path).resolve()) for path in additional_directories
        )
        self.inherit_parent_env = inherit_parent_env
        self._terminals: dict[str, _Terminal] = {}
        self._closed = False
        self._owned_tasks: set[asyncio.Task[Any]] = set()
        self._close_task: asyncio.Task[None] | None = None
        self._create_lock = asyncio.Lock()
        self._reserved_terminal_slots = 0

    async def create(
        self,
        command: str,
        args: list[str] | None = None,
        env: list[dict[str, str]] | None = None,
        cwd: str | None = None,
        outputByteLimit: int | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Start a command and return its terminal ID without waiting."""
        if not isinstance(command, str) or not command or "\x00" in command:
            raise ValueError("terminal command must be a non-empty string without NUL bytes")
        command_bytes = self._utf8_size(command, "terminal command")
        if command_bytes > MAX_COMMAND_BYTES:
            raise ValueError(f"terminal command exceeds the {MAX_COMMAND_BYTES}-byte limit")

        argv = [] if args is None else args
        if not isinstance(argv, list):
            raise ValueError("terminal args must be a list of strings without NUL bytes")
        if len(argv) > MAX_ARGUMENTS:
            raise ValueError(f"terminal args exceed the {MAX_ARGUMENTS}-argument limit")
        if not all(isinstance(arg, str) and "\x00" not in arg for arg in argv):
            raise ValueError("terminal args must be a list of strings without NUL bytes")
        argv_bytes = command_bytes + 1 + sum(self._utf8_size(arg, "terminal argument") + 1 for arg in argv)
        if argv_bytes > MAX_ARGV_BYTES:
            raise ValueError(f"terminal command and args exceed the {MAX_ARGV_BYTES}-byte limit")
        output_byte_limit = DEFAULT_OUTPUT_BYTE_LIMIT if outputByteLimit is None else outputByteLimit
        if isinstance(output_byte_limit, bool) or not isinstance(output_byte_limit, int):
            raise ValueError("outputByteLimit must be an integer")
        if not 0 <= output_byte_limit <= MAX_OUTPUT_BYTE_LIMIT:
            raise ValueError(f"outputByteLimit must be between 0 and {MAX_OUTPUT_BYTE_LIMIT}")

        async with self._create_lock:
            if self._closed:
                raise RuntimeError("terminal service is closed")
            if len(self._terminals) + self._reserved_terminal_slots >= MAX_TERMINALS:
                raise RuntimeError(f"terminal service limit of {MAX_TERMINALS} unreleased terminals reached")

            process_cwd = self._resolve_cwd(cwd)
            process_env = self._build_env(env)
            executable = self._resolve_command(command, process_env)
            process: asyncio.subprocess.Process | None = None
            terminal: _Terminal | None = None
            terminal_id: str | None = None
            try:
                spawn_task = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        executable,
                        *argv,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        cwd=process_cwd,
                        env=process_env,
                        limit=min(MAX_OUTPUT_BYTE_LIMIT, max(64 * 1024, output_byte_limit)),
                    )
                )
                try:
                    process = await asyncio.shield(spawn_task)
                except BaseException:
                    # A cancelled create may still publish a real child later.
                    # Keep its capacity slot reserved until cleanup either sees
                    # spawn failure or converts the reservation into a record.
                    self._reserved_terminal_slots += 1
                    self._start_owned_task(self._claim_and_reap_late_spawn(spawn_task, output_byte_limit))
                    raise
                if process is None:
                    raise RuntimeError("terminal spawn returned without a process")
                terminal = _Terminal(process=process, output_byte_limit=output_byte_limit)
                terminal_id = str(uuid.uuid4())
                self._terminals[terminal_id] = terminal
                terminal.task = asyncio.create_task(self._capture_output(terminal))
                if self._closed:
                    raise RuntimeError("terminal service closed while creating a process")
                return {"terminalId": terminal_id}
            except BaseException:
                if process is not None and terminal is None:
                    terminal = _Terminal(process=process, output_byte_limit=output_byte_limit)
                if terminal is not None and terminal_id is None:
                    terminal_id = str(uuid.uuid4())
                    self._terminals[terminal_id] = terminal
                    terminal.task = asyncio.create_task(self._capture_output(terminal))
                if terminal is not None:

                    async def _terminate_and_forget() -> None:
                        await self._terminate(terminal)
                        if terminal_id is not None and self._terminals.get(terminal_id) is terminal:
                            self._terminals.pop(terminal_id, None)

                    cleanup = self._start_owned_task(_terminate_and_forget())
                    await self._await_owned_cleanup(cleanup)
                raise

    async def _claim_and_reap_late_spawn(
        self,
        spawn_task: asyncio.Task[asyncio.subprocess.Process],
        output_byte_limit: int,
    ) -> None:
        """Own and retire a process that appears after create was interrupted."""
        try:
            process = await asyncio.shield(spawn_task)
        except BaseException:
            async with self._create_lock:
                self._reserved_terminal_slots -= 1
            return

        terminal = _Terminal(process=process, output_byte_limit=output_byte_limit)
        terminal_id = str(uuid.uuid4())
        async with self._create_lock:
            self._reserved_terminal_slots -= 1
            self._terminals[terminal_id] = terminal
            terminal.task = asyncio.create_task(self._capture_output(terminal))
        await self._terminate(terminal)
        async with self._create_lock:
            if self._terminals.get(terminal_id) is terminal:
                self._terminals.pop(terminal_id, None)

    async def wait_for_exit(
        self,
        terminalId: str | None = None,
        terminal_id: str | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Wait for a terminal and return the ACP exit status shape."""
        terminal = self._get_terminal(terminalId or terminal_id)
        if terminal.process.returncode is None:
            await terminal.process.wait()
        if terminal.task is not None and not terminal.task.done():
            # A descendant can inherit stdout after the direct child exits.
            # Give buffered output a bounded drain window without changing
            # wait_for_exit into a wait for every descendant process.
            await asyncio.wait({terminal.task}, timeout=CAPTURE_JOIN_TIMEOUT)
        terminal.exit_event.set()
        return self._exit_status(terminal.process.returncode)

    async def get_output(
        self,
        terminalId: str | None = None,
        terminal_id: str | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Return valid UTF-8 output accumulated so far."""
        terminal = self._get_terminal(terminalId or terminal_id)
        result: dict[str, Any] = {
            "output": bytes(terminal.output).decode("utf-8", errors="ignore"),
            "truncated": terminal.truncated,
        }
        if terminal.truncated:
            result["_meta"] = {"soulacp": {"truncated": True}}
        if terminal.process.returncode is not None:
            result["exitStatus"] = self._exit_status(terminal.process.returncode)
        return result

    async def kill(
        self,
        terminalId: str | None = None,
        terminal_id: str | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Terminate a command while retaining its terminal record."""
        terminal = self._get_terminal(terminalId or terminal_id)
        cleanup = self._start_owned_task(self._terminate(terminal))
        await self._await_owned_cleanup(cleanup)
        return {}

    async def release(
        self,
        terminalId: str | None = None,
        terminal_id: str | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Terminate a running command and invalidate its terminal ID."""
        identifier = terminalId or terminal_id
        terminal = self._get_terminal(identifier)

        async def _terminate_and_release() -> None:
            await self._terminate(terminal)
            if identifier is not None and self._terminals.get(identifier) is terminal:
                self._terminals.pop(identifier, None)

        cleanup = self._start_owned_task(_terminate_and_release())
        await self._await_owned_cleanup(cleanup)
        return {}

    async def close(self) -> None:
        """Terminate every terminal before propagating caller cancellation."""
        self._closed = True
        if self._close_task is None or (self._close_task.done() and self._terminals):
            self._close_task = self._start_owned_task(self._close_all())
        await self._await_owned_cleanup(self._close_task)

    @staticmethod
    async def _await_owned_cleanup(task: asyncio.Task[Any]) -> None:
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
                    "Terminal cleanup failed while its caller was cancelled: %s",
                    exception_summary(cleanup_error),
                )
            raise asyncio.CancelledError
        if cleanup_error is not None:
            raise cleanup_error

    async def _close_all(self) -> None:
        async with self._create_lock:
            terminals = list(self._terminals.items())
            failures: list[BaseException] = []
            if terminals:
                results = await asyncio.gather(
                    *(self._terminate(terminal) for _terminal_id, terminal in terminals),
                    return_exceptions=True,
                )
                for (terminal_id, terminal), result in zip(terminals, results, strict=True):
                    if isinstance(result, BaseException):
                        failures.append(result)
                    elif self._terminals.get(terminal_id) is terminal:
                        self._terminals.pop(terminal_id, None)
        current = asyncio.current_task()
        owned = [task for task in self._owned_tasks if task is not current]
        if owned:
            results = await asyncio.gather(*owned, return_exceptions=True)
            failures.extend(
                result
                for result in results
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)
            )
        if failures:
            _raise_private_cause(RuntimeError(f"failed to terminate {len(failures)} terminal process(es)"), failures[0])

    async def _capture_output(self, terminal: _Terminal) -> None:
        cancelled = False
        try:
            stdout = terminal.process.stdout
            if stdout is None:
                raise RuntimeError("terminal subprocess stdout pipe is unavailable")
            while True:
                chunk = await stdout.read(64 * 1024)
                if not chunk:
                    break
                terminal.output.extend(chunk)
                self._trim_output(terminal)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            logger.debug("Terminal output capture failed: %s", exception_summary(exc))
        finally:
            if not cancelled and terminal.process.returncode is None:
                try:
                    await terminal.process.wait()
                except Exception as exc:
                    logger.warning(
                        "Terminal process wait failed; exit remains unknown: %s",
                        exception_summary(exc),
                    )
            if terminal.process.returncode is not None:
                terminal.exit_event.set()

    @staticmethod
    def _trim_output(terminal: _Terminal) -> None:
        excess = len(terminal.output) - terminal.output_byte_limit
        if excess <= 0:
            return
        del terminal.output[:excess]
        while terminal.output and terminal.output[0] & 0xC0 == 0x80:
            del terminal.output[0]
        terminal.truncated = True

    async def _terminate(self, terminal: _Terminal) -> None:
        async with terminal.terminate_lock:
            await self._terminate_locked(terminal)

    async def _terminate_locked(self, terminal: _Terminal) -> None:
        process = terminal.process
        termination_error: BaseException | None = None
        if process.returncode is None:
            try:
                process.terminate()
            except (ProcessLookupError, PermissionError, OSError) as exc:
                termination_error = exc
            else:
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except (asyncio.TimeoutError, ProcessLookupError, PermissionError, OSError) as exc:
                    termination_error = exc
            if process.returncode is None:
                try:
                    process.kill()
                except (ProcessLookupError, PermissionError, OSError) as exc:
                    termination_error = exc
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except (asyncio.TimeoutError, ProcessLookupError, PermissionError, OSError) as exc:
                    termination_error = exc
        if process.returncode is None:
            error = RuntimeError("terminal process did not exit after terminate/kill")
            if termination_error is not None:
                _raise_private_cause(error, termination_error)
            raise error from None
        if terminal.task is not None and terminal.task is not asyncio.current_task():
            try:
                done, _pending = await asyncio.wait({terminal.task}, timeout=CAPTURE_JOIN_TIMEOUT)
            except asyncio.CancelledError:
                terminal.task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await terminal.task
                terminal.exit_event.set()
                raise
            if terminal.task not in done:
                terminal.task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await terminal.task
            elif not terminal.task.cancelled():
                capture_error = terminal.task.exception()
                if capture_error is not None:
                    logger.debug(
                        "Terminal output task failed during cleanup: %s",
                        exception_summary(capture_error),
                    )
        transport = getattr(process, "_transport", None)
        if transport:
            with suppress(Exception):
                transport.close()
        terminal.exit_event.set()

    def _start_owned_task(self, awaitable: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(awaitable)
        self._owned_tasks.add(task)

        def _done(done: asyncio.Task[Any]) -> None:
            self._owned_tasks.discard(done)
            if not done.cancelled():
                with suppress(Exception):
                    done.exception()

        task.add_done_callback(_done)
        return task

    def _get_terminal(self, terminal_id: str | None) -> _Terminal:
        if not terminal_id or terminal_id not in self._terminals:
            raise TerminalNotFoundError("Terminal not found")
        return self._terminals[terminal_id]

    def _resolve_cwd(self, requested: str | None) -> str:
        root = Path(self.cwd).resolve()
        roots = (root, *(Path(path).resolve() for path in self.additional_directories))
        if requested is None:
            return str(root)
        if not isinstance(requested, str) or not requested or "\x00" in requested:
            raise ValueError("terminal cwd must be a non-empty string without NUL bytes")
        self._utf8_size(requested, "terminal cwd")
        candidate = Path(requested)
        if not candidate.is_absolute():
            raise ValueError("terminal cwd must be absolute")
        resolved = candidate.resolve()
        if not any(resolved.is_relative_to(allowed_root) for allowed_root in roots):
            raise PermissionError("terminal cwd escapes workspace")
        return str(resolved)

    @staticmethod
    def _resolve_command(command: str, environment: dict[str, str]) -> str:
        if PurePosixPath(command).name != command or PureWindowsPath(command).name != command:
            return command
        resolved = find_binary_in_environment([command], environment)
        if resolved is None:
            raise FileNotFoundError("terminal command was not found on an absolute PATH entry")
        return resolved

    @staticmethod
    def _utf8_size(value: str, label: str) -> int:
        try:
            return len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError(f"{label} must contain valid Unicode") from exc

    def _build_env(self, entries: list[dict[str, str]] | None) -> dict[str, str]:
        environment = build_subprocess_environment(self.inherit_parent_env)
        if entries is None:
            return environment
        if not isinstance(entries, list):
            raise ValueError("terminal env must be a list")
        if len(entries) > MAX_ENV_ENTRIES:
            raise ValueError(f"terminal env exceeds the {MAX_ENV_ENTRIES}-entry limit")
        total_bytes = 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("terminal env entries must be objects")
            name = entry.get("name")
            value = entry.get("value")
            if not isinstance(name, str) or not name or "=" in name or "\x00" in name:
                raise ValueError("terminal environment variable name is invalid")
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError("terminal environment value is invalid")
            total_bytes += self._utf8_size(name, "terminal environment name")
            total_bytes += self._utf8_size(value, f"terminal environment value for {name!r}") + 2
            if total_bytes > MAX_ENV_BYTES:
                raise ValueError(f"terminal env exceeds the {MAX_ENV_BYTES}-byte limit")
            update_subprocess_environment(environment, {name: value})
        return environment

    @staticmethod
    def _exit_status(returncode: int | None) -> dict[str, Any]:
        if returncode is None:
            return {"exitCode": None, "signal": None}
        if returncode >= 0:
            return {"exitCode": returncode, "signal": None}
        try:
            signal_name = signal.Signals(-returncode).name
        except ValueError:
            signal_name = str(-returncode)
        return {"exitCode": None, "signal": signal_name}
