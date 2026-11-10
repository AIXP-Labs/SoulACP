"""OpenCode's real RPC and dispatch queue preserve coalesced response order."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace

import pytest

from soulacp.adapters.opencode_client import OpenCodeACPClient
from soulacp.config import ACPConfig
from soulacp.errors import ACPProtocolError


def config_options(value: bool) -> list[dict]:
    return [{"id": "verbose", "name": "Verbose", "type": "boolean", "currentValue": value}]


@pytest.mark.parametrize("foreign", [False, True], ids=["same-session", "foreign-session"])
async def test_opencode_coalesced_session_response_and_updates(foreign):
    client = OpenCodeACPClient(ACPConfig(provider="opencode", inherit_parent_env=False, timeout_connect=2))
    client._connected = True
    client._popen = SimpleNamespace(poll=lambda: None)
    client._dispatch_queue = asyncio.Queue()
    # Only background cleanup of the fake transport is suppressed. The actual
    # OpenCode RPC, serial dispatcher, protocol validation and lifecycle run.
    client._schedule_host_cleanup = lambda: None
    client._schedule_process_cleanup = lambda: None
    completions, protocol_errors, updates = [], [], []
    original_complete = client._complete_rpc_lifecycle
    original_dispatch = client._dispatch

    def complete(method, result, transition_id, *args, **kwargs):
        completions.append(method)
        return original_complete(method, result, transition_id, *args, **kwargs)

    async def dispatch(message):
        try:
            return await original_dispatch(message)
        except ACPProtocolError as exc:
            protocol_errors.append(exc)
            raise

    client._complete_rpc_lifecycle = complete
    client._dispatch = dispatch
    client.set_update_callback(lambda update: updates.append((client.session_id, update["sessionUpdate"])))
    result = {"sessionId": "session-new", "configOptions": config_options(False)}

    async def write(data, _timeout):
        request = json.loads(data)
        assert request["method"] == "session/new"
        sid = "foreign-session" if foreign else "session-new"
        batch = [
            {"jsonrpc": "2.0", "id": request["id"], "result": result},
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {
                        "sessionUpdate": "available_commands_update",
                        "availableCommands": [{"name": "latest", "description": "Latest wire command"}],
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": sid,
                    "update": {"sessionUpdate": "config_option_update", "configOptions": config_options(True)},
                },
            },
        ]
        # No await between queue entries: the dispatcher sees one complete
        # buffered batch before the RPC waiter can resume.
        for frame in batch:
            client._dispatch_queue.put_nowait(frame)

    client._write_with_timeout = write
    dispatcher = asyncio.create_task(client._dispatch_messages())
    try:
        assert await client._rpc("session/new", {}, timeout=2) == result
        assert client.session_id == "session-new"
        if foreign:
            await asyncio.wait_for(asyncio.shield(dispatcher), 2)
            assert not client.is_connected and updates == []
            assert len(protocol_errors) == 1
        else:
            assert client.is_connected and not dispatcher.done()
            assert protocol_errors == []
            assert updates == [("session-new", "available_commands_update"), ("session-new", "config_option_update")]
            assert client.available_commands == [{"name": "latest", "description": "Latest wire command"}]
            assert client.session_config_options == config_options(True)
        assert completions == ["session/new"]
        assert client._pending == client._inflight == {}
        assert getattr(client, "_rpc_lifecycles", {}) == {}
        assert not client._session_transition_active and client._session_transition_id is None
    finally:
        client._connected = False
        dispatcher.cancel()
        with suppress(asyncio.CancelledError):
            await dispatcher
        client._dispatch_queue = None
        client._popen = None
