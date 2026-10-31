"""Default response chains redact diagnostics without losing original objects."""

from __future__ import annotations

import traceback
from types import SimpleNamespace

import pytest

from soulacp import ACPConfig
from soulacp.adapters.base_client import ACPClientBase
from soulacp.errors import ACPPartialResponseError, ACPRequestOutcomeUnknownError
from soulacp.privacy import _bind_private_cause
from soulacp.retry import is_retryable

MARKER = "synthetic-cause-text-must-remain-private"


@pytest.mark.parametrize("kind", [ACPPartialResponseError, ACPRequestOutcomeUnknownError])
@pytest.mark.parametrize("raised", [False, True])
def test_response_cause_identity_is_out_of_display_chain(kind, raised):
    cause = OSError(MARKER)
    error = kind(cause, partial_output="exact partial text")
    if raised:
        try:
            raise cause
        except OSError:
            with pytest.raises(kind) as caught:
                raise error from error.__cause__
            assert caught.value is error
    assert error.cause is cause and str(error.cause) == MARKER
    assert error.partial_output == "exact partial text"
    assert error.__cause__ is not cause
    assert MARKER not in str(error) and MARKER not in repr(error)
    assert MARKER not in "".join(traceback.format_exception(error))
    if kind is ACPRequestOutcomeUnknownError:
        assert error.request_may_have_started is True and is_retryable(error) is False


def test_primary_and_transport_diagnostics_remain_distinct_and_private():
    transport = ConnectionError(MARKER)
    primary = FileNotFoundError(MARKER)
    error = ACPRequestOutcomeUnknownError(transport)
    assert _bind_private_cause(error, primary, primary=True) is error
    assert error.cause is transport and error.primary_cause is primary
    assert error.__cause__ is not primary and error.__cause__ is not transport
    assert MARKER not in "".join(traceback.format_exception(error))
    assert is_retryable(error) is False


@pytest.mark.asyncio
async def test_real_rpc_write_failure_does_not_reintroduce_raw_display_cause(monkeypatch):
    raw = OSError(MARKER)

    class Client(ACPClientBase):
        def _get_acp_command(self):
            raise AssertionError("No provider may start")

        async def _initialize(self):
            raise AssertionError("No provider may initialize")

    class Writer:
        def is_closing(self):
            return False

        def write(self, payload):
            pass

        async def drain(self):
            raise raw

    client = Client(ACPConfig())
    client._connected = True
    client.process = SimpleNamespace(returncode=None, stdin=Writer())
    retired = []

    async def retire(method):
        retired.append(method)

    monkeypatch.setattr(client, "_invalidate_uncertain_rpc_transport", retire)
    try:
        with pytest.raises(ACPRequestOutcomeUnknownError) as caught:
            await client._rpc("session/prompt", {"prompt": []}, timeout=1)
        error = caught.value
        assert error.cause is raw and error.request_may_have_started is True
        assert retired == ["session/prompt"] and is_retryable(error) is False
        assert MARKER not in "".join(traceback.format_exception(error))
    finally:
        client._connected = False
        client.process = None
