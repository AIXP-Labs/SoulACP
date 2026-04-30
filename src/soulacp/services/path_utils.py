"""Secure path resolution for ACP host services."""

from __future__ import annotations

from pathlib import Path


def resolve_path(path: str, cwd: str) -> Path:
    """Resolve *path* safely relative to *cwd*.

    Rules:
    - Windows absolute paths (``C:\\...``, ``D:/...``) are used directly.
    - Unix-style "pseudo-absolute" paths (``/src/main.py``) are treated
      as relative to *cwd* (common in AI-generated output).
    - Relative paths are resolved against *cwd*.
    - The resolved path **must** stay within the *cwd* tree.

    Raises:
        PermissionError: If the resolved path escapes the workspace.
    """
    # Strip leading slashes for pseudo-absolute paths
    clean = path.lstrip("/\\")

    # Detect real Windows absolute paths (e.g. C:\..., D:/...)
    if len(path) >= 3 and path[0].isalpha() and path[1] == ":":
        resolved = Path(path).resolve()
    else:
        resolved = Path(cwd, clean).resolve()

    # Security: must stay within workspace (use is_relative_to, not string prefix)
    cwd_resolved = Path(cwd).resolve()
    if not resolved.is_relative_to(cwd_resolved):
        raise PermissionError(f"Path escapes workspace: {path}")

    return resolved
