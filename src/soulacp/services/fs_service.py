"""File system service — responds to CLI subprocess file operation requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .path_utils import resolve_path


class FSService:
    """Host-side file system service for ACP clients.

    Provides read, write, list, stat, and exists operations within a
    sandboxed working directory.
    """

    MAX_READ_CHARS: int = 10_000
    DEFAULT_LINE_LIMIT: int = 2000

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd

    async def read_text_file(
        self,
        path: str,
        offset: int = 1,
        limit: int = 0,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Read a text file with optional line range.

        If *path* points to a directory, returns a listing instead.
        """
        resolved = resolve_path(path, self.cwd)

        if resolved.is_dir():
            files = [f.name for f in sorted(resolved.iterdir()) if not f.name.startswith(".")]
            return {"content": "\n".join(files)}

        text = resolved.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        # Line range slicing (1-based offset)
        start = max(0, offset - 1)
        end = start + (limit or self.DEFAULT_LINE_LIMIT)
        selected = lines[start:end]
        content = "\n".join(selected)

        # Truncate overly long content
        if len(content) > self.MAX_READ_CHARS:
            content = content[: self.MAX_READ_CHARS]
            content += f"\n[... truncated, file has {len(lines)} lines ...]"

        return {"content": content}

    async def write_text_file(
        self,
        path: str,
        content: str,
        **_extra: Any,
    ) -> dict[str, Any]:
        """Write content to a text file, creating parent directories."""
        resolved = resolve_path(path, self.cwd)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

        stat = resolved.stat()
        return {
            "exists": True,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "isFile": True,
            "isDirectory": False,
            "content": f"Successfully wrote {stat.st_size} bytes",
        }

    async def list_directory(
        self,
        path: str,
        **_extra: Any,
    ) -> dict[str, Any]:
        """List directory contents (excluding hidden files)."""
        resolved = resolve_path(path, self.cwd)
        if not resolved.is_dir():
            raise FileNotFoundError(f"Not a directory: {path}")
        files = [f.name for f in sorted(resolved.iterdir()) if not f.name.startswith(".")]
        return {"files": files, "content": "\n".join(files)}

    async def exists(self, path: str, **_extra: Any) -> dict[str, Any]:
        """Check whether a path exists."""
        resolved = resolve_path(path, self.cwd)
        return {"exists": resolved.exists()}

    async def stat(self, path: str, **_extra: Any) -> dict[str, Any]:
        """Return file or directory metadata."""
        resolved = resolve_path(path, self.cwd)

        if not resolved.exists():
            return {
                "exists": False,
                "isFile": False,
                "isDirectory": False,
                "type": "file",
                "path": path,
                "name": Path(path).name,
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
