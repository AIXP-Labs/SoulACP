"""Secure path resolution for ACP host services."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


def resolve_path(path: str, cwd: str, additional_roots: Sequence[str] = ()) -> Path:
    """Resolve *path* safely relative to *cwd*.

    Rules:
    - Native absolute paths are resolved as supplied.
    - Relative paths are resolved against *cwd* for legacy adapter
      compatibility, although ACP v1 filesystem requests use absolute paths.
    - The resolved path **must** stay within *cwd* or an explicitly supplied
      additional root.

    Raises:
        PermissionError: If the resolved path escapes the workspace.
    """
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ValueError("path must be a non-empty string without NUL bytes")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("path must contain valid Unicode") from exc

    cwd_resolved = Path(cwd).resolve()
    roots = (cwd_resolved, *(Path(root).resolve() for root in additional_roots))
    candidate = Path(path)
    if candidate.drive and not candidate.is_absolute():
        raise ValueError("drive-relative paths are not supported")
    resolved = candidate.resolve() if candidate.is_absolute() else (cwd_resolved / candidate).resolve()

    # Security: must stay within workspace (use is_relative_to, not string prefix)
    if not any(resolved.is_relative_to(root) for root in roots):
        raise PermissionError("Path escapes workspace")

    return resolved
