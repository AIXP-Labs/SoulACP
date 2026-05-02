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


def test_rpc_error_str_includes_all_context():
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
    # All fields surfaced — this is the whole point of the new class.
    assert "code=-32603" in s
    assert "Internal error" in s
    assert "session/prompt" in s
    assert "id=42" in s
    assert "elapsed_ms=1234" in s  # truncated to integer
    assert "sid=sess-xyz" in s
    assert "session expired" in s
    assert "stderr line A" in s
    assert "stderr line B" in s


def test_rpc_error_stderr_tail_capped_at_10_lines():
    from soulacp import RPCError

    tail = [f"line {i}" for i in range(20)]
    err = RPCError(code=-32603, message="x", stderr_tail=tail)
    s = str(err)
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

    # RPCError's __str__ embeds the message — overflow markers still match.
    err = RPCError(code=-32000, message="prompt is too long for this model")
    assert is_context_overflow(err)
