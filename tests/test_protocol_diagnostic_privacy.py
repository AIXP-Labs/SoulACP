"""Malformed provider values remain inspectable without public-text disclosure."""

import logging

import pytest

from soulacp import ACPConfig
from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.adapters.claude_client import ClaudeACPClient
from soulacp.errors import ACPProtocolError
from soulacp.privacy import public_exception_summary

MARKER = "SYNTHETIC_PRIVATE_DIAGNOSTIC"


@pytest.mark.parametrize("opt_in", [None, "true", "1"])
@pytest.mark.parametrize("value", [MARKER, {"private": MARKER}, [MARKER]])
def test_protocol_version_error_redacts_public_text_and_retains_details(monkeypatch, opt_in, value):
    if opt_in is None:
        monkeypatch.delenv("ACP_LOG_SENSITIVE_DATA", raising=False)
    else:
        monkeypatch.setenv("ACP_LOG_SENSITIVE_DATA", opt_in)
    with pytest.raises(ACPProtocolError, match="protocol version mismatch") as caught:
        ACPClientBase._validate_initialize_result({"protocolVersion": value})
    error = caught.value
    assert MARKER not in str(error)
    assert MARKER not in repr(error)
    assert MARKER not in public_exception_summary(error)
    assert error.details["protocolVersion"] == value
    assert MARKER in error.format_diagnostic(include_sensitive=True)


def option(**changes):
    return (
        dict(id=MARKER, name="Model", type="select", currentValue="safe", options=[dict(value="safe", name="Safe")])
        | changes
    )


@pytest.mark.parametrize(
    "values",
    [
        [option(name=None)],
        [option(description=1)],
        [option(category=1)],
        [option(_meta=[])],
        [option(type="boolean", currentValue="invalid")],
        [option(currentValue=None)],
        [option(currentValue="unadvertised")],
        [option(type="invalid")],
        [option(), option()],
        [option(options=None)],
        [option(options=[1])],
        [option(options=[dict(value="safe", name=None)])],
        [option(options=[dict(value=MARKER, name="Safe", _meta=[])])],
        [option(options=[dict(value=MARKER, name="Safe"), dict(value=MARKER, name="Safe")])],
        [option(options=[dict(group=MARKER, name="Group", options=[], _meta=[])])],
        [option(options=[dict(group=MARKER, name="Group", options=[])] * 2)],
    ],
)
def test_config_errors_do_not_echo_provider_identifiers(values):
    with pytest.raises(ACPProtocolError) as caught:
        ACPClientBase._validate_config_options(values, source="session/new")
    assert MARKER not in str(caught.value)
    assert MARKER not in repr(caught.value)
    assert MARKER in caught.value.format_diagnostic(include_sensitive=True)


def test_mode_error_retains_diagnostic_context_but_not_public_value():
    with pytest.raises(ACPProtocolError) as caught:
        ACPClientBase._validate_session_modes(
            dict(currentModeId=MARKER, availableModes=[dict(id=MARKER, name="Mode", _meta=[])]), source="session/new"
        )
    assert MARKER not in str(caught.value)
    assert MARKER in caught.value.format_diagnostic(include_sensitive=True)


def test_valid_payloads_and_partial_output_are_unchanged():
    original = [option()]
    assert ACPClientBase._validate_config_options(original, source="session/new") == original
    error = ACPProtocolError("safe reason", partial_output="synthetic output")
    assert str(error) == "safe reason"
    assert error.partial_output == "synthetic output"


@pytest.mark.parametrize("opt_in", [None, "true", "1"])
@pytest.mark.parametrize("branch", ["active", "selected", "rejected"])
async def test_claude_model_diagnostics_require_opt_in_and_escape_lines(monkeypatch, caplog, opt_in, branch):
    if opt_in is None:
        monkeypatch.delenv("ACP_LOG_SENSITIVE_DATA", raising=False)
    else:
        monkeypatch.setenv("ACP_LOG_SENSITIVE_DATA", opt_in)
    current = "sonnet\n" + MARKER
    client = ClaudeACPClient(ACPConfig(provider="claude", model="claude-acp/sonnet"))
    client.session_config_options = ACPClientBase._validate_config_options(
        [
            dict(
                id="model",
                name="Model",
                category="model",
                type="select",
                currentValue=current if branch == "active" else "default",
                options=[dict(value=current, name="Sonnet"), dict(value="default", name="Default")],
            )
        ],
        source="session/new",
    )
    calls = []

    async def select(config_id, value):
        calls.append((config_id, value))
        if branch == "rejected":
            raise RPCError(-32603, "synthetic rejection")

    monkeypatch.setattr(client, "set_config_option", select)
    caplog.set_level(logging.INFO, logger="soulacp.adapters.claude_client")
    assert await client._select_model_config_option("sonnet") is (branch != "rejected")
    assert calls == ([] if branch == "active" else [("model", current)])
    assert (MARKER in caplog.text) is (opt_in == "1")
    assert "\n" + MARKER not in caplog.text
