"""Regression tests for default-safe diagnostic rendering."""

from __future__ import annotations

import logging

from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.privacy import exception_summary


def test_plain_exception_summary_is_redacted_by_default(monkeypatch):
    monkeypatch.delenv("ACP_LOG_SENSITIVE_DATA", raising=False)
    secret = "C:/Users/private-user/project/private.txt"

    summary = exception_summary(RuntimeError(secret))

    assert summary == "RuntimeError"
    assert secret not in summary


def test_sensitive_exception_summary_requires_exact_opt_in(monkeypatch):
    secret = "private provider diagnostic"
    error = RuntimeError(secret)

    monkeypatch.setenv("ACP_LOG_SENSITIVE_DATA", "true")
    assert secret not in exception_summary(error)

    monkeypatch.setenv("ACP_LOG_SENSITIVE_DATA", "1")
    assert secret in exception_summary(error)


def test_rpc_error_sensitive_fields_are_not_rendered_by_default(monkeypatch):
    monkeypatch.delenv("ACP_LOG_SENSITIVE_DATA", raising=False)
    error = RPCError(
        -32000,
        "private provider diagnostic",
        data={"path": "C:/Users/private-user/private.txt"},
        session_id="private-session-id",
        stderr_tail=["private stderr"],
    )

    summary = exception_summary(error)

    assert "private" not in summary
    assert "<redacted>" in summary


def test_non_protocol_stdout_log_is_redacted_by_default(monkeypatch, caplog):
    monkeypatch.delenv("ACP_LOG_SENSITIVE_DATA", raising=False)
    secret = "private prompt text"
    caplog.set_level(logging.DEBUG, logger="soulacp.adapters.base_client")

    assert ACPClientBase._parse_json(f"{secret} {{not-json}}") is None

    assert secret not in caplog.text
    assert "non-protocol stdout line" in caplog.text


def test_exception_summary_survives_broken_exception_diagnostics(monkeypatch):
    class BrokenDiagnostic(Exception):
        @property
        def code(self):
            raise RuntimeError("broken code property")

        @property
        def method(self):
            raise RuntimeError("broken method property")

        def __str__(self):
            raise RuntimeError("broken string conversion")

    error = BrokenDiagnostic()
    monkeypatch.delenv("ACP_LOG_SENSITIVE_DATA", raising=False)
    assert exception_summary(error) == "BrokenDiagnostic"

    monkeypatch.setenv("ACP_LOG_SENSITIVE_DATA", "1")
    assert exception_summary(error) == "BrokenDiagnostic"
