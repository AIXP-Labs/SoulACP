"""Privacy-safe formatting helpers for logs and public exception messages."""

from __future__ import annotations

import os
import re
from typing import Any

SENSITIVE_LOG_ENV = "ACP_LOG_SENSITIVE_DATA"
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_$./:-]{1,80}$")


def sensitive_logging_enabled() -> bool:
    """Return whether the process explicitly opted into sensitive diagnostics."""
    return os.environ.get(SENSITIVE_LOG_ENV) == "1"


def exception_summary(exc: BaseException, *, include_sensitive: bool | None = None) -> str:
    """Format an exception without exposing its message unless explicitly enabled."""
    if include_sensitive is None:
        include_sensitive = sensitive_logging_enabled()

    exception_name = type(exc).__name__
    try:
        formatter = getattr(exc, "format_diagnostic", None)
    except Exception:
        formatter = None
    if callable(formatter):
        try:
            return str(formatter(include_sensitive=include_sensitive))
        except Exception:
            # Diagnostic formatting must never mask the original failure.
            return exception_name

    if include_sensitive:
        try:
            return str(exc)
        except Exception:
            return exception_name

    parts = [exception_name]
    try:
        code: Any = getattr(exc, "code", None)
    except Exception:
        code = None
    if isinstance(code, int) and not isinstance(code, bool):
        parts.append(f"code={code}")
    try:
        method = getattr(exc, "method", None)
    except Exception:
        method = None
    if isinstance(method, str) and _SAFE_IDENTIFIER.fullmatch(method):
        parts.append(f"method={method}")
    return " | ".join(parts)


def public_exception_summary(exc: BaseException) -> str:
    """Return an always-redacted exception summary for public error strings."""
    return exception_summary(exc, include_sensitive=False)
