"""Structured public exceptions for pool and response lifecycle failures."""

from __future__ import annotations

from typing import Any, ClassVar

from soulacp.privacy import _bind_private_cause, public_exception_summary


class ACPError(Exception):
    """Base class for soulacp-owned operational errors."""


class ACPAuthenticationError(ACPError):
    """Explicit authentication failed; retry or fallback requires caller action.

    Original errors, when present, are retained in the private ``cause`` field.
    Method IDs, provider messages and credential material are not public strings.
    """

    cause: BaseException | None = None

    def __init__(self) -> None:
        super().__init__("Explicit ACP authentication failed; automatic retry and fallback are disabled")


class ACPPoolError(ACPError):
    """Base class for connection-pool failures."""


class ACPPoolClosedError(ACPPoolError):
    """Raised when acquiring from a pool that is closing or closed."""


class ACPPoolAcquireTimeoutError(ACPPoolError):
    """Raised when no connection capacity becomes available in time."""

    timeout: float
    stats: dict[str, Any]

    def __init__(self, timeout: float, stats: dict[str, Any] | None = None) -> None:
        self.timeout = timeout
        self.stats = stats or {}
        super().__init__(f"pool capacity wait exceeded {timeout:g}s")


class ACPResumeError(ACPError):
    """Raised when a requested logical session cannot be resumed safely."""

    session_id: str

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__("failed to resume requested ACP session")


class ACPProviderError(ACPError):
    """A provider failure whose raw diagnostic text is retained out of band."""

    provider: str
    details: str | None
    returncode: int | None

    def __init__(
        self,
        provider: str,
        *,
        details: str | None = None,
        returncode: int | None = None,
    ) -> None:
        self.provider = provider
        self.details = details
        self.returncode = returncode
        super().__init__(self.format_diagnostic(include_sensitive=False))

    def format_diagnostic(self, *, include_sensitive: bool = False) -> str:
        """Render provider details, redacting raw diagnostics by default."""
        parts = [f"{self.provider} provider failed"]
        if self.returncode is not None:
            parts.append(f"returncode={self.returncode}")
        if self.details:
            parts.append(f"details={self.details!r}" if include_sensitive else "details=<redacted>")
        return " | ".join(parts)


class ACPUnsupportedCapabilityError(ACPError):
    """Raised before calling an ACP method the agent did not advertise."""

    capability: str

    def __init__(self, capability: str, *, public_capability: str | None = None) -> None:
        self.capability = capability
        label = capability if public_capability is None else public_capability
        super().__init__(f"ACP agent does not advertise capability {label!r}")


class ACPResponseError(ACPError):
    """Base class for failed responses that may contain partial text."""

    partial_output: str
    primary_cause: BaseException | None = None

    def __init__(self, message: str, *, partial_output: str = "") -> None:
        self.partial_output = partial_output
        super().__init__(message)


class ACPPartialResponseError(ACPResponseError):
    """Wrap an underlying response error without disguising partial output as success."""

    cause: BaseException
    __cause__: BaseException | None

    def __init__(self, cause: BaseException, *, partial_output: str = "") -> None:
        self.cause = cause
        super().__init__(
            f"ACP response failed after {len(partial_output)} partial characters: {public_exception_summary(cause)}",
            partial_output=partial_output,
        )
        _bind_private_cause(self, cause)


class ACPRequestOutcomeUnknownError(ACPResponseError):
    """Raised when a prompt may have started but no final response was observed.

    This is deliberately non-retryable: an agent may have completed tool calls
    or other external side effects before its transport failed.
    """

    request_may_have_started: ClassVar[bool] = True
    cause: BaseException
    __cause__: BaseException | None

    def __init__(self, cause: BaseException, *, partial_output: str = "") -> None:
        self.cause = cause
        super().__init__(
            f"ACP prompt outcome is unknown after transport failure: {public_exception_summary(cause)}",
            partial_output=partial_output,
        )
        _bind_private_cause(self, cause)


class ACPProtocolError(ACPResponseError):
    """Raised when an agent returns a malformed prompt lifecycle response."""

    message: str
    details: Any

    def __init__(self, message: str, *, partial_output: str = "", details: Any = None) -> None:
        self.message = message
        self.details = details
        super().__init__(self.format_diagnostic(include_sensitive=False), partial_output=partial_output)

    def format_diagnostic(self, *, include_sensitive: bool = False) -> str:
        """Keep provider values out of public strings, retaining explicit diagnostics."""
        if self.details is None:
            return self.message
        detail = repr(self.details) if include_sensitive else "<redacted>"
        return f"{self.message} | details={detail}"


class ACPPromptTimeoutError(ACPResponseError):
    """Raised when the end-to-end prompt deadline expires."""

    timeout: float

    def __init__(self, timeout: float, *, partial_output: str = "") -> None:
        self.timeout = timeout
        super().__init__(
            f"ACP prompt deadline exceeded {timeout:g}s",
            partial_output=partial_output,
        )


class ACPStreamTimeoutError(ACPResponseError):
    """Raised when a stream has no ACP activity for the configured interval."""

    timeout: float

    def __init__(self, timeout: float, *, partial_output: str = "") -> None:
        self.timeout = timeout
        super().__init__(
            f"ACP stream had no activity for {timeout:g}s",
            partial_output=partial_output,
        )
