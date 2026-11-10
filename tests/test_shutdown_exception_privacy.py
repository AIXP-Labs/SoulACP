"""Failed process cleanup retains ownership without exposing raw OS errors."""

from __future__ import annotations

import traceback

import pytest

from soulacp import ACPConfig
from soulacp.adapters import base_client as base_module
from soulacp.adapters.base_client import ACPClientBase
from soulacp.adapters.opencode_client import OpenCodeACPClient
from soulacp.services.terminal_service import TerminalService, _Terminal

MARKER = "synthetic-private-shutdown-path"


class FailedAsyncProcess:
    returncode = None
    stdin = None
    _transport = None

    def __init__(self, failure):
        self.failure = failure

    def terminate(self):
        raise self.failure

    def kill(self):
        raise self.failure

    async def wait(self):
        raise self.failure


class FailedPopen:
    stdin = stdout = stderr = None

    def __init__(self, failure):
        self.failure = failure

    def poll(self):
        return None

    def terminate(self):
        raise self.failure

    def kill(self):
        raise self.failure

    def wait(self, timeout=None):
        raise self.failure


@pytest.mark.asyncio
@pytest.mark.parametrize("sensitive_logging", [False, True])
@pytest.mark.parametrize("boundary", ["terminal", "acp", "opencode_async", "opencode_sync"])
async def test_shutdown_errors_keep_private_cause_and_process_ownership(
    tmp_path, monkeypatch, boundary, sensitive_logging
):
    monkeypatch.setenv("ACP_LOG_SENSITIVE_DATA", "1" if sensitive_logging else "0")
    raw = PermissionError(13, MARKER, MARKER)
    process = FailedAsyncProcess(raw)

    async def no_native_tree(_process, _descendants):
        return None

    monkeypatch.setattr(base_module, "_terminate_process_tree", no_native_tree)
    if boundary == "terminal":
        service = TerminalService(str(tmp_path), inherit_parent_env=False)
        terminal = _Terminal(process=process, output_byte_limit=16)
        service._terminals["synthetic-owned-handle"] = terminal
        operation = service.close
    elif boundary == "acp":

        class Client(ACPClientBase):
            def _get_acp_command(self):
                raise AssertionError("no native process may start")

            async def _initialize(self):
                raise AssertionError("no provider may initialize")

        client = Client(ACPConfig())
        client.process = process

        async def operation():
            await client._reap_disconnected_transport(set())
    else:
        client = OpenCodeACPClient(ACPConfig(provider="opencode"))
        process = FailedPopen(raw)
        client._popen = process

        async def operation():
            if boundary == "opencode_async":
                await client._terminate_popen(process)
            else:
                client._terminate_popen_sync(process)

    with pytest.raises(RuntimeError) as caught:
        await operation()
    error = caught.value
    assert MARKER not in str(error)
    assert MARKER not in "".join(traceback.format_exception(error))
    diagnostic = error.cause
    if boundary == "terminal":
        assert service._terminals["synthetic-owned-handle"] is terminal
        diagnostic = diagnostic.cause
    elif boundary == "acp":
        assert client.process is process
    else:
        assert client._popen is process
    assert diagnostic is raw
