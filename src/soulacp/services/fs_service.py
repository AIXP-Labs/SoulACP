"""File system service — responds to CLI subprocess file operation requests."""

from __future__ import annotations

import asyncio
import io
import os
import stat
from collections.abc import Callable, Sequence
from pathlib import Path, PureWindowsPath
from typing import Any, BinaryIO, TypeVar

from soulacp.privacy import _raise_private_cause

from .path_utils import resolve_path

_T = TypeVar("_T")


class HostResourceLimitError(ValueError):
    """A valid operation exceeds a bounded host resource budget."""


class TextEncodingError(ValueError):
    """File bytes cannot be decoded as UTF-8; raw diagnostics stay private."""


class UnsupportedTextResourceError(ValueError):
    """A text request names a nonregular resource."""


def _reject_windows_device(path: str) -> None:
    if os.name != "nt" or not isinstance(path, str):
        return
    for part in PureWindowsPath(path).parts:
        name = part.split(":", 1)[0].split(".", 1)[0].rstrip(" ").upper()
        if name in {"CON", "PRN", "AUX", "NUL"} or (
            len(name) == 4 and name[:3] in {"COM", "LPT"} and name[3] in "123456789\u00b9\u00b2\u00b3"
        ):
            raise UnsupportedTextResourceError("Requested path is not a regular text file")


def _open_regular(resolved: Path, *, writing: bool) -> int:
    try:
        before = resolved.lstat()
    except FileNotFoundError:
        if not writing:
            raise
        before = None
    if before is not None:
        if stat.S_ISDIR(before.st_mode):
            raise IsADirectoryError("Requested path is not a text file")
        if not stat.S_ISREG(before.st_mode):
            raise UnsupportedTextResourceError("Requested path is not a regular text file")
    flags = os.O_WRONLY if writing else os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NOCTTY", 0)
    if before is None:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(resolved, flags, 0o666)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise UnsupportedTextResourceError("Requested path is not a regular text file")
        if before is not None and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("Text file changed during safe open")
    except BaseException:
        os.close(fd)
        raise
    return fd


class _BoundedRawReader(io.RawIOBase):
    """Expose a readable raw stream that fails after a fixed byte budget."""

    def __init__(self, raw: BinaryIO, limit: int) -> None:
        super().__init__()
        self._raw = raw
        self._limit = limit
        self._consumed = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if not buffer:
            return 0
        remaining = self._limit - self._consumed
        chunk = self._raw.read(min(len(buffer), remaining + 1))
        if not chunk:
            return 0
        if len(chunk) > remaining:
            raise HostResourceLimitError(f"file exceeds the {self._limit}-byte read limit")
        buffer[: len(chunk)] = chunk
        self._consumed += len(chunk)
        return len(chunk)


async def _await_owned_thread(func: Callable[..., _T], *args: Any) -> _T:
    """Let an irreversible thread operation finish before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(func, *args))
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
            if task.done():
                try:
                    task.result()
                except BaseException as worker_error:
                    _raise_private_cause(cancellation, worker_error)
                raise cancellation from None
            continue
        except BaseException as exc:
            if cancellation is not None:
                _raise_private_cause(cancellation, exc)
            raise
        else:
            if cancellation is not None:
                raise cancellation
            return result


class FSService:
    """Host-side file system service for ACP clients.

    Provides read, write, list, stat, and exists operations within a
    workspace-confined working directory. This is path containment, not an OS
    sandbox for the agent process itself.
    """

    MAX_READ_CHARS: int = 10_000
    MAX_FILE_BYTES: int = 10 * 1_048_576
    MAX_WRITE_BYTES: int = 10 * 1_048_576
    MAX_LINE_LIMIT: int = 10_000
    MAX_DIRECTORY_ENTRIES: int = 2_000
    MAX_DIRECTORY_SCAN_ENTRIES: int = 10_000
    DEFAULT_LINE_LIMIT: int = 2000
    MAX_UINT32: int = 2**32 - 1

    def __init__(self, cwd: str, additional_directories: Sequence[str] = ()) -> None:
        self.cwd: str = cwd
        self.additional_directories: tuple[str, ...] = tuple(additional_directories)

    async def read_text_file(
        self,
        path: str,
        offset: int | None = None,
        limit: int | None = None,
        line: int | None = None,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Read a bounded text-file slice using ACP's 1-based ``line``."""
        start_line = line if line is not None else (offset if offset is not None else 1)
        if (
            isinstance(start_line, bool)
            or not isinstance(start_line, int)
            or start_line < 0
            or start_line > self.MAX_UINT32
        ):
            raise ValueError("line must be a uint32 integer")
        # ACP describes line as 1-based but permits the schema boundary 0.
        start_line = max(1, start_line)
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
            raise ValueError("limit must be an integer >= 0")
        line_limit = limit
        if line_limit is not None and line_limit > self.MAX_LINE_LIMIT:
            raise ValueError(f"limit must be <= {self.MAX_LINE_LIMIT}")

        return await _await_owned_thread(
            self._read_path,
            path,
            self.cwd,
            self.additional_directories,
            start_line,
            line_limit,
            self.MAX_READ_CHARS,
            self.MAX_FILE_BYTES,
        )

    def _read_path(
        self,
        path: str,
        cwd: str,
        roots: tuple[str, ...],
        start_line: int,
        line_limit: int | None,
        char_budget: int,
        byte_budget: int,
    ) -> dict[str, Any]:
        _reject_windows_device(path)
        resolved = resolve_path(path, cwd, roots)
        return self._read_resolved(resolved, start_line, line_limit, char_budget, byte_budget)

    def _read_resolved(
        self,
        resolved: Path,
        start_line: int,
        line_limit: int | None,
        char_budget: int | None = None,
        byte_budget: int | None = None,
    ) -> dict[str, Any]:
        char_budget = self.MAX_READ_CHARS if char_budget is None else char_budget
        byte_budget = self.MAX_FILE_BYTES if byte_budget is None else byte_budget
        fd = _open_regular(resolved, writing=False)
        try:
            raw_handle = os.fdopen(fd, "rb", buffering=0)
        except BaseException:
            os.close(fd)
            raise
        with raw_handle:
            opened_metadata = os.fstat(raw_handle.fileno())
            if opened_metadata.st_size > byte_budget:
                raise HostResourceLimitError(f"file exceeds the {byte_budget}-byte read limit")
            if line_limit == 0:
                return {"content": ""}

            selected: list[str] = []
            selected_chars = 0
            bounded_raw = _BoundedRawReader(raw_handle, byte_budget)
            try:
                with io.TextIOWrapper(
                    io.BufferedReader(bounded_raw), encoding="utf-8", errors="strict", newline=""
                ) as handle:
                    iterator = iter(handle)
                    line_number = 0
                    while line_limit is None or len(selected) < line_limit:
                        try:
                            text = next(iterator)
                        except StopIteration:
                            break
                        line_number += 1
                        if line_number < start_line:
                            continue
                        if selected_chars + len(text) > char_budget:
                            raise HostResourceLimitError(f"content exceeds the {char_budget}-character read limit")
                        selected.append(text)
                        selected_chars += len(text)
            except UnicodeDecodeError as exc:
                _raise_private_cause(TextEncodingError("Unsupported text encoding"), exc)

        content = "".join(selected)
        return {"content": content}

    async def write_text_file(
        self,
        path: str,
        content: str,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Write content and return the empty ACP v1 success result."""
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if len(content) > self.MAX_WRITE_BYTES:
            raise HostResourceLimitError(f"content exceeds the {self.MAX_WRITE_BYTES}-byte write limit")
        try:
            encoded = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            _raise_private_cause(ValueError("content must contain valid Unicode"), exc)
        if len(encoded) > self.MAX_WRITE_BYTES:
            raise HostResourceLimitError(f"content exceeds the {self.MAX_WRITE_BYTES}-byte write limit")
        await _await_owned_thread(self._write_path, path, self.cwd, self.additional_directories, content, encoded)
        return {}

    def _write_path(self, path: str, cwd: str, roots: tuple[str, ...], content: str, encoded: bytes) -> None:
        _reject_windows_device(path)
        resolved = resolve_path(path, cwd, roots)
        self._write_resolved(resolved, content, encoded)

    @staticmethod
    def _write_resolved(resolved: Path, content: str, encoded: bytes | None = None) -> None:
        data = content.encode("utf-8") if encoded is None else encoded
        fd = _open_regular(resolved, writing=True)
        try:
            os.ftruncate(fd, 0)
            remaining = memoryview(data)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("Text write made no progress")
                remaining = remaining[written:]
        finally:
            os.close(fd)

    async def list_directory(
        self,
        path: str,
        **_extra: Any,
    ) -> dict[str, Any]:
        """List directory contents (excluding hidden files)."""
        resolved = resolve_path(path, self.cwd, self.additional_directories)
        files = await asyncio.to_thread(self._list_resolved, resolved)
        return {"files": files, "content": "\n".join(files)}

    def _list_resolved(self, resolved: Path) -> list[str]:
        if not resolved.is_dir():
            raise FileNotFoundError("Requested path is not a directory")
        files: list[str] = []
        scanned = 0
        with os.scandir(resolved) as entries:
            for entry in entries:
                scanned += 1
                if scanned > self.MAX_DIRECTORY_SCAN_ENTRIES:
                    raise ValueError(f"directory exceeds the {self.MAX_DIRECTORY_SCAN_ENTRIES}-entry scan limit")
                if entry.name.startswith("."):
                    continue
                files.append(entry.name)
                if len(files) > self.MAX_DIRECTORY_ENTRIES:
                    raise ValueError(f"directory exceeds the {self.MAX_DIRECTORY_ENTRIES}-entry listing limit")
        return sorted(files)

    async def exists(self, path: str, **_extra: Any) -> dict[str, Any]:
        """Check whether a path exists."""
        resolved = resolve_path(path, self.cwd, self.additional_directories)
        return {"exists": await asyncio.to_thread(resolved.exists)}

    async def stat(self, path: str, **_extra: Any) -> dict[str, Any]:
        """Return file or directory metadata."""
        resolved = resolve_path(path, self.cwd, self.additional_directories)
        return await asyncio.to_thread(self._stat_resolved, resolved, path)

    @staticmethod
    def _stat_resolved(resolved: Path, original_path: str) -> dict[str, Any]:
        if not resolved.exists():
            return {
                "exists": False,
                "isFile": False,
                "isDirectory": False,
                "type": "file",
                "path": original_path,
                "name": Path(original_path).name,
                "size": 0,
                "mtime": 0,
            }

        s = resolved.stat()
        return {
            "exists": True,
            "size": s.st_size,
            "mtime": s.st_mtime,
            "ctime": s.st_ctime,
            "atime": s.st_atime,
            "isFile": resolved.is_file(),
            "isDirectory": resolved.is_dir(),
            "type": "directory" if resolved.is_dir() else "file",
            "path": str(resolved),
            "name": resolved.name,
            "permissions": oct(s.st_mode)[-3:],
        }
