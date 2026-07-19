"""Tests for RPCError and code-aware retry/overflow detection."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# RPCError construction & formatting
# ---------------------------------------------------------------------------


def test_rpc_error_basic_fields():
    from soulacp import RPCError

    err = RPCError(code=-32603, message="Internal error")
    assert err.code == -32603
    assert err.message == "Internal error"
    assert err.data is None
    assert err.method is None
    assert err.msg_id is None
    assert err.elapsed_ms is None
    assert err.stderr_tail == []
    assert err.session_id is None


def test_rpc_error_str_redacts_provider_controlled_context():
    from soulacp import RPCError

    err = RPCError(
        code=-32603,
        message="Internal error",
        data={"detail": "session expired"},
        method="session/prompt",
        msg_id=42,
        elapsed_ms=1234.5,
        stderr_tail=["stderr line A", "stderr line B"],
        session_id="sess-xyz",
    )
    s = str(err)
    assert "code=-32603" in s
    assert "Internal error" in s
    assert "session/prompt" in s
    assert "id=42" in s
    assert "elapsed_ms=1234" in s  # truncated to integer
    assert "sid=<redacted>" in s
    assert "data=<redacted>" in s
    assert "stderr_tail=<redacted:2 lines>" in s
    assert "sess-xyz" not in s
    assert "session expired" not in s
    assert "stderr line A" not in s
    assert "stderr line B" not in s


def test_rpc_error_explicit_sensitive_diagnostic_includes_raw_context():
    from soulacp import RPCError

    err = RPCError(
        code=-32603,
        message="private provider message",
        data={"detail": "session expired"},
        method="session/prompt",
        stderr_tail=["stderr line A", "stderr line B"],
        session_id="sess-xyz",
    )

    diagnostic = err.format_diagnostic(include_sensitive=True)

    assert "private provider message" in diagnostic
    assert "sid=sess-xyz" in diagnostic
    assert "session expired" in diagnostic
    assert "stderr line A" in diagnostic
    assert "stderr line B" in diagnostic


def test_rpc_error_stderr_tail_capped_at_10_lines():
    from soulacp import RPCError

    tail = [f"line {i}" for i in range(20)]
    err = RPCError(code=-32603, message="x", stderr_tail=tail)
    s = err.format_diagnostic(include_sensitive=True)
    # Only the last 10 should appear inline.
    assert "line 19" in s
    assert "line 10" in s
    assert "line 9" not in s


def test_rpc_error_isinstance_of_exception():
    from soulacp import RPCError

    err = RPCError(code=-32600, message="x")
    assert isinstance(err, Exception)
    # Caller-side `except Exception` still catches it (back-compat).
    try:
        raise err
    except Exception as e:
        assert e is err


# ---------------------------------------------------------------------------
# RPCError.is_retryable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [-32603, -32000, -32001, -32099])
def test_rpc_error_is_retryable_codes(code):
    from soulacp import RPCError

    assert RPCError(code=code, message="x").is_retryable


@pytest.mark.parametrize("code", [-32700, -32600, -32601, -32602, 0, 100, None])
def test_rpc_error_not_retryable_codes(code):
    from soulacp import RPCError

    assert not RPCError(code=code, message="x").is_retryable


def test_fatal_subprocess_exit_remains_detectable_for_control_plane_retry():
    from soulacp import RPCError

    err = RPCError(
        code=-32603,
        message="Internal error",
        data={"details": "Claude Code process exited with code 3221226505"},
    )
    assert err.is_retryable
    assert err._is_fatal_subprocess_exit()


def test_prompt_rpc_error_is_not_retryable_without_idempotency_key():
    from soulacp import RPCError

    err = RPCError(code=-32603, message="Internal error", method="session/prompt")
    assert not err.is_retryable


def test_authentication_rpc_error_requires_user_action_instead_of_retry():
    from soulacp import RPCError

    err = RPCError(code=-32000, message="authentication method is no longer supported", method="authenticate")
    assert not err.is_retryable


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (-32000, "Authentication required"),
        (-32002, "Resource not found"),
    ],
)
def test_standard_acp_terminal_errors_are_visible_and_not_retried(code, message):
    from soulacp import RPCError

    err = RPCError(code=code, message=message, method="session/resume")

    assert not err.is_retryable
    assert message in str(err)


def test_provider_specific_server_error_keeps_legacy_retry_behavior():
    from soulacp import RPCError

    assert RPCError(code=-32000, message="provider temporarily overloaded").is_retryable


# ---------------------------------------------------------------------------
# retry.is_retryable integration with RPCError
# ---------------------------------------------------------------------------


def test_retry_is_retryable_recognises_rpc_error_code():
    from soulacp import RPCError
    from soulacp.retry import is_retryable

    # Code-based decision — even with no keyword in message.
    assert is_retryable(RPCError(code=-32603, message="opaque server failure"))
    assert is_retryable(RPCError(code=-32050, message="opaque server failure"))
    assert not is_retryable(RPCError(code=-32601, message="method not found"))


def test_retry_is_retryable_falls_back_to_keywords_for_plain_exception():
    from soulacp.retry import is_retryable

    # Plain Exception with retryable keyword still works (back-compat).
    assert is_retryable(ConnectionError("connection reset"))
    assert is_retryable(Exception("503 service unavailable"))
    assert not is_retryable(ValueError("bad value"))


# ---------------------------------------------------------------------------
# is_context_overflow
# ---------------------------------------------------------------------------


def test_context_overflow_detection_string_match():
    from soulacp.session import is_context_overflow

    assert is_context_overflow(Exception("Prompt is too long"))
    assert is_context_overflow(Exception("context length exceeded"))
    assert is_context_overflow(Exception("the context window is full"))
    assert not is_context_overflow(Exception("internal error"))
    assert not is_context_overflow(ConnectionError("reset"))


def test_context_overflow_detection_via_rpc_error():
    from soulacp import RPCError
    from soulacp.session import is_context_overflow

    # Detection inspects the structured raw message without exposing it in str().
    err = RPCError(code=-32000, message="prompt is too long for this model")
    assert is_context_overflow(err)
    assert "prompt is too long" not in str(err)


def test_context_overflow_detection_survives_broken_exception_string():
    from soulacp.session import is_context_overflow

    class BrokenError(Exception):
        def __str__(self):
            raise RuntimeError("broken string conversion")

    assert not is_context_overflow(BrokenError())


def test_public_response_wrapper_does_not_render_raw_cause():
    from soulacp import ACPRequestOutcomeUnknownError

    secret = "C:/Users/private-user/secret.txt"
    cause = RuntimeError(secret)
    wrapped = ACPRequestOutcomeUnknownError(cause)

    assert secret not in str(wrapped)
    assert "RuntimeError" in str(wrapped)
    assert wrapped.cause is cause
