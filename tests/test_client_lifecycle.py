"""Tests for base-client deadlines, error propagation, and cleanup."""

from __future__ import annotations

import asyncio
import copy
import gc
import io
import json
import os as _os
import subprocess as _subprocess
import sys
import threading
import time as _time
from contextlib import suppress as _suppress

import pytest

from soulacp.adapters import base_client as base_client_module
from soulacp.adapters import cursor_client as cursor_module
from soulacp.adapters import openclaw_client as openclaw_module
from soulacp.adapters import opencode_client as opencode_module
from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.adapters.cursor_client import CursorCLIClient
from soulacp.adapters.gemini_client import GeminiACPClient
from soulacp.adapters.kimi_client import KimiACPClient
from soulacp.adapters.openclaw_client import OpenClawACPClient
from soulacp.adapters.opencode_client import OpenCodeACPClient
from soulacp.adapters.vibe_client import VibeACPClient
from soulacp.config import ACPConfig
from soulacp.errors import (
    ACPPartialResponseError,
    ACPPromptTimeoutError,
    ACPProtocolError,
    ACPProviderError,
    ACPRequestOutcomeUnknownError,
    ACPStreamTimeoutError,
    ACPUnsupportedCapabilityError,
)


def make_config(**overrides) -> ACPConfig:
    values = {
        "pool_size": 1,
        "pool_max_connections": 1,
        "pool_keepalive_interval": 0,
        "timeout_prompt": 0.05,
        "timeout_stream": 0.02,
    }
    values.update(overrides)
    return ACPConfig(**values)


@pytest.fixture(autouse=True)
def _clear_plain_openclaw_credentials(monkeypatch):
    monkeypatch.delenv("OPENCLAW_TOKEN", raising=False)
    monkeypatch.delenv("OPENCLAW_PASSWORD", raising=False)


def test_openclaw_uses_isolated_bridge_session_by_default(monkeypatch):
    monkeypatch.setattr(openclaw_module, "find_openclaw_binary", lambda: "openclaw")
    monkeypatch.delenv("OPENCLAW_SESSION_KEY", raising=False)

    command = OpenClawACPClient(make_config(provider="openclaw"))._get_acp_command()

    assert "--session" not in command


def test_openclaw_shared_session_must_be_explicit(monkeypatch):
    monkeypatch.setattr(openclaw_module, "find_openclaw_binary", lambda: "openclaw")
    monkeypatch.setenv("OPENCLAW_SESSION_KEY", "agent:team:shared")

    command = OpenClawACPClient(make_config(provider="openclaw"))._get_acp_command()

    index = command.index("--session")
    assert command[index + 1] == "agent:team:shared"


def test_openclaw_command_uses_configured_child_environment(monkeypatch):
    monkeypatch.setattr(openclaw_module, "find_openclaw_binary", lambda: "openclaw")
    monkeypatch.setenv("OPENCLAW_SESSION_KEY", "agent:process:session")
    client = OpenClawACPClient(
        make_config(
            provider="openclaw",
            extra_env={
                "OPENCLAW_SESSION_KEY": "agent:config:session",
                "OPENCLAW_VERBOSE": "true",
            },
        )
    )

    command = client._get_acp_command()

    assert command[command.index("--session") + 1] == "agent:config:session"
    assert "--verbose" in command


def test_openclaw_command_does_not_read_parent_in_minimal_environment(monkeypatch):
    monkeypatch.setattr(openclaw_module, "find_openclaw_binary", lambda: "openclaw")
    monkeypatch.setenv("OPENCLAW_URL", "wss://parent.invalid")
    monkeypatch.setenv("OPENCLAW_TOKEN", "parent-secret")
    monkeypatch.setenv("OPENCLAW_SESSION_KEY", "agent:parent:session")
    monkeypatch.setenv("OPENCLAW_VERBOSE", "true")
    client = OpenClawACPClient(
        make_config(
            provider="openclaw",
            inherit_parent_env=False,
        )
    )

    assert client._get_acp_command() == ["openclaw", "acp", "--no-prefix-cwd"]


@pytest.mark.parametrize(
    ("name", "safe_name"),
    [
        ("OPENCLAW_TOKEN", "OPENCLAW_TOKEN_FILE"),
        ("OPENCLAW_PASSWORD", "OPENCLAW_PASSWORD_FILE"),
    ],
)
def test_openclaw_rejects_plaintext_argv_credentials(monkeypatch, name, safe_name):
    monkeypatch.setattr(openclaw_module, "find_openclaw_binary", lambda: "openclaw")
    secret = "do-not-expose-this-secret"
    monkeypatch.setenv(name, secret)

    with pytest.raises(ValueError) as exc_info:
        OpenClawACPClient(make_config(provider="openclaw"))._get_acp_command()

    assert safe_name in str(exc_info.value)
    assert secret not in str(exc_info.value)


def test_openclaw_accepts_file_based_credentials(monkeypatch):
    monkeypatch.setattr(openclaw_module, "find_openclaw_binary", lambda: "openclaw")
    client = OpenClawACPClient(
        make_config(
            provider="openclaw",
            extra_env={"OPENCLAW_TOKEN_FILE": "C:/secrets/openclaw-token"},
        )
    )

    command = client._get_acp_command()

    assert command[command.index("--token-file") + 1] == "C:/secrets/openclaw-token"
    assert "--token" not in command


@pytest.mark.parametrize(
    "url",
    ["ws://127.0.0.1:18789", "wss://gateway.example.test:18789/socket"],
)
def test_openclaw_accepts_absolute_gateway_websocket_url(monkeypatch, url):
    monkeypatch.setattr(openclaw_module, "find_openclaw_binary", lambda: "openclaw")
    client = OpenClawACPClient(make_config(provider="openclaw", extra_env={"OPENCLAW_URL": url}))

    command = client._get_acp_command()

    assert command[command.index("--url") + 1] == url


@pytest.mark.parametrize(
    "url",
    [
        "https://gateway.example.test",
        " wss://gateway.example.test",
        "wss://gateway.example.test\\socket",
        "wss://gateway.example.test:99999",
    ],
)
def test_openclaw_rejects_invalid_gateway_websocket_url(monkeypatch, url):
    monkeypatch.setattr(openclaw_module, "find_openclaw_binary", lambda: "openclaw")
    client = OpenClawACPClient(make_config(provider="openclaw", extra_env={"OPENCLAW_URL": url}))

    with pytest.raises(ValueError, match="OPENCLAW_URL"):
        client._get_acp_command()


def test_openclaw_rejects_credentials_embedded_in_gateway_url(monkeypatch):
    monkeypatch.setattr(openclaw_module, "find_openclaw_binary", lambda: "openclaw")
    secret = "url-secret"
    monkeypatch.setenv("OPENCLAW_URL", f"wss://user:{secret}@gateway.example.test:18789")

    with pytest.raises(ValueError, match="embedded credentials") as exc_info:
        OpenClawACPClient(make_config(provider="openclaw"))._get_acp_command()

    assert secret not in str(exc_info.value)


@pytest.mark.asyncio
async def test_openclaw_resume_sends_complete_session_context():
    client = OpenClawACPClient(make_config(provider="openclaw", cwd="C:/workspace"))
    client.agent_capabilities = {"loadSession": True}
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        return {}

    client._rpc = fake_rpc

    assert await client.resume("session-existing") is True
    assert calls == [
        (
            "session/load",
            {
                "sessionId": "session-existing",
                "cwd": "C:/workspace",
                "mcpServers": [],
            },
            client.config.timeout_connect,
        )
    ]
    assert client.session_id == "session-existing"


@pytest.mark.asyncio
async def test_openclaw_explicit_off_mode_is_sent():
    client = OpenClawACPClient(make_config(provider="openclaw", model="openclaw/default/off", cwd="C:/workspace"))
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        if method == "session/new":
            return {"sessionId": "session-new"}
        return {}

    client._rpc = fake_rpc

    await client._initialize()

    assert calls[-1] == (
        "session/set_mode",
        {"sessionId": "session-new", "modeId": "off"},
        client.config.timeout_connect,
    )


@pytest.mark.asyncio
async def test_openclaw_optional_mode_does_not_swallow_transport_failure():
    client = OpenClawACPClient(make_config(provider="openclaw", model="openclaw/default/high"))

    async def fake_rpc(method, _params, timeout=None):
        if method == "session/new":
            return {"sessionId": "session-new"}
        if method == "session/set_mode":
            raise ConnectionError("transport lost")
        return {}

    client._rpc = fake_rpc

    with pytest.raises(ConnectionError, match="transport lost"):
        await client._initialize()


@pytest.mark.asyncio
async def test_kimi_uses_standard_login_authentication_contract():
    client = KimiACPClient(make_config(provider="kimi", model="kimi-acp/default"))
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        if method == "initialize":
            return {"authMethods": [{"id": "login"}]}
        if method == "session/new":
            return {"sessionId": "session-new"}
        return {}

    client._rpc = fake_rpc

    await client._initialize()

    authenticate = next(call for call in calls if call[0] == "authenticate")
    assert authenticate == ("authenticate", {"methodId": "login"}, client.config.timeout_connect)


@pytest.mark.asyncio
async def test_vibe_does_not_auto_start_advertised_interactive_auth():
    client = VibeACPClient(make_config(provider="vibe", model="vibe-acp/default"))
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        if method == "initialize":
            return {"authMethods": [{"id": "browser-auth"}]}
        if method == "session/new":
            return {"sessionId": "session-new"}
        return {}

    client._rpc = fake_rpc

    await client._initialize()

    assert "authenticate" not in {method for method, _params, _timeout in calls}


class StubClient(ACPClientBase):
    def __init__(self, config: ACPConfig) -> None:
        super().__init__(config)
        self._connected = True
        self.session_id = "session-test"
        self.notifications: list[tuple[str, dict | None]] = []

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _get_acp_command(self) -> list[str]:
        return ["fake-acp"]

    async def _initialize(self) -> None:
        return None

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        self.notifications.append((method, params))


class PartialFailureClient(StubClient):
    async def _rpc(self, method: str, params: dict, timeout: float | None = None):
        self._chunks.append("partial")
        raise RuntimeError("rpc failed")


class IncompleteClient(StubClient):
    async def _rpc(self, method: str, params: dict, timeout: float | None = None):
        return {}


class PartialIncompleteClient(StubClient):
    async def _rpc(self, method: str, params: dict, timeout: float | None = None):
        self._chunks.append("partial")
        return {}


class HangingClient(StubClient):
    async def _rpc(self, method: str, params: dict, timeout: float | None = None):
        await asyncio.Event().wait()


class ActivityClient(StubClient):
    async def _rpc(self, method: str, params: dict, timeout: float | None = None):
        for _ in range(3):
            await asyncio.sleep(0.1)
            self._handle_stream_update(
                {
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "activity-tool",
                            "status": "in_progress",
                        }
                    }
                }
            )
        self._handle_stream_update({"params": {"update": {"sessionUpdate": "turn_end"}}})
        return {"stopReason": "end_turn"}


class OneChunkClient(StubClient):
    async def _rpc(self, method: str, params: dict, timeout: float | None = None):
        self._handle_stream_update(
            {
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "partial"},
                    }
                }
            }
        )
        await asyncio.Event().wait()


class ImmediateTwoChunkClient(StubClient):
    async def _rpc(self, method: str, params: dict, timeout: float | None = None):
        for text in ("first", " second"):
            self._handle_stream_update(
                {
                    "params": {
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": text},
                        }
                    }
                }
            )
        return {"stopReason": "end_turn"}


class BlockingRequestClient(StubClient):
    def __init__(self, config: ACPConfig) -> None:
        super().__init__(config)
        self.request_gate = asyncio.Event()

    async def _handle_request(self, _msg: dict) -> None:
        await self.request_gate.wait()


class CaptureRequestClient(StubClient):
    def __init__(self, config: ACPConfig) -> None:
        super().__init__(config)
        self.results: list[tuple[int | str | None, object]] = []
        self.errors: list[tuple[int | str | None, int, str]] = []

    async def _send_result(self, msg_id: int | str | None, result: object) -> None:
        self.results.append((msg_id, result))

    async def _send_error(self, msg_id: int | str | None, code: int, message: str) -> None:
        self.errors.append((msg_id, code, message))


class BlockingCaptureRequestClient(CaptureRequestClient):
    def __init__(self, config: ACPConfig) -> None:
        super().__init__(config)
        self.request_gate = asyncio.Event()

    async def _handle_request(self, _msg: dict) -> None:
        await self.request_gate.wait()


@pytest.mark.asyncio
async def test_optional_model_setting_only_degrades_rpc_rejection():
    client = StubClient(make_config())

    async def rejected_rpc(_method, _params, timeout=None):
        raise RPCError(-32601, "method not found")

    client._rpc = rejected_rpc

    assert await client._set_model_if_supported("model-a") is False


@pytest.mark.asyncio
async def test_legacy_model_method_not_reprobed_after_method_not_found():
    command_identity = "cache-test-agent"
    cache_key = (StubClient, command_identity)
    base_client_module._UNSUPPORTED_LEGACY_MODEL_METHODS.discard(cache_key)
    calls = 0

    async def rejected_rpc(_method, _params, timeout=None):
        nonlocal calls
        calls += 1
        raise RPCError(-32601, "method not found")

    try:
        first = StubClient(make_config())
        second = StubClient(make_config())
        first._command_identity = command_identity
        second._command_identity = command_identity
        first._rpc = rejected_rpc
        second._rpc = rejected_rpc

        assert await first._set_model_if_supported("model-a") is False
        assert await second._set_model_if_supported("model-a") is False
        assert calls == 1
    finally:
        base_client_module._UNSUPPORTED_LEGACY_MODEL_METHODS.discard(cache_key)


@pytest.mark.asyncio
async def test_model_setting_skips_rpc_when_advertised_model_is_current():
    client = StubClient(make_config())
    client.session_config_options = [
        {
            "id": "model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": "claude-fable-5",
            "options": [
                {"value": "claude-fable-5", "name": "Fable"},
                {"value": "sonnet", "name": "Sonnet"},
            ],
        }
    ]

    async def unexpected_rpc(_method, _params, timeout=None):
        pytest.fail("an already-selected model must not emit an RPC")

    client._rpc = unexpected_rpc

    assert await client._set_model_if_supported("claude-fable-5") is True


@pytest.mark.asyncio
async def test_model_setting_uses_advertised_config_option():
    client = StubClient(make_config())
    client.session_config_options = [
        {
            "id": "preferred-model",
            "name": "Model",
            "category": "model",
            "type": "select",
            "currentValue": "claude-fable-5",
            "options": [
                {"value": "claude-fable-5", "name": "Fable"},
                {"value": "sonnet", "name": "Sonnet"},
            ],
        }
    ]
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        return {"configOptions": client.session_config_options}

    client._rpc = fake_rpc

    assert await client._set_model_if_supported("sonnet") is True
    assert calls == [
        (
            "session/set_config_option",
            {"sessionId": "session-test", "configId": "preferred-model", "value": "sonnet"},
            client.config.timeout_connect,
        )
    ]


@pytest.mark.asyncio
async def test_model_setting_does_not_probe_legacy_method_for_unadvertised_value():
    client = StubClient(make_config())
    client.session_config_options = [
        {
            "id": "model",
            "name": "Model",
            "type": "select",
            "currentValue": "default",
            "options": [{"value": "default", "name": "Default"}],
        }
    ]

    async def unexpected_rpc(_method, _params, timeout=None):
        pytest.fail("an advertised model option must suppress legacy method probing")

    client._rpc = unexpected_rpc

    assert await client._set_model_if_supported("missing-model") is False


@pytest.mark.asyncio
async def test_query_requires_an_active_session_before_transport_io():
    client = StubClient(make_config())
    client.session_id = None

    async def unexpected_rpc(*_args, **_kwargs):
        pytest.fail("a sessionless query must not reach the transport")

    client._rpc = unexpected_rpc

    with pytest.raises(RuntimeError, match="No active ACP session"):
        await client.query("hello")
    with pytest.raises(RuntimeError, match="No active ACP session"):
        await anext(client.query_stream("hello"))


@pytest.mark.asyncio
async def test_query_rechecks_session_after_waiting_for_query_lock():
    client = StubClient(make_config())
    await client._query_lock.acquire()

    async def unexpected_rpc(*_args, **_kwargs):
        pytest.fail("a session closed while waiting must not reach the transport")

    client._rpc = unexpected_rpc
    query_task = asyncio.create_task(client.query("hello"))
    await asyncio.sleep(0)
    client.session_id = None
    client._query_lock.release()

    with pytest.raises(RuntimeError, match="No active ACP session"):
        await query_task


@pytest.mark.asyncio
async def test_prompt_rejects_invalid_unicode_before_transport_io():
    client = StubClient(make_config())

    async def unexpected_rpc(*_args, **_kwargs):
        pytest.fail("an invalid prompt must not reach the transport")

    client._rpc = unexpected_rpc

    with pytest.raises(ValueError, match="valid Unicode"):
        await client.query("\ud800")
    with pytest.raises(ValueError, match="valid Unicode"):
        await anext(client.query_stream("\ud800"))


@pytest.mark.asyncio
async def test_optional_model_setting_propagates_transport_failure():
    client = StubClient(make_config())

    async def failed_rpc(_method, _params, timeout=None):
        raise ConnectionError("transport lost")

    client._rpc = failed_rpc

    with pytest.raises(ConnectionError, match="transport lost"):
        await client._set_model_if_supported("model-a")


@pytest.mark.asyncio
async def test_non_stream_rpc_failure_never_returns_partial_as_success():
    client = PartialFailureClient(make_config())

    with pytest.raises(RuntimeError) as exc_info:
        await client.query("hello")

    assert exc_info.value.partial_output == "partial"


@pytest.mark.asyncio
async def test_non_stream_missing_stop_reason_disconnects_and_raises():
    client = IncompleteClient(make_config(timeout_prompt=0.01))

    with pytest.raises(ACPProtocolError) as exc_info:
        await client.query("hello")

    assert exc_info.value.partial_output == ""
    assert client.notifications == [("session/cancel", {"sessionId": "session-test"})]
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_non_stream_cleanup_failure_does_not_mask_prompt_timeout():
    client = StubClient(make_config())

    async def timed_out_rpc(_method, _params, timeout=None):
        raise asyncio.TimeoutError

    async def failed_cleanup():
        raise RuntimeError("cleanup failed")

    client._rpc = timed_out_rpc
    client._cancel_after_timeout = failed_cleanup

    with pytest.raises(ACPPromptTimeoutError):
        await client.query("hello")


@pytest.mark.asyncio
async def test_non_stream_cleanup_failure_does_not_mask_protocol_error():
    client = IncompleteClient(make_config())

    async def failed_cleanup():
        raise RuntimeError("cleanup failed")

    client._cancel_after_timeout = failed_cleanup

    with pytest.raises(ACPProtocolError, match="stopReason"):
        await client.query("hello")


@pytest.mark.asyncio
async def test_non_stream_protocol_error_retains_partial_output():
    client = PartialIncompleteClient(make_config())

    with pytest.raises(ACPProtocolError) as exc_info:
        await client.query("hello")

    assert exc_info.value.partial_output == "partial"
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_cancelling_direct_non_stream_query_disconnects_inflight_prompt():
    client = HangingClient(make_config(timeout_prompt=10))
    task = asyncio.create_task(client.query("hello"))
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.notifications == [("session/cancel", {"sessionId": "session-test"})]
    assert client.is_connected is False


def test_message_end_update_does_not_complete_prompt_turn():
    client = StubClient(make_config())
    client._complete = asyncio.Event()
    client._streaming = True
    client._stream_queue = asyncio.Queue()

    client._handle_stream_update(
        {
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_end",
                    "content": {"type": "text", "text": "tail"},
                }
            }
        }
    )

    assert client._complete.is_set() is False
    queued = [client._stream_queue.get_nowait(), client._stream_queue.get_nowait()]
    assert queued[1] == "tail"
    assert None not in queued


def test_update_callback_cannot_mutate_internal_stream_processing():
    client = StubClient(make_config())

    def mutate_update(update: dict) -> None:
        update["sessionUpdate"] = "tool_call_update"
        update["content"]["text"] = "mutated"

    client.set_update_callback(mutate_update)
    client._handle_stream_update(
        {
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "original"},
                }
            }
        }
    )

    assert client._chunks == ["original"]


def test_session_transition_history_does_not_pollute_prompt_response_chunks():
    client = StubClient(make_config())
    observed = []
    client._chunks = ["previous prompt"]
    client._chunk_chars = len(client._chunks[0])
    client._session_transition_id = "session-test"
    client.set_update_callback(observed.append)

    update = {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": "replayed history"},
    }
    client._handle_stream_update({"params": {"update": update}})

    assert client._chunks == ["previous prompt"]
    assert observed == [update]


def test_session_update_requires_a_non_empty_discriminator():
    client = StubClient(make_config())

    for update in ({}, {"sessionUpdate": ""}, {"sessionUpdate": 1}):
        with pytest.raises(ACPProtocolError, match="sessionUpdate"):
            client._handle_stream_update({"params": {"update": update}})


@pytest.mark.asyncio
async def test_stream_inactivity_timeout_is_not_a_clean_end():
    client = HangingClient(make_config(timeout_prompt=1, timeout_stream=0.01))

    with pytest.raises(ACPStreamTimeoutError):
        _chunks = [chunk async for chunk in client.query_stream("hello")]

    assert client.notifications == [("session/cancel", {"sessionId": "session-test"})]
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_stream_cleanup_failure_preserves_timeout_and_resets_local_state():
    client = HangingClient(make_config(timeout_prompt=1, timeout_stream=0.01))
    rpc_cancelled = asyncio.Event()

    async def hanging_rpc(_method, _params, timeout=None):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            rpc_cancelled.set()
            raise

    async def failed_cleanup():
        raise RuntimeError("cleanup failed")

    client._rpc = hanging_rpc
    client._cancel_after_timeout = failed_cleanup

    with pytest.raises(ACPStreamTimeoutError):
        _chunks = [chunk async for chunk in client.query_stream("hello")]

    assert rpc_cancelled.is_set()
    assert client._streaming is False
    assert client._activity_queued is False
    assert client._stream_queue is None


@pytest.mark.asyncio
async def test_explicit_stream_close_propagates_cleanup_failure():
    client = OneChunkClient(make_config(timeout_prompt=1, timeout_stream=1))

    async def failed_cleanup():
        raise RuntimeError("cleanup failed")

    client._cancel_after_timeout = failed_cleanup
    stream = client.query_stream("hello")

    assert await anext(stream) == "partial"
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await stream.aclose()

    assert client._streaming is False
    assert client._stream_queue is None


@pytest.mark.asyncio
async def test_non_text_session_updates_reset_stream_inactivity():
    # The full response takes longer than timeout_stream, while each activity
    # gap remains comfortably shorter. The test therefore fails if non-text
    # updates stop resetting the inactivity timer.
    client = ActivityClient(make_config(timeout_prompt=2, timeout_stream=0.25))

    chunks = [chunk async for chunk in client.query_stream("hello")]

    assert chunks == []
    assert client.notifications == []


def test_non_text_activity_notifications_are_coalesced():
    client = StubClient(make_config())
    client._streaming = True
    client._stream_queue = asyncio.Queue()

    for _ in range(100):
        client._handle_stream_update(
            {
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "coalesced-tool",
                        "status": "in_progress",
                    }
                }
            }
        )

    assert client._stream_queue.qsize() == 1


@pytest.mark.asyncio
async def test_stream_still_obeys_end_to_end_prompt_deadline():
    client = HangingClient(make_config(timeout_prompt=0.02, timeout_stream=1))

    with pytest.raises(ACPPromptTimeoutError):
        _chunks = [chunk async for chunk in client.query_stream("hello")]


@pytest.mark.asyncio
async def test_stream_prompt_deadline_excludes_downstream_chunk_processing():
    client = ImmediateTwoChunkClient(make_config(timeout_prompt=0.02, timeout_stream=1))
    stream = client.query_stream("hello")

    assert await anext(stream) == "first"
    await asyncio.sleep(0.03)
    assert await anext(stream) == " second"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_server_request_does_not_block_reader_dispatch_loop():
    client = BlockingRequestClient(make_config())

    await asyncio.wait_for(
        client._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "terminal/wait_for_exit",
                "params": {"sessionId": "session-test"},
            }
        ),
        timeout=0.1,
    )

    assert len(client._request_tasks) == 1
    client.request_gate.set()
    await asyncio.gather(*client._request_tasks)


@pytest.mark.asyncio
async def test_server_request_concurrency_is_bounded(monkeypatch):
    client = BlockingCaptureRequestClient(make_config())
    monkeypatch.setattr(base_client_module, "MAX_HOST_REQUEST_TASKS", 2)

    for msg_id in (1, 2):
        await client._dispatch(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "terminal/output",
                "params": {"sessionId": "session-test"},
            }
        )
    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "terminal/output",
            "params": {"sessionId": "session-test"},
        }
    )

    assert len(client._request_tasks) == 2
    assert client.errors == [(3, -32000, "Too many concurrent ACP host requests")]
    client.request_gate.set()
    await asyncio.gather(*client._request_tasks)


@pytest.mark.asyncio
async def test_terminal_wait_limit_preserves_capacity_for_control_requests(monkeypatch):
    client = BlockingCaptureRequestClient(make_config())
    monkeypatch.setattr(base_client_module, "MAX_HOST_REQUEST_TASKS", 3)
    monkeypatch.setattr(base_client_module, "MAX_TERMINAL_WAIT_REQUESTS", 2)

    for msg_id in (1, 2, 3):
        await client._dispatch(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "terminal/wait_for_exit",
                "params": {"sessionId": "session-test", "terminalId": "terminal"},
            }
        )
    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "terminal/kill",
            "params": {"sessionId": "session-test", "terminalId": "terminal"},
        }
    )

    assert len(client._request_tasks) == 3
    assert client._terminal_wait_request_ids == {1, 2}
    assert client.errors == [(3, -32000, "Too many concurrent terminal wait requests")]

    client.request_gate.set()
    await asyncio.gather(*client._request_tasks)
    await asyncio.sleep(0)
    assert client._terminal_wait_request_ids == set()


@pytest.mark.asyncio
async def test_agent_can_cancel_an_inflight_host_request():
    client = CaptureRequestClient(make_config())
    request_started = asyncio.Event()
    never_finish = asyncio.Event()

    async def blocked_output(**_params):
        request_started.set()
        await never_finish.wait()

    client._terminal_service.get_output = blocked_output
    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": "terminal-1",
            "method": "terminal/output",
            "params": {"sessionId": "session-test", "terminalId": "terminal-1"},
        }
    )
    await request_started.wait()
    task = client._request_tasks_by_id["terminal-1"]

    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "method": "$/cancel_request",
            "params": {"requestId": "terminal-1"},
        }
    )
    await asyncio.gather(task, return_exceptions=True)

    assert client.errors == [("terminal-1", -32800, "Request cancelled")]
    assert client._request_tasks == set()
    assert client._request_tasks_by_id == {}


@pytest.mark.asyncio
async def test_cancel_request_notification_is_strictly_validated():
    client = CaptureRequestClient(make_config())

    for message in (
        {"jsonrpc": "2.0", "method": "$/cancel_request", "params": {}},
        {"jsonrpc": "2.0", "method": "$/cancel_request", "params": {"requestId": True}},
        {"jsonrpc": "2.0", "method": "$/cancel_request", "params": {"requestId": 1, "_meta": []}},
        {"jsonrpc": "2.0", "id": 1, "method": "$/cancel_request", "params": {"requestId": 1}},
    ):
        with pytest.raises(ACPProtocolError, match="cancel_request"):
            await client._dispatch(message)


@pytest.mark.asyncio
async def test_disconnected_client_ignores_late_host_request():
    client = BlockingRequestClient(make_config())
    client._connected = False

    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "terminal/output",
            "params": {"sessionId": "session-test"},
        }
    )

    assert client._request_tasks == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["terminal/output", "read_text_file"])
async def test_host_request_rejects_different_session(method: str):
    client = CaptureRequestClient(make_config())

    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": "request-1",
            "method": method,
            "params": {"sessionId": "old-session"},
        }
    )

    assert client.errors == [("request-1", -32602, f"{method} belongs to a different session")]


@pytest.mark.asyncio
async def test_session_update_must_belong_to_current_session():
    client = StubClient(make_config())

    with pytest.raises(ACPProtocolError, match="different session"):
        await client._dispatch(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "old-session",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "x"},
                    },
                },
            }
        )


@pytest.mark.asyncio
async def test_session_update_must_be_a_notification():
    client = StubClient(make_config())

    with pytest.raises(ACPProtocolError, match="notification without an id"):
        await client._dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/update",
                "params": {"sessionId": "session-test", "update": {}},
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", ["invalid", [], 1])
async def test_session_update_outer_meta_must_be_an_object_or_null(meta):
    client = StubClient(make_config())

    with pytest.raises(ACPProtocolError, match="_meta"):
        await client._dispatch(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": "session-test",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "x"},
                    },
                    "_meta": meta,
                },
            }
        )


def test_initialize_response_requires_matching_protocol_version():
    ACPClientBase._validate_initialize_result({"protocolVersion": 1, "_meta": None})

    for result in ({}, {"protocolVersion": True}, {"protocolVersion": 2}, {"protocolVersion": 1, "_meta": []}, None):
        with pytest.raises(ACPProtocolError, match=r"protocol|result|_meta"):
            ACPClientBase._validate_initialize_result(result)


def test_initialize_metadata_is_validated_copied_and_reset():
    client = StubClient(make_config())
    result = {
        "protocolVersion": 1,
        "agentCapabilities": {"sessionCapabilities": {"list": {}}},
        "agentInfo": {"name": "agent", "version": "1.2.3"},
        "authMethods": [{"id": "login", "name": "Browser login"}],
    }

    client._capture_initialize_metadata(result)
    result["agentCapabilities"]["sessionCapabilities"].clear()

    assert client.supports_session_capability("list") is True
    assert client.agent_info == {"name": "agent", "version": "1.2.3"}
    assert client.auth_methods == [{"id": "login", "name": "Browser login"}]

    for invalid in (
        {"protocolVersion": 1, "agentCapabilities": []},
        {"protocolVersion": 1, "agentInfo": "agent"},
        {"protocolVersion": 1, "agentInfo": {"name": "agent"}},
        {"protocolVersion": 1, "agentInfo": {"name": "agent", "version": 1}},
        {"protocolVersion": 1, "authMethods": ["login"]},
        {"protocolVersion": 1, "authMethods": [{}]},
        {"protocolVersion": 1, "authMethods": [{"id": ""}]},
        {"protocolVersion": 1, "authMethods": [{"id": "login"}]},
        {"protocolVersion": 1, "authMethods": [{"id": "login", "name": "Login", "description": 1}]},
        {
            "protocolVersion": 1,
            "agentInfo": {"name": "agent", "version": "1.0", "_meta": []},
        },
        {
            "protocolVersion": 1,
            "authMethods": [{"id": "login", "name": "Login", "_meta": []}],
        },
    ):
        with pytest.raises(ACPProtocolError):
            client._capture_initialize_metadata(invalid)


@pytest.mark.parametrize(
    "capabilities",
    [
        {"_meta": []},
        {"loadSession": 1},
        {"promptCapabilities": None},
        {"promptCapabilities": {"image": 1}},
        {"mcpCapabilities": []},
        {"mcpCapabilities": {"sse": "yes"}},
        {"sessionCapabilities": None},
        {"sessionCapabilities": {"list": True}},
        {"sessionCapabilities": {"list": {"_meta": []}}},
        {"auth": None},
        {"auth": {"logout": True}},
        {"auth": {"logout": {"_meta": []}}},
    ],
)
def test_initialize_rejects_malformed_known_agent_capabilities(capabilities):
    client = StubClient(make_config())

    with pytest.raises(ACPProtocolError, match="agentCapabilities"):
        client._capture_initialize_metadata({"protocolVersion": 1, "agentCapabilities": capabilities})


def test_initialize_params_advertise_boolean_config_support_without_mutation():
    original = {"protocolVersion": 1, "clientCapabilities": {"terminal": True}}

    normalized = ACPClientBase._normalize_rpc_params("initialize", original)

    assert normalized["clientCapabilities"]["session"]["configOptions"]["boolean"] == {}
    assert original == {"protocolVersion": 1, "clientCapabilities": {"terminal": True}}


def test_prompt_stop_reason_is_strict_acp_enum():
    assert ACPClientBase._require_prompt_stop_reason({"stopReason": "end_turn"}) == "end_turn"

    with pytest.raises(ACPProtocolError, match="unknown stopReason"):
        ACPClientBase._require_prompt_stop_reason({"stopReason": "done"})

    with pytest.raises(ACPProtocolError, match="_meta"):
        ACPClientBase._require_prompt_stop_reason({"stopReason": "end_turn", "_meta": []})


@pytest.mark.parametrize("method", ["session/new", "session/load", "session/resume"])
def test_session_setup_rpc_params_include_required_mcp_servers_without_mutating_input(method):
    original = {"sessionId": "s", "cwd": "C:/workspace"}

    normalized = ACPClientBase._normalize_rpc_params(method, original)

    assert normalized == {**original, "mcpServers": []}
    assert "mcpServers" not in original


def test_configured_session_context_is_capability_gated_injected_and_copied():
    config = make_config(
        cwd="C:/workspace",
        additional_directories=["C:/workspace/shared"],
        mcp_servers=[
            {
                "name": "local",
                "command": "C:/tools/mcp.exe",
                "args": [],
                "env": [],
            },
            {
                "type": "http",
                "name": "remote",
                "url": "https://mcp.example.test",
                "headers": [],
            },
        ],
    )
    client = StubClient(config)
    client.agent_capabilities = {
        "sessionCapabilities": {"additionalDirectories": {}},
        "mcpCapabilities": {"http": True},
    }
    original = {"cwd": "C:/workspace", "mcpServers": []}

    normalized = client._apply_configured_session_params("session/new", original)
    normalized["additionalDirectories"].append("C:/workspace/mutated")
    normalized["mcpServers"][0]["args"].append("--mutated")

    assert original == {"cwd": "C:/workspace", "mcpServers": []}
    assert config.additional_directories == ["C:/workspace/shared"]
    assert config.mcp_servers[0]["args"] == []


def test_configured_additional_directories_require_advertised_capability():
    client = StubClient(make_config(additional_directories=["C:/workspace/shared"]))

    with pytest.raises(ACPUnsupportedCapabilityError, match="additionalDirectories"):
        client._apply_configured_session_params("session/new", {"cwd": "C:/workspace", "mcpServers": []})


@pytest.mark.parametrize("transport", ["http", "sse"])
def test_configured_network_mcp_transport_requires_advertised_capability(transport):
    client = StubClient(
        make_config(
            mcp_servers=[
                {
                    "type": transport,
                    "name": "remote",
                    "url": "https://mcp.example.test",
                    "headers": [],
                }
            ]
        )
    )

    with pytest.raises(ACPUnsupportedCapabilityError, match=f"mcpCapabilities.{transport}"):
        client._apply_configured_session_params("session/new", {"cwd": "C:/workspace", "mcpServers": []})


def test_configured_stdio_mcp_transport_requires_no_optional_capability():
    client = StubClient(
        make_config(
            mcp_servers=[
                {
                    "name": "local",
                    "command": "C:/tools/mcp.exe",
                    "args": [],
                    "env": [],
                }
            ]
        )
    )

    normalized = client._apply_configured_session_params(
        "session/new",
        {"cwd": "C:/workspace", "mcpServers": []},
    )

    assert normalized["mcpServers"] == client.config.mcp_servers


@pytest.mark.asyncio
async def test_session_list_is_capability_gated_validated_and_copied():
    client = StubClient(make_config(cwd="C:/workspace"))
    client.agent_capabilities = {"sessionCapabilities": {"list": {}}}
    calls = []
    response = {
        "sessions": [{"sessionId": "session-1", "cwd": "C:/workspace"}],
        "nextCursor": "page-2",
    }

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        return response

    client._rpc = fake_rpc
    page = await client.list_sessions(cwd="C:/workspace", cursor="page-1")
    response["sessions"].clear()

    assert page == {
        "sessions": [{"sessionId": "session-1", "cwd": "C:/workspace"}],
        "nextCursor": "page-2",
    }
    assert calls == [
        (
            "session/list",
            {"cwd": "C:/workspace", "cursor": "page-1"},
            client.config.timeout_connect,
        )
    ]

    client.agent_capabilities = {}
    with pytest.raises(ACPUnsupportedCapabilityError, match=r"sessionCapabilities\.list"):
        await client.list_sessions()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"sessions": [], "_meta": []},
        {"sessions": [{"sessionId": "session-1", "cwd": "C:/workspace", "_meta": []}]},
    ],
)
async def test_session_list_rejects_malformed_response_metadata(response):
    client = StubClient(make_config())
    client.agent_capabilities = {"sessionCapabilities": {"list": {}}}

    async def fake_rpc(*_args, **_kwargs):
        return response

    client._rpc = fake_rpc

    with pytest.raises(ACPProtocolError, match="_meta"):
        await client.list_sessions()


@pytest.mark.asyncio
async def test_session_list_preserves_empty_opaque_cursor_tokens():
    client = StubClient(make_config())
    client.agent_capabilities = {"sessionCapabilities": {"list": {}}}
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        return {"sessions": [], "nextCursor": ""}

    client._rpc = fake_rpc

    assert await client.list_sessions(cursor="") == {"sessions": [], "nextCursor": ""}
    assert calls == [("session/list", {"cursor": ""}, client.config.timeout_connect)]


@pytest.mark.asyncio
async def test_session_list_requires_absolute_request_and_response_paths():
    client = StubClient(make_config())
    client.agent_capabilities = {"sessionCapabilities": {"list": {}}}

    with pytest.raises(ValueError, match="absolute"):
        await client.list_sessions(cwd="relative/path")

    async def relative_response(*_args, **_kwargs):
        return {"sessions": [{"sessionId": "session-1", "cwd": "relative/path"}]}

    client._rpc = relative_response
    with pytest.raises(ACPProtocolError, match="absolute cwd"):
        await client.list_sessions()

    async def relative_additional_directory(*_args, **_kwargs):
        return {
            "sessions": [
                {
                    "sessionId": "session-1",
                    "cwd": "C:/workspace",
                    "additionalDirectories": ["relative/path"],
                }
            ]
        }

    client._rpc = relative_additional_directory
    with pytest.raises(ACPProtocolError, match="additionalDirectories"):
        await client.list_sessions()

    for field in ("title", "updatedAt"):

        async def malformed_optional_field(*_args, field=field, **_kwargs):
            return {"sessions": [{"sessionId": "session-1", "cwd": "C:/workspace", field: {}}]}

        client._rpc = malformed_optional_field
        with pytest.raises(ACPProtocolError, match=field):
            await client.list_sessions()


@pytest.mark.asyncio
async def test_gemini_resume_matches_standard_session_id_field():
    client = GeminiACPClient(make_config(provider="gemini", cwd="C:/workspace"))
    client.agent_capabilities = {"loadSession": True, "sessionCapabilities": {"list": {}}}
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        if method == "session/list":
            return {"sessions": [{"sessionId": "session-existing", "cwd": "C:/workspace"}]}
        return {}

    client._rpc = fake_rpc

    assert await client.resume("session-existing") is True
    assert client.session_id == "session-existing"
    assert calls == [
        ("session/list", {}, client.config.timeout_connect),
        (
            "session/load",
            {"sessionId": "session-existing", "cwd": "C:/workspace", "mcpServers": []},
            client.config.timeout_connect,
        ),
    ]


@pytest.mark.asyncio
async def test_gemini_resume_rejects_session_missing_from_standard_list():
    client = GeminiACPClient(make_config(provider="gemini", cwd="C:/workspace"))
    client.agent_capabilities = {"loadSession": True, "sessionCapabilities": {"list": {}}}
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        return {"sessions": [{"sessionId": "session-other", "cwd": "C:/workspace"}]}

    client._rpc = fake_rpc

    assert await client.resume("session-missing") is False
    assert client.session_id is None
    assert calls == [("session/list", {}, client.config.timeout_connect)]


@pytest.mark.asyncio
async def test_gemini_resume_loads_when_session_list_has_more_pages():
    client = GeminiACPClient(make_config(provider="gemini", cwd="C:/workspace"))
    client.agent_capabilities = {"loadSession": True, "sessionCapabilities": {"list": {}}}
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        if method == "session/list":
            return {
                "sessions": [{"sessionId": "session-other", "cwd": "C:/workspace"}],
                "nextCursor": "page-2",
            }
        return {}

    client._rpc = fake_rpc

    assert await client.resume("session-existing") is True
    assert client.session_id == "session-existing"
    assert calls == [
        ("session/list", {}, client.config.timeout_connect),
        (
            "session/load",
            {"sessionId": "session-existing", "cwd": "C:/workspace", "mcpServers": []},
            client.config.timeout_connect,
        ),
    ]


@pytest.mark.asyncio
async def test_gemini_resume_skips_unadvertised_session_list():
    client = GeminiACPClient(make_config(provider="gemini", cwd="C:/workspace"))
    client.agent_capabilities = {"loadSession": True}
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        return {}

    client._rpc = fake_rpc

    assert await client.resume("session-existing") is True
    assert calls == [
        (
            "session/load",
            {"sessionId": "session-existing", "cwd": "C:/workspace", "mcpServers": []},
            client.config.timeout_connect,
        )
    ]


@pytest.mark.asyncio
async def test_session_delete_rejects_active_session_and_requires_capability():
    client = StubClient(make_config())
    client.agent_capabilities = {"sessionCapabilities": {"delete": {}}}
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        return {}

    client._rpc = fake_rpc
    await client.delete_session("session-old")
    assert calls == [("session/delete", {"sessionId": "session-old"}, client.config.timeout_connect)]

    with pytest.raises(ValueError, match="active"):
        await client.delete_session("session-test")

    client.agent_capabilities = {}
    with pytest.raises(ACPUnsupportedCapabilityError, match=r"sessionCapabilities\.delete"):
        await client.delete_session("session-old")


@pytest.mark.asyncio
async def test_authenticate_logout_and_session_close_are_capability_gated():
    client = StubClient(make_config())
    client.auth_methods = [{"id": "browser", "name": "Browser login"}]
    client.agent_capabilities = {
        "auth": {"logout": {}},
        "sessionCapabilities": {"close": {}},
    }
    client.session_config_options = [{"id": "brave", "name": "Brave", "type": "boolean", "currentValue": False}]
    client.session_mode_state = {
        "currentModeId": "ask",
        "availableModes": [{"id": "ask", "name": "Ask"}],
    }
    client.session_info = {"title": "Old title"}
    client.session_usage = {"used": 10, "size": 100}
    client.available_commands = [{"name": "old", "description": "Old command"}]
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        return {"_meta": {"test": method}}

    client._rpc = fake_rpc

    auth = await client.authenticate("browser")
    logout = await client.logout()
    closed = await client.close_session()

    assert auth == {"_meta": {"test": "authenticate"}}
    assert logout == {"_meta": {"test": "logout"}}
    assert closed == {"_meta": {"test": "session/close"}}
    assert calls == [
        ("authenticate", {"methodId": "browser"}, client.config.timeout_connect),
        ("logout", {}, client.config.timeout_connect),
        ("session/close", {"sessionId": "session-test"}, client.config.timeout_connect),
    ]
    assert client.session_id is None
    assert client.session_config_options == []
    assert client.session_mode_state is None
    assert client.session_info == {}
    assert client.session_usage is None
    assert client.available_commands == []
    assert client.is_pool_reusable is False

    with pytest.raises(ACPUnsupportedCapabilityError, match="authentication method missing"):
        await client.authenticate("missing")
    client.agent_capabilities = {}
    with pytest.raises(ACPUnsupportedCapabilityError, match=r"auth\.logout"):
        await client.logout()
    with pytest.raises(ACPUnsupportedCapabilityError, match=r"sessionCapabilities\.close"):
        await client.close_session("session-other")


@pytest.mark.asyncio
async def test_new_control_methods_reject_non_object_responses():
    client = StubClient(make_config())
    client.auth_methods = [{"id": "browser"}]
    client.agent_capabilities = {
        "auth": {"logout": {}},
        "sessionCapabilities": {"close": {}},
    }

    async def fake_rpc(*_args, **_kwargs):
        return None

    client._rpc = fake_rpc
    with pytest.raises(ACPProtocolError, match="authenticate response"):
        await client.authenticate("browser")
    with pytest.raises(ACPProtocolError, match="logout response"):
        await client.logout()
    with pytest.raises(ACPProtocolError, match="session/close response"):
        await client.close_session()


@pytest.mark.asyncio
async def test_control_methods_reject_malformed_metadata_before_state_changes():
    client = StubClient(make_config())
    client.auth_methods = [{"id": "browser"}]
    client.agent_capabilities = {
        "auth": {"logout": {}},
        "sessionCapabilities": {"close": {}, "delete": {}},
    }
    client.session_info = {"title": "Keep"}

    async def fake_rpc(*_args, **_kwargs):
        return {"_meta": []}

    client._rpc = fake_rpc

    for operation in (
        lambda: client.authenticate("browser"),
        client.logout,
        client.close_session,
        lambda: client.delete_session("session-old"),
    ):
        with pytest.raises(ACPProtocolError, match="_meta"):
            await operation()

    assert client.session_id == "session-test"
    assert client.session_info == {"title": "Keep"}
    assert client.is_pool_reusable is True


@pytest.mark.asyncio
async def test_control_methods_reject_invalid_unicode_before_transport_io():
    client = StubClient(make_config())
    client.auth_methods = [{"id": "browser"}]
    client.agent_capabilities = {
        "sessionCapabilities": {"close": {}, "delete": {}, "list": {}, "resume": {}},
    }
    client.session_config_options = [
        {
            "id": "model",
            "name": "Model",
            "type": "select",
            "currentValue": "default",
            "options": [{"value": "default", "name": "Default"}],
        }
    ]
    client.session_mode_state = {
        "currentModeId": "ask",
        "availableModes": [{"id": "ask", "name": "Ask"}],
    }

    async def unexpected_rpc(*_args, **_kwargs):
        pytest.fail("invalid Unicode must not reach the transport")

    client._rpc = unexpected_rpc

    for operation in (
        lambda: client.authenticate("\ud800"),
        lambda: client.close_session("\ud800"),
        lambda: client.delete_session("\ud800"),
        lambda: client.list_sessions(cursor="\ud800"),
        lambda: client.set_config_option("\ud800", "default"),
        lambda: client.set_config_option("model", "\ud800"),
        lambda: client.set_mode("\ud800"),
        lambda: client._resume_if_available("session/resume", "\ud800", agent_name="test"),
    ):
        with pytest.raises(ValueError, match="valid Unicode"):
            await operation()


@pytest.mark.asyncio
async def test_automatic_auth_only_tolerates_explicit_rpc_rejection():
    client = StubClient(make_config())
    calls = []

    async def rejected_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        raise RPCError(code=-32000, message="credentials already inherited", method=method)

    client._rpc = rejected_rpc

    assert (
        await client._authenticate_if_advertised(
            {"authMethods": [{"id": "browser"}]},
            agent_name="Test Agent",
        )
        is False
    )
    assert calls == [("authenticate", {"methodId": "browser"}, client.config.timeout_connect)]


@pytest.mark.asyncio
async def test_automatic_auth_propagates_transport_failure():
    client = StubClient(make_config())

    async def disconnected_rpc(method, params, timeout=None):
        raise ConnectionError("transport lost")

    client._rpc = disconnected_rpc

    with pytest.raises(ConnectionError, match="transport lost"):
        await client._authenticate_if_advertised(
            {"authMethods": [{"id": "browser"}]},
            agent_name="Test Agent",
        )


@pytest.mark.asyncio
async def test_automatic_auth_noops_without_advertised_method():
    client = StubClient(make_config())

    assert await client._authenticate_if_advertised({}, agent_name="Test Agent") is False


@pytest.mark.asyncio
async def test_resume_helper_only_degrades_explicit_rpc_rejection():
    client = StubClient(make_config(cwd="C:/workspace"))
    client.agent_capabilities = {"loadSession": True}
    calls = []

    async def rejected_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        raise RPCError(code=-32000, message="unknown session", method=method)

    client._rpc = rejected_rpc

    assert (
        await client._resume_if_available(
            "session/load",
            "session-old",
            agent_name="Test Agent",
        )
        is False
    )
    assert client.session_id == "session-test"
    assert calls == [
        (
            "session/load",
            {"sessionId": "session-old", "cwd": "C:/workspace", "mcpServers": []},
            client.config.timeout_connect,
        )
    ]


@pytest.mark.asyncio
async def test_resume_helper_degrades_resource_not_found_code():
    client = StubClient(make_config())
    client.agent_capabilities = {"sessionCapabilities": {"resume": {}}}

    async def missing_rpc(method, params, timeout=None):
        raise RPCError(code=-32002, message="resource unavailable", method=method)

    client._rpc = missing_rpc

    assert (
        await client._resume_if_available(
            "session/resume",
            "session-old",
            agent_name="Test Agent",
        )
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "message"),
    [
        (-32000, "Authentication required"),
        (-32603, "Internal error"),
        (-32602, "Invalid params"),
    ],
)
async def test_resume_helper_propagates_rpc_failures_that_do_not_prove_session_is_missing(code, message):
    client = StubClient(make_config())
    client.agent_capabilities = {"sessionCapabilities": {"resume": {}}}
    error = RPCError(code=code, message=message, method="session/resume")

    async def rejected_rpc(method, params, timeout=None):
        raise error

    client._rpc = rejected_rpc

    with pytest.raises(RPCError) as exc_info:
        await client._resume_if_available(
            "session/resume",
            "session-old",
            agent_name="Test Agent",
        )

    assert exc_info.value is error


@pytest.mark.asyncio
async def test_resume_helper_propagates_transport_failure():
    client = StubClient(make_config())
    client.agent_capabilities = {"sessionCapabilities": {"resume": {}}}

    async def disconnected_rpc(method, params, timeout=None):
        raise ConnectionError("transport lost")

    client._rpc = disconnected_rpc

    with pytest.raises(ConnectionError, match="transport lost"):
        await client._resume_if_available(
            "session/resume",
            "session-old",
            agent_name="Test Agent",
        )


@pytest.mark.asyncio
async def test_resume_helper_waits_for_in_flight_query_state():
    client = StubClient(make_config())
    client.agent_capabilities = {"sessionCapabilities": {"resume": {}}}
    rpc_started = asyncio.Event()

    async def fake_rpc(_method, _params, timeout=None):
        rpc_started.set()
        return {}

    client._rpc = fake_rpc
    await client._query_lock.acquire()
    resume = asyncio.create_task(
        client._resume_if_available(
            "session/resume",
            "session-new",
            agent_name="Test Agent",
        )
    )
    await asyncio.sleep(0)

    assert rpc_started.is_set() is False
    assert resume.done() is False

    client._query_lock.release()
    assert await resume is True
    assert client.session_id == "session-new"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "capability"),
    [
        ("session/load", "loadSession"),
        ("session/resume", "sessionCapabilities.resume"),
    ],
)
async def test_resume_helper_returns_false_without_advertised_capability(method, capability):
    client = StubClient(make_config())

    async def unexpected_rpc(_method, _params, timeout=None):
        pytest.fail("unsupported resume must return before transport I/O")

    client._rpc = unexpected_rpc

    assert await client._resume_if_available(method, "session-old", agent_name="Test Agent") is False


@pytest.mark.asyncio
async def test_session_config_options_track_responses_updates_and_boolean_values():
    client = StubClient(make_config())
    initial = [
        {
            "id": "mode",
            "name": "Mode",
            "type": "select",
            "currentValue": "ask",
            "options": [{"value": "ask", "name": "Ask"}, {"value": "code", "name": "Code"}],
        },
        {"id": "brave", "name": "Brave", "type": "boolean", "currentValue": False},
    ]
    client._capture_session_config("session/new", {"sessionId": "session-test", "configOptions": initial})
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        updated = copy.deepcopy(initial)
        updated[1]["currentValue"] = True
        result = {"configOptions": updated}
        client._capture_session_config(method, result)
        return result

    client._rpc = fake_rpc
    options = await client.set_config_option("brave", True)

    assert calls == [
        (
            "session/set_config_option",
            {"sessionId": "session-test", "configId": "brave", "value": True, "type": "boolean"},
            client.config.timeout_connect,
        )
    ]
    assert options[1]["currentValue"] is True

    client._handle_stream_update(
        {
            "params": {
                "update": {
                    "sessionUpdate": "config_option_update",
                    "configOptions": [
                        {
                            "id": "mode",
                            "name": "Mode",
                            "type": "select",
                            "currentValue": "code",
                            "options": initial[0]["options"],
                        }
                    ],
                }
            }
        }
    )
    assert client.session_config_options[0]["currentValue"] == "code"


@pytest.mark.asyncio
async def test_session_config_option_rejects_unknown_ids_and_invalid_values():
    client = StubClient(make_config())
    client.session_config_options = [
        {
            "id": "mode",
            "name": "Mode",
            "type": "select",
            "currentValue": "ask",
            "options": [{"value": "ask", "name": "Ask"}],
        }
    ]

    with pytest.raises(ACPUnsupportedCapabilityError, match="missing"):
        await client.set_config_option("missing", "value")
    with pytest.raises(ValueError, match="invalid value"):
        await client.set_config_option("mode", "code")


@pytest.mark.asyncio
async def test_session_config_accepts_null_and_grouped_select_options():
    client = StubClient(make_config())
    client._capture_session_config(
        "session/new",
        {"sessionId": "session-test", "configOptions": None, "modes": None},
    )
    assert client.session_config_options == []
    assert client.session_mode_state is None

    grouped = [
        {
            "id": "model",
            "name": "Model",
            "type": "select",
            "currentValue": "fast",
            "options": [
                {
                    "group": "speed",
                    "name": "Speed",
                    "options": [
                        {"value": "fast", "name": "Fast"},
                        {"value": "deep", "name": "Deep"},
                    ],
                }
            ],
        }
    ]
    client._capture_session_config("session/new", {"configOptions": grouped})
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        updated = copy.deepcopy(grouped)
        updated[0]["currentValue"] = "deep"
        result = {"configOptions": updated}
        client._capture_session_config(method, result)
        return result

    client._rpc = fake_rpc
    options = await client.set_config_option("model", "deep")

    assert calls[0][1]["value"] == "deep"
    assert options[0]["currentValue"] == "deep"


@pytest.mark.parametrize(
    "options",
    [
        [{"id": "missing-name", "type": "boolean", "currentValue": True}],
        [{"id": "missing-type", "name": "Missing type", "currentValue": True}],
        [{"id": "flag", "name": "Flag", "type": "boolean", "currentValue": "yes"}],
        [
            {
                "id": "mode",
                "name": "Mode",
                "type": "select",
                "currentValue": "missing",
                "options": [{"value": "ask", "name": "Ask"}],
            }
        ],
        [
            {"id": "same", "name": "First", "type": "boolean", "currentValue": True},
            {"id": "same", "name": "Second", "type": "boolean", "currentValue": False},
        ],
    ],
)
def test_session_config_options_fail_closed_on_incomplete_or_inconsistent_state(options):
    with pytest.raises(ACPProtocolError):
        ACPClientBase._validate_config_options(options, source="session/new")


@pytest.mark.parametrize(
    "options",
    [
        [{"id": "flag", "name": "Flag", "type": "boolean", "currentValue": True, "_meta": []}],
        [
            {
                "id": "mode",
                "name": "Mode",
                "type": "select",
                "currentValue": "ask",
                "options": [{"value": "ask", "name": "Ask", "_meta": []}],
            }
        ],
        [
            {
                "id": "model",
                "name": "Model",
                "type": "select",
                "currentValue": "fast",
                "options": [
                    {
                        "group": "speed",
                        "name": "Speed",
                        "_meta": [],
                        "options": [{"value": "fast", "name": "Fast"}],
                    }
                ],
            }
        ],
    ],
)
def test_session_config_options_reject_malformed_nested_metadata(options):
    with pytest.raises(ACPProtocolError, match="_meta"):
        ACPClientBase._validate_config_options(options, source="session/new")


def test_session_config_response_validation_is_atomic():
    client = StubClient(make_config())
    client.session_config_options = [{"id": "old"}]
    client.session_mode_state = {
        "currentModeId": "ask",
        "availableModes": [{"id": "ask", "name": "Ask"}],
    }

    with pytest.raises(ACPProtocolError, match="_meta"):
        client._capture_session_config(
            "session/new",
            {
                "configOptions": [{"id": "flag", "name": "Flag", "type": "boolean", "currentValue": True}],
                "modes": {
                    "currentModeId": "code",
                    "availableModes": [{"id": "code", "name": "Code", "_meta": []}],
                },
            },
        )

    assert client.session_config_options == [{"id": "old"}]
    assert client.session_mode_state == {
        "currentModeId": "ask",
        "availableModes": [{"id": "ask", "name": "Ask"}],
    }


@pytest.mark.asyncio
async def test_session_modes_track_responses_updates_and_set_mode():
    client = StubClient(make_config())
    modes = {
        "currentModeId": "ask",
        "availableModes": [
            {"id": "ask", "name": "Ask"},
            {"id": "code", "name": "Code"},
        ],
    }
    client._capture_session_config("session/new", {"modes": modes})
    modes["availableModes"].clear()
    calls = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        return {}

    client._rpc = fake_rpc
    state = await client.set_mode("code")

    assert calls == [
        (
            "session/set_mode",
            {"sessionId": "session-test", "modeId": "code"},
            client.config.timeout_connect,
        )
    ]
    assert state["currentModeId"] == "code"
    state["availableModes"].clear()
    assert len(client.session_mode_state["availableModes"]) == 2

    client._handle_stream_update(
        {"params": {"update": {"sessionUpdate": "current_mode_update", "currentModeId": "ask"}}}
    )
    assert client.session_mode_state["currentModeId"] == "ask"

    with pytest.raises(ValueError, match="not advertised"):
        await client.set_mode("unknown")

    async def malformed_response(*_args, **_kwargs):
        return {"_meta": []}

    client._rpc = malformed_response
    with pytest.raises(ACPProtocolError, match="_meta"):
        await client.set_mode("code")
    assert client.session_mode_state["currentModeId"] == "ask"

    with pytest.raises(ACPProtocolError, match="not advertised"):
        client._handle_stream_update(
            {"params": {"update": {"sessionUpdate": "current_mode_update", "currentModeId": "unknown"}}}
        )
    with pytest.raises(ACPProtocolError, match="description"):
        client._validate_session_modes(
            {
                "currentModeId": "ask",
                "availableModes": [{"id": "ask", "name": "Ask", "description": 1}],
            },
            source="session/new",
        )


def test_stable_session_state_updates_are_validated_merged_and_detached():
    client = StubClient(make_config())
    info_update = {
        "sessionUpdate": "session_info_update",
        "title": "Working set",
        "updatedAt": "2026-07-18T12:00:00Z",
        "_meta": {"source": "agent"},
    }
    client._handle_stream_update({"params": {"update": info_update}})
    info_update["_meta"]["source"] = "mutated"

    assert client.session_info == {
        "title": "Working set",
        "updatedAt": "2026-07-18T12:00:00Z",
        "_meta": {"source": "agent"},
    }

    client._handle_stream_update({"params": {"update": {"sessionUpdate": "session_info_update", "title": None}}})
    assert client.session_info == {
        "updatedAt": "2026-07-18T12:00:00Z",
        "_meta": {"source": "agent"},
    }

    usage_update = {
        "sessionUpdate": "usage_update",
        "used": 512,
        "size": 4096,
        "cost": {"amount": 1.25, "currency": "USD", "_meta": {"estimated": True}},
    }
    client._handle_stream_update({"params": {"update": usage_update}})
    usage_update["cost"]["amount"] = 99
    assert client.session_usage == {
        "used": 512,
        "size": 4096,
        "cost": {"amount": 1.25, "currency": "USD", "_meta": {"estimated": True}},
    }

    commands_update = {
        "sessionUpdate": "available_commands_update",
        "availableCommands": [
            {
                "name": "review",
                "description": "Review the workspace",
                "input": {"hint": "optional focus"},
            }
        ],
    }
    client._handle_stream_update({"params": {"update": commands_update}})
    commands_update["availableCommands"][0]["name"] = "mutated"
    assert client.available_commands == [
        {
            "name": "review",
            "description": "Review the workspace",
            "input": {"hint": "optional focus"},
        }
    ]


@pytest.mark.parametrize(
    ("update", "match"),
    [
        ({"sessionUpdate": "session_info_update", "title": 1}, "title"),
        ({"sessionUpdate": "session_info_update", "_meta": []}, "_meta"),
        ({"sessionUpdate": "usage_update", "used": True, "size": 1}, "used"),
        ({"sessionUpdate": "usage_update", "used": 1}, "size"),
        ({"sessionUpdate": "usage_update", "used": 2**64, "size": 2**64 - 1}, "used"),
        (
            {
                "sessionUpdate": "usage_update",
                "used": 1,
                "size": 2,
                "cost": {"amount": float("inf"), "currency": "USD"},
            },
            "amount",
        ),
        ({"sessionUpdate": "available_commands_update"}, "availableCommands"),
        (
            {
                "sessionUpdate": "available_commands_update",
                "availableCommands": [{"name": "review"}],
            },
            "description",
        ),
        (
            {
                "sessionUpdate": "available_commands_update",
                "availableCommands": [{"name": "review", "description": "Review", "input": {}}],
            },
            "hint",
        ),
    ],
)
def test_stable_session_state_updates_reject_malformed_payloads(update, match):
    client = StubClient(make_config())

    with pytest.raises(ACPProtocolError, match=match):
        client._handle_stream_update({"params": {"update": update}})


@pytest.mark.parametrize(
    "update",
    [
        {
            "sessionUpdate": "user_message_chunk",
            "content": {"type": "text", "text": "question"},
            "messageId": "message-1",
        },
        {
            "sessionUpdate": "agent_thought_chunk",
            "content": {
                "type": "image",
                "data": "aW1hZ2U=",
                "mimeType": "image/png",
                "uri": None,
                "annotations": {"audience": ["assistant"], "priority": 0.5},
            },
        },
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "tool-1",
            "title": "Edit file",
            "kind": "edit",
            "status": "in_progress",
            "content": [
                {"type": "content", "content": {"type": "text", "text": "working"}},
                {"type": "diff", "path": "C:/workspace/a.py", "oldText": None, "newText": "pass\n"},
                {"type": "terminal", "terminalId": "terminal-1"},
            ],
            "locations": [{"path": "C:/workspace/a.py", "line": 0}],
        },
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "tool-1",
            "title": None,
            "kind": None,
            "status": "completed",
            "content": None,
            "locations": None,
        },
        {
            "sessionUpdate": "plan",
            "entries": [
                {"content": "Inspect", "priority": "high", "status": "completed"},
                {"content": "Fix", "priority": "medium", "status": "in_progress"},
                {"content": "Verify", "priority": "low", "status": "pending"},
            ],
        },
    ],
)
def test_stable_callback_updates_accept_schema_valid_payloads(update):
    client = StubClient(make_config())
    observed = []
    client.set_update_callback(observed.append)

    client._handle_stream_update({"params": {"update": update}})

    assert observed == [update]


@pytest.mark.parametrize(
    ("update", "match"),
    [
        ({"sessionUpdate": "agent_message_chunk"}, "content"),
        ({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": 1}}, "text"),
        (
            {"sessionUpdate": "agent_thought_chunk", "content": {"type": "image", "data": "x"}},
            "mimeType",
        ),
        (
            {
                "sessionUpdate": "user_message_chunk",
                "content": {"type": "text", "text": "x"},
                "messageId": 1,
            },
            "messageId",
        ),
        (
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "x", "annotations": {"audience": ["system"]}},
            },
            "audience",
        ),
        (
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "resource", "resource": {"text": "x"}},
            },
            "uri",
        ),
        ({"sessionUpdate": "tool_call", "title": "Run"}, "toolCallId"),
        ({"sessionUpdate": "tool_call", "toolCallId": "1"}, "title"),
        (
            {"sessionUpdate": "tool_call", "toolCallId": "1", "title": "Run", "kind": "shell"},
            "kind",
        ),
        (
            {"sessionUpdate": "tool_call_update", "toolCallId": "1", "status": "done"},
            "status",
        ),
        (
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "1",
                "content": [{"type": "diff", "path": "a.py"}],
            },
            "newText",
        ),
        (
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "1",
                "locations": [{"path": "a.py", "line": True}],
            },
            "line",
        ),
        ({"sessionUpdate": "plan"}, "entries"),
        (
            {"sessionUpdate": "plan", "entries": [{"content": "Run", "priority": "urgent", "status": "pending"}]},
            "priority",
        ),
        (
            {"sessionUpdate": "current_mode_update", "currentModeId": "ask", "_meta": []},
            "_meta",
        ),
    ],
)
def test_stable_callback_updates_reject_malformed_payloads_before_callback(update, match):
    client = StubClient(make_config())
    observed = []
    client.set_update_callback(observed.append)

    with pytest.raises(ACPProtocolError, match=match):
        client._handle_stream_update({"params": {"update": update}})

    assert observed == []


def test_session_transition_is_exclusive_and_restores_all_state_on_failure():
    client = StubClient(make_config())
    client.session_id = "session-old"
    client.session_config_options = [{"id": "old"}]
    client.session_mode_state = {"currentModeId": "ask"}
    client.session_info = {"title": "Old"}
    client.session_usage = {"used": 10, "size": 100}
    client.available_commands = [{"name": "old", "description": "Old"}]

    transition_id, snapshot = client._begin_rpc_lifecycle("session/new", {})
    assert transition_id is None
    assert snapshot is not None
    assert client.session_config_options == []
    assert client.session_info == {}
    with pytest.raises(RuntimeError, match="already in progress"):
        client._begin_rpc_lifecycle("session/resume", {"sessionId": "session-other"})

    with pytest.raises(ACPProtocolError, match="configOptions"):
        client._complete_rpc_lifecycle(
            "session/new",
            {"sessionId": "session-new", "configOptions": "invalid"},
            transition_id,
        )
    client._finish_rpc_lifecycle(transition_id, snapshot, False)

    assert client.session_id == "session-old"
    assert client.session_config_options == [{"id": "old"}]
    assert client.session_mode_state == {"currentModeId": "ask"}
    assert client.session_info == {"title": "Old"}
    assert client.session_usage == {"used": 10, "size": 100}
    assert client.available_commands == [{"name": "old", "description": "Old"}]
    assert client._session_transition_id is None
    assert client._session_transition_active is False


@pytest.mark.parametrize(
    ("method", "result"),
    [
        ("initialize", {"protocolVersion": 1, "_meta": []}),
        ("authenticate", {"_meta": []}),
        ("logout", {"_meta": []}),
        ("session/new", {"sessionId": "session-new", "_meta": []}),
        ("session/load", {"_meta": []}),
        ("session/resume", {"_meta": []}),
        ("session/prompt", {"stopReason": "end_turn", "_meta": []}),
        ("session/list", {"sessions": [], "_meta": []}),
        ("session/delete", {"_meta": []}),
        ("session/close", {"_meta": []}),
        ("session/set_mode", {"_meta": []}),
        ("session/set_config_option", {"configOptions": [], "_meta": []}),
    ],
)
def test_shared_rpc_lifecycle_validates_stable_response_metadata(method, result):
    client = StubClient(make_config())
    client.session_config_options = [{"id": "old"}]

    with pytest.raises(ACPProtocolError, match="_meta"):
        client._complete_rpc_lifecycle(method, result, None)

    assert client.session_id == "session-test"
    assert client.session_config_options == [{"id": "old"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auto_approve", "expected_option"),
    [(True, "allow-once"), (False, "reject-once")],
)
async def test_permission_selection_uses_acp_option_kind(auto_approve: bool, expected_option: str):
    client = CaptureRequestClient(make_config(auto_approve_permissions=auto_approve))
    await client._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "session/request_permission",
            "params": {
                "toolCall": {"toolCallId": "tool-1"},
                "options": [
                    {"optionId": "reject-always", "name": "Reject always", "kind": "reject_always"},
                    {"optionId": "allow-always", "name": "Allow always", "kind": "allow_always"},
                    {"optionId": "reject-once", "name": "Reject once", "kind": "reject_once"},
                    {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                ],
            },
        }
    )

    assert client.results == [(4, {"outcome": {"outcome": "selected", "optionId": expected_option}})]
    assert client.errors == []


@pytest.mark.asyncio
async def test_legacy_permission_ids_fail_closed_when_auto_approve_is_disabled():
    client = CaptureRequestClient(make_config(auto_approve_permissions=False))

    await client._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "session/request_permission",
            "params": {"options": [{"optionId": "legacy-unknown"}]},
        }
    )

    assert client.results == []
    assert client.errors == [(6, -32602, "Permission request has no compatible option")]


@pytest.mark.asyncio
async def test_legacy_permission_ids_remain_supported_when_auto_approve_is_enabled():
    client = CaptureRequestClient(make_config(auto_approve_permissions=True))

    await client._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 61,
            "method": "session/request_permission",
            "params": {"options": [{"optionId": "legacy-allow"}]},
        }
    )

    assert client.results == [(61, {"outcome": {"outcome": "selected", "optionId": "legacy-allow"}})]
    assert client.errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}]},
        {
            "toolCall": {},
            "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}],
        },
        {
            "toolCall": {"toolCallId": "tool-1"},
            "options": [{"optionId": "allow", "kind": "allow_once"}],
        },
        {
            "toolCall": {"toolCallId": "tool-1"},
            "options": [{"optionId": "allow", "name": "Allow", "kind": "sometimes"}],
        },
    ],
)
async def test_acp_v1_permission_request_rejects_malformed_required_fields(params):
    client = CaptureRequestClient(make_config(auto_approve_permissions=True))

    await client._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 62,
            "method": "session/request_permission",
            "params": params,
        }
    )

    assert client.results == []
    assert client.errors == [(62, -32602, "Invalid permission request parameters")]


@pytest.mark.asyncio
async def test_unknown_method_containing_permission_is_not_auto_approved():
    client = CaptureRequestClient(make_config(auto_approve_permissions=True))

    await client._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "extension/request_permission_override",
            "params": {"options": [{"optionId": "allow-once", "kind": "allow_once"}]},
        }
    )

    assert client.results == []
    assert client.errors == [(7, -32601, "Method not supported: extension/request_permission_override")]


@pytest.mark.asyncio
async def test_server_request_rejects_non_object_params():
    client = CaptureRequestClient(make_config())

    await client._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "fs/read_text_file",
            "params": ["not", "an", "object"],
        }
    )

    assert client.results == []
    assert client.errors == [(5, -32602, "JSON-RPC params must be an object")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params", "message"),
    [
        (
            "fs/read_text_file",
            {"path": "file.txt", "_meta": []},
            "Invalid host request metadata",
        ),
        (
            "session/request_permission",
            {"options": [{"optionId": "allow", "kind": "allow_once"}], "_meta": []},
            "Invalid host request metadata",
        ),
        (
            "session/request_permission",
            {
                "toolCall": {"toolCallId": "tool-1", "_meta": []},
                "options": [{"optionId": "allow", "kind": "allow_once"}],
            },
            "Invalid permission request metadata",
        ),
        (
            "session/request_permission",
            {"options": [{"optionId": "allow", "kind": "allow_once", "_meta": []}]},
            "Invalid permission request metadata",
        ),
    ],
)
async def test_host_requests_reject_malformed_metadata(method, params, message):
    client = CaptureRequestClient(make_config())

    await client._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 52,
            "method": method,
            "params": params,
        }
    )

    assert client.results == []
    assert client.errors == [(52, -32602, message)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (ValueError(r"invalid C:\\Users\\private\\secret.txt"), -32602, "Invalid host request parameters"),
        (OSError(r"failed C:\\Users\\private\\secret.txt"), -32603, "Host service request failed"),
    ],
)
async def test_host_service_errors_do_not_expose_local_exception_details(error, code, message):
    client = CaptureRequestClient(make_config())

    async def fail(**_params):
        raise error

    client._fs_service.read_text_file = fail
    await client._handle_request(
        {
            "jsonrpc": "2.0",
            "id": 51,
            "method": "fs/read_text_file",
            "params": {"path": "file.txt"},
        }
    )

    assert client.results == []
    assert client.errors == [(51, code, message)]
    assert "private" not in client.errors[0][2]


@pytest.mark.asyncio
async def test_server_request_preserves_null_json_rpc_id():
    client = CaptureRequestClient(make_config())

    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": None,
            "method": "extension/not_supported",
            "params": {},
        }
    )
    await asyncio.gather(*client._request_tasks)

    assert client.errors == [(None, -32601, "Method not supported: extension/not_supported")]


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", [1.5, True, 2**63, -(2**63) - 1, []])
async def test_server_request_rejects_ids_outside_acp_request_id(request_id):
    client = CaptureRequestClient(make_config())

    with pytest.raises(ACPProtocolError, match="request id"):
        await client._dispatch(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "extension/not_supported",
                "params": {},
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "1.0", 2])
async def test_dispatch_rejects_non_json_rpc_2_frames(version):
    client = StubClient(make_config())
    message = {"id": 1, "result": {}}
    if version is not None:
        message["jsonrpc"] = version

    with pytest.raises(ACPProtocolError, match=r"version 2\.0"):
        await client._dispatch(message)


@pytest.mark.asyncio
async def test_dispatch_rejects_frame_without_message_kind():
    client = StubClient(make_config())

    with pytest.raises(ACPProtocolError, match="request, response, or notification"):
        await client._dispatch({"jsonrpc": "2.0", "params": {}})


@pytest.mark.asyncio
@pytest.mark.parametrize("response_field", ["result", "error"])
async def test_dispatch_rejects_request_response_hybrid(response_field):
    client = StubClient(make_config())
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "extension/test",
        "params": {},
        response_field: {},
    }

    with pytest.raises(ACPProtocolError, match="must not contain result or error"):
        await client._dispatch(message)


@pytest.mark.parametrize("method", ["session/new", "session/load", "session/resume"])
def test_session_lifecycle_rejects_non_object_result(method):
    client = StubClient(make_config())
    transition_id = "session-existing" if method != "session/new" else None

    with pytest.raises(ACPProtocolError, match=f"{method} response result must be an object"):
        client._complete_rpc_lifecycle(method, [], transition_id)


def test_session_new_rejects_invalid_session_id_unicode():
    client = StubClient(make_config())

    with pytest.raises(ACPProtocolError, match="valid Unicode"):
        client._complete_rpc_lifecycle("session/new", {"sessionId": "\ud800"}, None)


def test_malformed_json_rpc_object_fails_closed():
    with pytest.raises(ACPProtocolError, match="Malformed JSON-RPC frame"):
        StubClient._parse_json('{"jsonrpc":')


def test_valid_non_object_json_frame_fails_closed():
    with pytest.raises(ACPProtocolError, match="must be an object"):
        StubClient._parse_json("[]")


def test_malformed_json_array_frame_fails_closed():
    with pytest.raises(ACPProtocolError, match="Malformed JSON-RPC frame"):
        StubClient._parse_json("[")


@pytest.mark.parametrize(
    "payload",
    [
        '{"jsonrpc":"2.0","method":"first","method":"second"}',
        '{"jsonrpc":"2.0","method":"extension/test","params":{"role":"user","role":"admin"}}',
    ],
)
def test_json_rpc_frame_rejects_duplicate_object_keys(payload):
    with pytest.raises(ACPProtocolError, match="Malformed JSON-RPC frame"):
        StubClient._parse_json(payload)


@pytest.mark.parametrize(
    "payload",
    [
        r'{"jsonrpc":"2.0","method":"extension/test","params":{"value":"\ud800"}}',
        r'{"jsonrpc":"2.0","method":"extension/test","params":{"\ud800":"value"}}',
    ],
)
def test_json_rpc_frame_rejects_escaped_invalid_unicode(payload):
    with pytest.raises(ACPProtocolError, match="valid Unicode"):
        StubClient._parse_json(payload)


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_json_rpc_frame_rejects_non_finite_numbers(number):
    payload = f'{{"jsonrpc":"2.0","method":"extension/test","params":{{"value":{number}}}}}'

    with pytest.raises(ACPProtocolError, match=r"Malformed|finite"):
        StubClient._parse_json(payload)


def test_json_rpc_encoder_emits_strict_utf8_and_rejects_invalid_values():
    encoded = base_client_module._encode_json_line({"jsonrpc": "2.0", "method": "test", "params": {"text": "é"}})

    assert encoded.endswith(b"\n")
    assert "é" in encoded.decode("utf-8")
    with pytest.raises(UnicodeEncodeError):
        base_client_module._encode_json_line({"value": "\ud800"})
    with pytest.raises(ValueError, match="Out of range float values"):
        base_client_module._encode_json_line({"value": float("nan")})


def test_response_character_limit_is_enforced(monkeypatch):
    client = StubClient(make_config())
    monkeypatch.setattr(base_client_module, "MAX_RESPONSE_CHARS", 4)

    client._append_chunk("1234")
    with pytest.raises(ACPProtocolError) as exc_info:
        client._append_chunk("5")

    assert exc_info.value.partial_output == "1234"


def test_response_chunk_limit_is_enforced(monkeypatch):
    client = StubClient(make_config())
    monkeypatch.setattr(base_client_module, "MAX_RESPONSE_CHUNKS", 2)

    client._append_chunk("a")
    client._append_chunk("b")
    with pytest.raises(ACPProtocolError, match="chunk client limit") as exc_info:
        client._append_chunk("c")

    assert exc_info.value.partial_output == "ab"


class FakePipe:
    def __init__(self) -> None:
        self.closed = False
        self.writes: list[bytes] = []

    async def readline(self) -> bytes:
        return b""

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class HangingDrainPipe(FakePipe):
    async def drain(self) -> None:
        await asyncio.Event().wait()


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakePipe()
        self.stdout = FakePipe()
        self.stderr = FakePipe()
        self.returncode: int | None = None
        self.pid = 1234
        self.terminate_calls = 0
        self.kill_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode


class OversizedThenValidStderr:
    def __init__(self) -> None:
        self.calls = 0

    async def readline(self) -> bytes:
        self.calls += 1
        if self.calls == 1:
            raise ValueError("stderr line exceeded StreamReader limit")
        if self.calls == 2:
            return b"safe diagnostic\n"
        return b""


class BlockingStderr:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def readline(self) -> bytes:
        self.started.set()
        await asyncio.Event().wait()
        return b""


@pytest.mark.asyncio
async def test_stderr_reader_keeps_draining_after_an_over_limit_line():
    client = StubClient(make_config())
    process = FakeProcess()
    process.stderr = OversizedThenValidStderr()
    client.process = process

    await client._read_stderr()

    assert list(client._stderr_buffer) == [
        "[stderr line exceeded transport limit]",
        "safe diagnostic",
    ]


@pytest.mark.asyncio
async def test_stderr_reader_propagates_its_own_cancellation():
    client = StubClient(make_config())
    process = FakeProcess()
    stderr = BlockingStderr()
    process.stderr = stderr
    client.process = process
    reader = asyncio.create_task(client._read_stderr())
    await stderr.started.wait()

    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader


class StubbornAsyncProcess(FakeProcess):
    def __init__(self) -> None:
        super().__init__()
        self.can_exit = False

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.can_exit:
            self.returncode = 0
            return
        raise PermissionError("terminate denied")

    def kill(self) -> None:
        self.kill_calls += 1
        if self.can_exit:
            self.returncode = -9
            return
        raise PermissionError("kill denied")

    async def wait(self) -> int:
        if self.returncode is None:
            raise PermissionError("wait denied")
        return self.returncode


@pytest.mark.asyncio
async def test_base_ping_never_writes_an_invalid_acp_probe_frame():
    client = RacingClient(make_config())
    process = FakeProcess()
    client.process = process
    client._connected = True

    assert await client.ping() is True
    assert process.stdin.writes == []

    process.stdin.closed = True
    assert await client.ping() is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_factory",
    [
        pytest.param(lambda: RacingClient(make_config()), id="base"),
        pytest.param(lambda: OpenCodeACPClient(make_config()), id="opencode"),
        pytest.param(lambda: CursorCLIClient(make_config()), id="cursor-cli"),
    ],
)
async def test_disconnect_shares_cleanup_and_delays_caller_cancellation(client_factory):
    client = client_factory()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleanup_calls = 0

    async def controlled_cleanup() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        cleanup_started.set()
        await allow_cleanup.wait()

    client._disconnect_locked = controlled_cleanup
    first = asyncio.create_task(client.disconnect())
    await cleanup_started.wait()
    second = asyncio.create_task(client.disconnect())
    first.cancel()
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.sleep(0)

    assert not first.done()
    assert not second.done()
    allow_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second

    assert cleanup_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_factory",
    [
        pytest.param(lambda: RacingClient(make_config(timeout_connect=1)), id="base"),
        pytest.param(lambda: OpenCodeACPClient(make_config(timeout_connect=1)), id="opencode"),
    ],
)
async def test_connect_serializes_concurrent_process_creation(client_factory):
    client = client_factory()
    active = 0
    peak_active = 0

    async def controlled_connect() -> None:
        nonlocal active, peak_active
        active += 1
        peak_active = max(peak_active, active)
        await asyncio.sleep(0.01)
        active -= 1

    client._connect = controlled_connect

    await asyncio.gather(client.connect(), client.connect())

    assert peak_active == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_factory",
    [
        pytest.param(lambda: RacingClient(make_config(timeout_connect=1)), id="base"),
        pytest.param(lambda: OpenCodeACPClient(make_config(timeout_connect=1)), id="opencode"),
    ],
)
async def test_connect_cancellation_after_inner_completion_still_disconnects(client_factory):
    client = client_factory()
    connect_started = asyncio.Event()
    allow_connect = asyncio.Event()
    connect_completed = asyncio.Event()
    disconnect_calls = 0

    async def controlled_connect() -> None:
        connect_started.set()
        await allow_connect.wait()
        connect_completed.set()

    async def controlled_disconnect() -> None:
        nonlocal disconnect_calls
        disconnect_calls += 1

    client._connect = controlled_connect
    client._disconnect_locked = controlled_disconnect

    connect_task = asyncio.create_task(client.connect())
    await connect_started.wait()
    allow_connect.set()
    connect_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await connect_task

    assert connect_completed.is_set()
    assert disconnect_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "client_factory",
    [
        pytest.param(lambda: RacingClient(make_config(timeout_connect=1)), id="base"),
        pytest.param(lambda: OpenCodeACPClient(make_config(timeout_connect=1)), id="opencode"),
    ],
)
async def test_disconnect_waits_for_inflight_connect_before_cleanup(client_factory):
    client = client_factory()
    connect_started = asyncio.Event()
    allow_connect = asyncio.Event()
    events = []

    async def controlled_connect() -> None:
        connect_started.set()
        await allow_connect.wait()
        events.append("connect")

    async def controlled_disconnect() -> None:
        events.append("disconnect")

    client._connect = controlled_connect
    client._disconnect_locked = controlled_disconnect

    connect_task = asyncio.create_task(client.connect())
    await connect_started.wait()
    disconnect_task = asyncio.create_task(client.disconnect())
    await asyncio.sleep(0)

    assert not disconnect_task.done()
    allow_connect.set()
    await asyncio.gather(connect_task, disconnect_task)

    assert events == ["connect", "disconnect"]


@pytest.mark.asyncio
async def test_stale_reader_cleanup_does_not_disconnect_replacement_transport():
    client = RacingClient(make_config())
    stale = FakeProcess()
    replacement = FakeProcess()
    client.process = stale
    disconnect_calls = 0

    async def controlled_disconnect() -> None:
        nonlocal disconnect_calls
        disconnect_calls += 1

    client._disconnect_locked = controlled_disconnect
    client._schedule_process_cleanup()
    client.process = replacement
    assert client._process_cleanup_task is not None
    await client._process_cleanup_task

    assert disconnect_calls == 0
    assert client.process is replacement


class InitializeFailureClient(ACPClientBase):
    def _get_acp_command(self) -> list[str]:
        return ["fake-acp"]

    async def _initialize(self) -> None:
        raise RuntimeError("initialize failed")


class HangingInitializeClient(ACPClientBase):
    def _get_acp_command(self) -> list[str]:
        return ["fake-acp"]

    async def _initialize(self) -> None:
        await asyncio.Event().wait()


class CommandFailureClient(ACPClientBase):
    def __init__(self, config: ACPConfig) -> None:
        super().__init__(config)
        self.host_prepared = False
        self.host_closed = False

    async def _prepare_host_services(self) -> None:
        self.host_prepared = True

    async def _close_host_services(self) -> None:
        self.host_closed = True

    def _get_acp_command(self) -> list[str]:
        raise FileNotFoundError("missing test CLI")

    async def _initialize(self) -> None:
        raise AssertionError("initialize must not run")


@pytest.mark.asyncio
async def test_initialize_failure_cleans_process_and_reader_tasks(monkeypatch, tmp_path):
    process = FakeProcess()
    spawn_kwargs = {}

    async def fake_create_subprocess_exec(*_args, **kwargs):
        spawn_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    client = InitializeFailureClient(make_config(cwd=str(tmp_path)))

    with pytest.raises(RuntimeError, match="initialize failed"):
        await client.connect()

    assert process.terminate_calls == 1
    assert client.process is None
    assert client._reader_tasks == []
    assert spawn_kwargs["cwd"] == str(tmp_path)


@pytest.mark.asyncio
async def test_startup_failure_before_spawn_closes_host_services():
    client = CommandFailureClient(make_config())

    with pytest.raises(FileNotFoundError, match="missing test CLI"):
        await client.connect()

    assert client.host_prepared is True
    assert client.host_closed is True


@pytest.mark.asyncio
async def test_direct_base_connect_obeys_one_end_to_end_timeout(monkeypatch):
    process = FakeProcess()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    client = HangingInitializeClient(make_config(timeout_connect=0.01))

    with pytest.raises(ConnectionError, match=r"ACP startup exceeded 0\.01s"):
        await client.connect()

    assert process.terminate_calls == 1
    assert client.process is None
    assert client._reader_tasks == []


@pytest.mark.asyncio
async def test_base_spawn_timeout_reaps_process_that_arrives_late(monkeypatch):
    process = FakeProcess()
    spawn_started = asyncio.Event()
    release_spawn = asyncio.Event()

    async def delayed_create_subprocess_exec(*_args, **_kwargs):
        spawn_started.set()
        await release_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_create_subprocess_exec)
    client = RacingClient(make_config(timeout_connect=0.01))

    connect = asyncio.create_task(client.connect())
    await spawn_started.wait()
    with pytest.raises(ConnectionError, match=r"ACP startup exceeded 0\.01s"):
        await connect

    cleanup = client._async_spawn_cleanup_task
    assert cleanup is not None
    assert client._async_spawn_task is not None
    release_spawn.set()
    await asyncio.wait_for(asyncio.shield(cleanup), timeout=1)

    assert process.terminate_calls == 1
    assert process.returncode == 0
    assert client.process is None
    assert client._async_spawn_task is None
    assert client._async_spawn_cleanup_task is None


@pytest.mark.asyncio
async def test_base_late_spawn_cleanup_retains_unreaped_process_for_retry(monkeypatch):
    process = StubbornAsyncProcess()
    spawn_started = asyncio.Event()
    release_spawn = asyncio.Event()

    async def delayed_create_subprocess_exec(*_args, **_kwargs):
        spawn_started.set()
        await release_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_create_subprocess_exec)
    client = RacingClient(make_config(timeout_connect=0.01))

    connect = asyncio.create_task(client.connect())
    await spawn_started.wait()
    with pytest.raises(ConnectionError, match=r"ACP startup exceeded 0\.01s"):
        await connect

    cleanup = client._async_spawn_cleanup_task
    assert cleanup is not None
    release_spawn.set()
    with pytest.raises(RuntimeError, match="did not exit"):
        await asyncio.wait_for(asyncio.shield(cleanup), timeout=1)

    assert client.process is process
    assert client._async_spawn_task is None
    assert client._async_spawn_cleanup_task is None

    process.can_exit = True
    await client.disconnect()
    assert client.process is None


class RacingClient(ACPClientBase):
    def _get_acp_command(self) -> list[str]:
        return ["fake-acp"]

    async def _initialize(self) -> None:
        return None


@pytest.mark.asyncio
async def test_rpc_wait_fails_promptly_when_process_exits_but_stdout_remains_open(monkeypatch):
    monkeypatch.setattr(base_client_module, "RPC_PROCESS_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(base_client_module, "RPC_EXIT_DRAIN_TIMEOUT", 0.01)
    client = RacingClient(make_config())
    process = FakeProcess()
    process.returncode = 1
    client.process = process
    client._connected = True
    future = asyncio.get_running_loop().create_future()

    with pytest.raises(ConnectionError, match="exited before completing"):
        await client._await_rpc_future(future, asyncio.get_running_loop().time() + 1)


@pytest.mark.asyncio
async def test_rpc_wait_accepts_buffered_tail_immediately_after_process_exit(monkeypatch):
    monkeypatch.setattr(base_client_module, "RPC_PROCESS_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(base_client_module, "RPC_EXIT_DRAIN_TIMEOUT", 0.1)
    client = RacingClient(make_config())
    process = FakeProcess()
    process.returncode = 1
    client.process = process
    client._connected = True
    future = asyncio.get_running_loop().create_future()
    waiting = asyncio.create_task(client._await_rpc_future(future, asyncio.get_running_loop().time() + 1))
    await asyncio.sleep(0.01)
    future.set_result({"stopReason": "end_turn"})

    assert await waiting == {"stopReason": "end_turn"}


class LocalProcessClient(ACPClientBase):
    def _get_acp_command(self) -> list[str]:
        return [sys.executable, "-u", "-c", "import time; time.sleep(60)"]

    async def _initialize(self) -> None:
        self.session_id = "local-session"


class RacingStdin(FakePipe):
    def __init__(self, client: RacingClient) -> None:
        super().__init__()
        self.client = client
        self.message: dict | None = None

    def write(self, data: bytes) -> None:
        self.message = json.loads(data.decode())

    async def drain(self) -> None:
        assert self.message is not None
        await self.client._dispatch({"jsonrpc": "2.0", "id": self.message["id"], "result": {"ok": True}})


class ResumeReplayStdin(FakePipe):
    def __init__(self, client: RacingClient, *, fail: bool = False) -> None:
        super().__init__()
        self.client = client
        self.fail = fail
        self.message: dict | None = None

    def write(self, data: bytes) -> None:
        self.message = json.loads(data.decode())

    async def drain(self) -> None:
        assert self.message is not None
        target = self.message["params"]["sessionId"]
        await self.client._dispatch(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "sessionId": target,
                    "update": {
                        "sessionUpdate": "config_option_update",
                        "configOptions": [
                            {
                                "id": "mode",
                                "name": "Mode",
                                "type": "select",
                                "currentValue": "code",
                                "options": [{"value": "code", "name": "Code"}],
                            }
                        ],
                    },
                },
            }
        )
        if self.fail:
            response = {
                "jsonrpc": "2.0",
                "id": self.message["id"],
                "error": {"code": -32000, "message": "resume failed"},
            }
        else:
            response = {"jsonrpc": "2.0", "id": self.message["id"], "result": {}}
        await self.client._dispatch(response)


class FailingPromptStdin(FakePipe):
    def write(self, _data: bytes) -> None:
        return None

    async def drain(self) -> None:
        raise ConnectionError("pipe lost after write")


@pytest.mark.asyncio
async def test_rpc_registers_pending_before_fast_response():
    client = RacingClient(make_config())
    process = FakeProcess()
    process.stdin = RacingStdin(client)
    client.process = process
    client._connected = True

    result = await client._rpc("fast", {}, timeout=0.1)

    assert result == {"ok": True}
    assert client._pending == {}
    assert client._inflight == {}


@pytest.mark.asyncio
async def test_malformed_session_transition_response_restores_state_and_retires_transport():
    client = RacingClient(make_config())
    process = FakeProcess()
    process.stdin = RacingStdin(client)
    client.process = process
    client._connected = True
    client.session_id = "session-old"
    client.session_info = {"title": "Old"}

    with pytest.raises(ACPProtocolError, match="sessionId"):
        await client._rpc("session/new", {"cwd": "C:/workspace"}, timeout=0.1)

    assert client.session_id == "session-old"
    assert client.session_info == {"title": "Old"}
    assert client.is_connected is False
    assert client.is_pool_reusable is False
    assert process.terminate_calls == 1


@pytest.mark.asyncio
async def test_rpc_timeout_includes_blocked_pipe_drain():
    client = RacingClient(make_config(timeout_connect=0.01))
    process = FakeProcess()
    process.stdin = HangingDrainPipe()
    client.process = process
    client._connected = True

    started = asyncio.get_running_loop().time()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(client._rpc("blocked", {}, timeout=0.02), timeout=1)

    assert asyncio.get_running_loop().time() - started < 0.5
    assert client._pending == {}
    assert client._inflight == {}
    assert client.is_connected is False
    assert client.is_pool_reusable is False
    assert process.terminate_calls == 1


@pytest.mark.asyncio
async def test_direct_cancel_is_bounded_when_pipe_drain_blocks():
    client = RacingClient(make_config(timeout_connect=0.02))
    process = FakeProcess()
    process.stdin = HangingDrainPipe()
    client.process = process
    client._connected = True
    client.session_id = "session-test"

    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(client.cancel(), timeout=1)

    assert asyncio.get_running_loop().time() - started < 0.5


@pytest.mark.asyncio
async def test_base_notification_write_is_bounded_when_pipe_drain_blocks():
    client = RacingClient(make_config(timeout_connect=0.02))
    process = FakeProcess()
    process.stdin = HangingDrainPipe()
    client.process = process
    client._connected = True

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(client._send_notification("initialized"), timeout=1)


@pytest.mark.asyncio
async def test_base_host_replies_are_bounded_when_pipe_drain_blocks():
    client = RacingClient(make_config(timeout_connect=0.02))
    process = FakeProcess()
    process.stdin = HangingDrainPipe()
    client.process = process
    client._connected = True

    for sender in (
        client._send_result(1, {"ok": True}),
        client._send_error(2, -32603, "failed"),
    ):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(sender, timeout=1)


@pytest.mark.asyncio
async def test_failed_host_request_task_retires_transport():
    client = RacingClient(make_config(timeout_connect=0.02))
    process = FakeProcess()
    client.process = process
    client._connected = True

    async def fail_reply() -> None:
        raise asyncio.TimeoutError

    task = asyncio.create_task(fail_reply())
    await asyncio.sleep(0)
    client._request_tasks.add(task)
    client._request_tasks_by_id[1] = task
    client._request_done(1, task)

    cleanup = client._process_cleanup_task
    assert cleanup is not None
    await asyncio.wait_for(cleanup, timeout=1)

    assert process.terminate_calls == 1
    assert client.process is None


@pytest.mark.asyncio
async def test_non_stream_prompt_deadline_includes_blocked_pipe_drain():
    client = RacingClient(make_config(timeout_connect=0.01, timeout_prompt=0.02))
    process = FakeProcess()
    process.stdin = HangingDrainPipe()
    client.process = process
    client._connected = True
    client.session_id = "session-test"

    with pytest.raises(ACPPromptTimeoutError):
        await asyncio.wait_for(client.query("hello"), timeout=1)

    assert process.terminate_calls == 1
    assert client.process is None


@pytest.mark.asyncio
async def test_resume_accepts_pre_response_replay_and_switches_session_atomically():
    client = RacingClient(make_config())
    process = FakeProcess()
    process.stdin = ResumeReplayStdin(client)
    client.process = process
    client._connected = True
    client.session_id = "session-old"
    client.session_config_options = [{"id": "old"}]

    result = await client._rpc(
        "session/load",
        {"sessionId": "session-target", "cwd": "C:/workspace", "mcpServers": []},
        timeout=0.1,
    )

    assert result == {}
    assert client.session_id == "session-target"
    assert client.session_config_options[0]["currentValue"] == "code"
    assert client._session_transition_id is None


@pytest.mark.asyncio
async def test_failed_resume_restores_previous_session_config():
    client = RacingClient(make_config())
    process = FakeProcess()
    process.stdin = ResumeReplayStdin(client, fail=True)
    client.process = process
    client._connected = True
    client.session_id = "session-old"
    previous = [{"id": "old", "name": "Old", "type": "select", "currentValue": "old"}]
    client.session_config_options = previous
    previous_modes = {
        "currentModeId": "ask",
        "availableModes": [{"id": "ask", "name": "Ask"}],
    }
    client.session_mode_state = previous_modes
    previous_info = {"title": "Old session"}
    previous_usage = {"used": 12, "size": 100}
    previous_commands = [{"name": "old", "description": "Old command"}]
    client.session_info = previous_info
    client.session_usage = previous_usage
    client.available_commands = previous_commands

    with pytest.raises(RPCError) as exc_info:
        await client._rpc(
            "session/resume",
            {"sessionId": "session-target", "cwd": "C:/workspace"},
            timeout=0.1,
        )

    assert exc_info.value.message == "resume failed"
    assert "resume failed" not in str(exc_info.value)
    assert client.session_id == "session-old"
    assert client.session_config_options == previous
    assert client.session_mode_state == previous_modes
    assert client.session_info == previous_info
    assert client.session_usage == previous_usage
    assert client.available_commands == previous_commands
    assert client._session_transition_id is None
    assert client._session_transition_active is False


def test_otel_span_setup_failure_is_a_noop(monkeypatch):
    trace = pytest.importorskip("opentelemetry.trace")
    client = RacingClient(make_config())

    def fail_get_tracer(_name: str):
        raise RuntimeError("broken tracer provider")

    monkeypatch.setattr(trace, "get_tracer", fail_get_tracer)
    with client._otel_rpc_span("initialize", 1) as span:
        assert span is None


def test_otel_context_manager_failure_is_a_noop(monkeypatch):
    trace = pytest.importorskip("opentelemetry.trace")
    client = RacingClient(make_config())

    class BrokenSpanContext:
        def __enter__(self):
            raise RuntimeError("broken span context")

        def __exit__(self, *_args):
            raise RuntimeError("broken span cleanup")

    class Tracer:
        def start_as_current_span(self, *_args, **_kwargs):
            return BrokenSpanContext()

    monkeypatch.setattr(trace, "get_tracer", lambda _name: Tracer())
    with client._otel_rpc_span("initialize", 1) as span:
        assert span is None


def test_otel_context_manager_cannot_suppress_business_exception(monkeypatch):
    trace = pytest.importorskip("opentelemetry.trace")
    client = RacingClient(make_config())

    class SuppressingSpanContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return True

    class Tracer:
        def start_as_current_span(self, *_args, **_kwargs):
            return SuppressingSpanContext()

    monkeypatch.setattr(trace, "get_tracer", lambda _name: Tracer())
    with pytest.raises(RuntimeError, match="business failure"), client._otel_rpc_span("initialize", 1):
        raise RuntimeError("business failure")


def test_otel_rpc_span_collapses_custom_provider_attribute(monkeypatch):
    trace = pytest.importorskip("opentelemetry.trace")
    captured: dict[str, object] = {}
    client = RacingClient(make_config(provider="tenant/private-user"))

    class SpanContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    class Tracer:
        def start_as_current_span(self, name, *, attributes):
            captured["name"] = name
            captured["attributes"] = attributes
            return SpanContext()

    monkeypatch.setattr(trace, "get_tracer", lambda _name: Tracer())

    with client._otel_rpc_span("initialize", 1):
        pass

    assert captured == {
        "name": "acp.rpc initialize",
        "attributes": {"acp.method": "initialize", "acp.msg_id": 1, "acp.provider": "custom"},
    }


def test_otel_subprocess_propagation_excludes_baggage_and_invalid_values(monkeypatch):
    trace = pytest.importorskip("opentelemetry.trace")
    propagate = pytest.importorskip("opentelemetry.propagate")

    class SpanContext:
        trace_id = 1

    class Span:
        @staticmethod
        def get_span_context():
            return SpanContext()

    def inject(carrier):
        carrier.update(
            {
                "traceparent": "00-00000000000000000000000000000001-0000000000000001-01",
                "tracestate": "vendor=value",
                "baggage": "user.id=private-user",
                "x-custom-trace": "private-custom-value",
                "TRACEPARENT": "bad\x00value",
            }
        )

    monkeypatch.setattr(trace, "get_current_span", lambda: Span())
    monkeypatch.setattr(propagate, "inject", inject)
    environment: dict[str, str] = {}

    base_client_module._inject_otel_context_into_env(environment)

    assert environment == {
        "TRACEPARENT": "00-00000000000000000000000000000001-0000000000000001-01",
        "TRACESTATE": "vendor=value",
    }


@pytest.mark.asyncio
async def test_otel_span_attribute_failure_cannot_mask_rpc_timeout():
    client = RacingClient(make_config())
    process = FakeProcess()
    client.process = process
    client._connected = True

    class BrokenSpan:
        def set_attribute(self, *_args):
            raise RuntimeError("metrics backend failed")

    class SpanContext:
        def __enter__(self):
            return BrokenSpan()

        def __exit__(self, *_args):
            return False

    client._otel_rpc_span = lambda *_args: SpanContext()

    with pytest.raises(asyncio.TimeoutError):
        await client._rpc("initialize", {}, timeout=0.001)

    assert client._pending == {}
    assert client._inflight == {}


@pytest.mark.asyncio
async def test_rpc_timeout_sends_standard_cancel_request_notification():
    client = RacingClient(make_config())
    process = FakeProcess()
    client.process = process
    client._connected = True

    with pytest.raises(asyncio.TimeoutError):
        await client._rpc("session/list", {}, timeout=0.001)

    frames = [json.loads(frame.decode()) for frame in process.stdin.writes]
    assert frames[0]["method"] == "session/list"
    assert frames[1] == {
        "jsonrpc": "2.0",
        "method": "$/cancel_request",
        "params": {"requestId": frames[0]["id"]},
    }
    assert client.is_connected is False
    assert client.is_pool_reusable is False
    assert process.terminate_calls == 1


@pytest.mark.asyncio
async def test_rpc_future_finalizer_retrieves_disconnect_exception():
    loop = asyncio.get_running_loop()
    unhandled_contexts = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled_contexts.append(context))
    try:
        future = loop.create_future()
        future.set_exception(ConnectionError("ACP disconnected"))
        base_client_module._finalize_rpc_future(future)
        del future
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert unhandled_contexts == []


@pytest.mark.asyncio
async def test_rpc_caller_cancellation_sends_standard_cancel_request_notification():
    client = RacingClient(make_config())
    process = FakeProcess()
    client.process = process
    client._connected = True

    request = asyncio.create_task(client._rpc("session/list", {}, timeout=10))
    while not process.stdin.writes:
        await asyncio.sleep(0)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    frames = [json.loads(frame.decode()) for frame in process.stdin.writes]
    assert frames[-1] == {
        "jsonrpc": "2.0",
        "method": "$/cancel_request",
        "params": {"requestId": frames[0]["id"]},
    }
    assert client.is_connected is False
    assert client.is_pool_reusable is False
    assert process.terminate_calls == 1


@pytest.mark.asyncio
async def test_response_id_type_cannot_alias_pending_integer_id():
    client = RacingClient(make_config())
    future = asyncio.get_running_loop().create_future()
    client._pending[1] = future
    client._inflight[1] = ("initialize", 0.0)

    with pytest.raises(ACPProtocolError, match="response id"):
        await client._dispatch({"jsonrpc": "2.0", "id": True, "result": {"ok": True}})

    assert not future.done()
    future.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("response_id", [-(2**63) - 1, 2**63])
async def test_response_id_must_fit_acp_int64(response_id):
    client = RacingClient(make_config())

    with pytest.raises(ACPProtocolError, match="int64"):
        await client._dispatch({"jsonrpc": "2.0", "id": response_id, "result": {}})


@pytest.mark.asyncio
async def test_response_requires_exactly_one_result_or_error():
    client = RacingClient(make_config())

    with pytest.raises(ACPProtocolError, match="exactly one"):
        await client._dispatch({"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}})
    with pytest.raises(ACPProtocolError, match="exactly one"):
        await client._dispatch({"jsonrpc": "2.0", "id": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "match"),
    [
        (None, "error object"),
        ({"code": True, "message": "bad"}, "code"),
        ({"code": "-32603", "message": "bad"}, "code"),
        ({"code": -32603}, "message"),
        ({"code": -32603, "message": 42}, "message"),
    ],
)
async def test_rpc_error_fields_are_strictly_validated(error, match):
    client = RacingClient(make_config())

    with pytest.raises(ACPProtocolError, match=match):
        await client._dispatch({"jsonrpc": "2.0", "id": 99, "error": error})


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [-(2**31) - 1, 2**31])
async def test_rpc_error_code_must_fit_acp_int32(code):
    client = RacingClient(make_config())

    with pytest.raises(ACPProtocolError, match="int32"):
        await client._dispatch({"jsonrpc": "2.0", "id": 99, "error": {"code": code, "message": "bad"}})


@pytest.mark.asyncio
async def test_prompt_response_text_is_recovered_for_non_stream_query():
    client = RacingClient(make_config(timeout_prompt=1))
    process = FakeProcess()
    process.stdin = RacingStdin(client)
    client.process = process
    client._connected = True
    client.session_id = "session-test"

    async def dispatch_prompt_response(message: dict) -> None:
        await client._dispatch(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"stopReason": "end_turn", "text": "final text"},
            }
        )

    async def drain() -> None:
        assert process.stdin.message is not None
        await dispatch_prompt_response(process.stdin.message)

    process.stdin.drain = drain
    assert await client.query("hello") == "final text"


@pytest.mark.asyncio
async def test_prompt_result_text_cannot_disagree_with_streamed_text():
    client = RacingClient(make_config())
    future = asyncio.get_running_loop().create_future()
    client._chunks = ["streamed"]
    client._chunk_chars = len("streamed")
    client._pending[1] = future
    client._inflight[1] = ("session/prompt", 0.0)

    await client._dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"stopReason": "end_turn", "text": "different"},
        }
    )

    with pytest.raises(ACPProtocolError) as exc_info:
        await future
    assert exc_info.value.partial_output == "streamed"


@pytest.mark.asyncio
async def test_non_prompt_response_stop_reason_does_not_mutate_prompt_chunks():
    client = RacingClient(make_config())
    client._chunks = ["existing"]
    client._chunk_chars = len("existing")
    future = asyncio.get_running_loop().create_future()
    client._pending[1] = future
    client._inflight[1] = ("extension/status", 0.0)

    result = {"stopReason": "extension_complete", "text": "extension text"}
    await client._dispatch({"jsonrpc": "2.0", "id": 1, "result": result})

    assert await future == result
    assert client._chunks == ["existing"]
    assert client._chunk_chars == len("existing")


@pytest.mark.asyncio
async def test_prompt_transport_failure_after_write_has_unknown_outcome():
    client = RacingClient(make_config())
    process = FakeProcess()
    process.stdin = FailingPromptStdin()
    client.process = process
    client._connected = True

    with pytest.raises(ACPRequestOutcomeUnknownError) as exc_info:
        await client._rpc("session/prompt", {}, timeout=0.1)

    assert isinstance(exc_info.value.cause, ConnectionError)
    assert exc_info.value.request_may_have_started is True
    assert client._pending == {}
    assert client._inflight == {}


@pytest.mark.asyncio
async def test_non_stream_unknown_prompt_outcome_retires_direct_transport():
    client = StubClient(make_config())
    disconnect_calls = 0

    async def fail_prompt(*_args, **_kwargs):
        raise ACPRequestOutcomeUnknownError(ConnectionError("pipe failed"))

    async def disconnect():
        nonlocal disconnect_calls
        disconnect_calls += 1
        client._connected = False

    client._rpc = fail_prompt
    client.disconnect = disconnect

    with pytest.raises(ACPRequestOutcomeUnknownError):
        await client.query("hello")

    assert disconnect_calls == 1
    assert client._connected is False


@pytest.mark.asyncio
async def test_opencode_dispatches_buffered_response_before_reader_eof():
    client = OpenCodeACPClient(make_config())
    client._connected = True
    client._dispatch_queue = asyncio.Queue()
    future = asyncio.get_running_loop().create_future()
    client._pending[7] = future
    client._inflight[7] = ("session/prompt", 0.0)

    dispatch_task = asyncio.create_task(client._dispatch_messages())
    client._dispatch_queue.put_nowait({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}})
    client._dispatch_queue.put_nowait(None)

    assert await future == {"ok": True}
    await dispatch_task
    assert client._connected is False
    assert client._pending == {}
    if client._process_cleanup_task is not None:
        await client._process_cleanup_task


@pytest.mark.asyncio
async def test_opencode_eof_does_not_overwrite_already_dispatched_prompt_result():
    client = OpenCodeACPClient(make_config())
    client._connected = True
    client._streaming = True
    client._stream_queue = asyncio.Queue()
    future = asyncio.get_running_loop().create_future()
    client._pending[8] = future
    client._inflight[8] = ("session/prompt", 0.0)

    await client._dispatch({"jsonrpc": "2.0", "id": 8, "result": {"stopReason": "end_turn"}})
    client._handle_reader_disconnect()

    assert await future == {"stopReason": "end_turn"}
    assert client._stream_error is None
    assert client._stream_queue.empty()
    if client._process_cleanup_task is not None:
        await client._process_cleanup_task


@pytest.mark.asyncio
async def test_opencode_reader_protocol_failure_reaches_pending_rpc():
    client = OpenCodeACPClient(make_config())
    client._connected = True
    client._dispatch_queue = asyncio.Queue()
    future = asyncio.get_running_loop().create_future()
    failure = ACPProtocolError("oversized frame")
    client._pending[9] = future
    client._inflight[9] = ("initialize", 0.0)

    dispatch_task = asyncio.create_task(client._dispatch_messages())
    client._dispatch_queue.put_nowait(failure)
    client._dispatch_queue.put_nowait(None)

    with pytest.raises(ACPProtocolError) as exc_info:
        await future
    assert exc_info.value is failure
    await dispatch_task
    if client._process_cleanup_task is not None:
        await client._process_cleanup_task


def test_opencode_reader_rejects_invalid_utf8_stdout():
    client = OpenCodeACPClient(make_config())

    class RawPopen:
        stdout = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"result":{"text":"\xff"}}\n')

    client._popen = RawPopen()
    messages: list[dict | BaseException | None] = []
    client._schedule_dispatch = messages.append

    client._sync_read_loop()

    assert isinstance(messages[0], ACPProtocolError)
    assert "valid UTF-8" in str(messages[0])
    assert messages[1] is None


def test_opencode_reader_does_not_drop_empty_json_object():
    client = OpenCodeACPClient(make_config())

    class RawPopen:
        stdout = io.BytesIO(b"{}\n")

    client._popen = RawPopen()
    messages: list[dict | BaseException | None] = []
    client._schedule_dispatch = messages.append

    client._sync_read_loop()

    assert messages == [{}, None]


@pytest.mark.asyncio
async def test_opencode_async_writes_run_outside_event_loop_thread():
    client = OpenCodeACPClient(make_config())
    client._connected = True
    client._popen = WriteOnlyPopen()
    loop_thread = threading.get_ident()
    write_threads: list[int] = []
    client._sync_write = lambda _data: write_threads.append(threading.get_ident())

    await client._send_notification("session/cancel", {"sessionId": "session"})

    assert write_threads and write_threads[0] != loop_thread


@pytest.mark.asyncio
async def test_opencode_host_reply_write_is_bounded():
    client = OpenCodeACPClient(make_config(timeout_connect=0.02))
    client._connected = True
    client._popen = WriteOnlyPopen()
    write_started = threading.Event()
    release_write = threading.Event()

    def blocked_write(_data: bytes) -> None:
        write_started.set()
        assert release_write.wait(timeout=1)

    client._sync_write = blocked_write
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client._send_result(1, {"ok": True}), timeout=1)
        assert write_started.is_set()
        assert len(client._write_tasks) == 1
    finally:
        release_write.set()

    for _ in range(100):
        if not client._write_tasks:
            break
        await asyncio.sleep(0.01)
    assert client._write_tasks == set()


@pytest.mark.asyncio
async def test_opencode_notification_write_is_bounded():
    client = OpenCodeACPClient(make_config(timeout_connect=0.02))
    client._connected = True
    client._popen = WriteOnlyPopen()
    write_started = threading.Event()
    release_write = threading.Event()

    def blocked_write(_data: bytes) -> None:
        write_started.set()
        assert release_write.wait(timeout=1)

    client._sync_write = blocked_write
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client._send_notification("initialized"), timeout=1)
        assert write_started.is_set()
        assert len(client._write_tasks) == 1
    finally:
        release_write.set()

    for _ in range(100):
        if not client._write_tasks:
            break
        await asyncio.sleep(0.01)
    assert client._write_tasks == set()


@pytest.mark.asyncio
async def test_opencode_disconnect_waits_for_timed_out_write_worker():
    client = OpenCodeACPClient(make_config(timeout_connect=0.02))
    client._connected = True
    process = WriteOnlyPopen()
    client._popen = process
    write_started = threading.Event()
    release_write = threading.Event()
    terminate_started = threading.Event()

    def blocked_write(_data: bytes) -> None:
        write_started.set()
        assert release_write.wait(timeout=1)

    terminate = process.terminate

    def tracked_terminate() -> None:
        terminate_started.set()
        terminate()

    client._sync_write = blocked_write
    process.terminate = tracked_terminate

    with pytest.raises(asyncio.TimeoutError):
        await client._send_notification("initialized")
    assert write_started.is_set()
    assert len(client._write_tasks) == 1

    disconnect = asyncio.create_task(client.disconnect())
    assert await asyncio.to_thread(terminate_started.wait, 1)
    assert disconnect.done() is False
    release_write.set()
    await asyncio.wait_for(disconnect, timeout=1)

    assert client._write_tasks == set()
    assert client._popen is None


@pytest.mark.asyncio
async def test_opencode_rpc_timeout_sends_standard_cancel_request_notification():
    client = OpenCodeACPClient(make_config())
    client._connected = True
    client._popen = WriteOnlyPopen()
    writes: list[bytes] = []
    client._sync_write = writes.append

    with pytest.raises(asyncio.TimeoutError):
        await client._rpc("session/list", {}, timeout=0.01)

    frames = [json.loads(frame.decode()) for frame in writes]
    assert frames[0]["method"] == "session/list"
    assert frames[1] == {
        "jsonrpc": "2.0",
        "method": "$/cancel_request",
        "params": {"requestId": frames[0]["id"]},
    }
    assert client.is_connected is False
    assert client.is_pool_reusable is False
    assert client._popen is None


@pytest.mark.asyncio
async def test_opencode_rpc_caller_cancellation_remains_cancellation():
    client = OpenCodeACPClient(make_config())
    client._connected = True
    client._popen = WriteOnlyPopen()
    writes: list[bytes] = []
    client._sync_write = writes.append

    request = asyncio.create_task(client._rpc("session/list", {}, timeout=10))
    while not writes:
        await asyncio.sleep(0)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    frames = [json.loads(frame.decode()) for frame in writes]
    assert frames[-1] == {
        "jsonrpc": "2.0",
        "method": "$/cancel_request",
        "params": {"requestId": frames[0]["id"]},
    }
    assert client.is_connected is False
    assert client.is_pool_reusable is False
    assert client._popen is None


class PartialWriteStdin:
    def fileno(self) -> int:
        return 7

    def close(self) -> None:
        return None


class WriteOnlyPopen:
    def __init__(self) -> None:
        self.stdin = PartialWriteStdin()
        self.stdout = None
        self.stderr = None
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode


class ExitedBufferedPopen:
    def __init__(self) -> None:
        frame = {"jsonrpc": "2.0", "id": 17, "result": {"ok": True}}
        self.stdout = io.BytesIO(json.dumps(frame).encode() + b"\n")
        self.stderr = io.BytesIO()
        self.stdin = PartialWriteStdin()

    def poll(self):
        return 0


class DelayedSpawnPopen:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.returncode = None
        self.terminated = threading.Event()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.terminated.set()

    def kill(self):
        self.returncode = -9
        self.terminated.set()

    def wait(self, timeout=None):
        if not self.terminated.wait(timeout):
            raise opencode_module.subprocess.TimeoutExpired("opencode", timeout)
        return self.returncode


class StubbornPopen:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        self.returncode = None
        self.pid = 4321
        self.can_exit = False

    def poll(self):
        return self.returncode

    def terminate(self):
        if self.can_exit:
            self.returncode = 0
            return
        raise PermissionError("terminate denied")

    def kill(self):
        if self.can_exit:
            self.returncode = -9
            return
        raise PermissionError("kill denied")

    def wait(self, timeout=None):
        if self.returncode is None:
            raise PermissionError("wait denied")
        return self.returncode


class StubbornReaderThread:
    def __init__(self) -> None:
        self.can_exit = False
        self.join_calls = 0

    def is_alive(self) -> bool:
        return not self.can_exit

    def join(self, _timeout=None) -> None:
        self.join_calls += 1


@pytest.mark.asyncio
async def test_opencode_disconnect_retains_unreaped_process_for_retry():
    client = OpenCodeACPClient(make_config())
    process = StubbornPopen()
    client._popen = process
    client._connected = True

    with pytest.raises(RuntimeError, match="did not exit"):
        await client.disconnect()

    assert client._popen is process
    process.can_exit = True
    await client.disconnect()
    assert client._popen is None


@pytest.mark.asyncio
async def test_opencode_disconnect_reaps_parent_when_host_cleanup_fails(monkeypatch):
    client = OpenCodeACPClient(make_config())
    await client._prepare_host_services()
    process = DelayedSpawnPopen()
    client._popen = process
    terminal_service = client._terminal_service

    async def failed_host_cleanup():
        raise RuntimeError("host cleanup failed")

    monkeypatch.setattr(client, "_close_host_services", failed_host_cleanup)

    with pytest.raises(RuntimeError, match="host cleanup failed"):
        await client.disconnect()

    assert process.poll() == 0
    assert client._popen is None

    with pytest.raises(RuntimeError, match="host cleanup failed"):
        await client._prepare_host_services()
    assert client._terminal_service is terminal_service


@pytest.mark.asyncio
async def test_opencode_disconnect_retains_live_reader_thread_for_retry():
    client = OpenCodeACPClient(make_config())
    thread = StubbornReaderThread()
    client._reader_thread = thread

    with pytest.raises(RuntimeError, match="reader thread did not exit"):
        await client.disconnect()

    assert client._reader_thread is thread
    assert thread.join_calls == 1

    thread.can_exit = True
    await client.disconnect()

    assert client._reader_thread is None
    assert thread.join_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    ["[]", '{"value": NaN}', '{"value": "\\ud800"}', "{not-json}"],
)
async def test_opencode_rejects_invalid_inherited_config_before_spawn(monkeypatch, content):
    monkeypatch.setattr(opencode_module, "find_opencode_binary", lambda: "opencode")
    client = OpenCodeACPClient(
        make_config(
            provider="opencode",
            extra_env={"OPENCODE_CONFIG_CONTENT": content},
        )
    )

    with pytest.raises(ValueError, match="strict JSON object"):
        await client._start_and_initialize()

    assert client._spawn_task is None
    assert client._popen is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "existing_config", "expected_model"),
    [
        ("opencode-acp/default", {"theme": "dark"}, None),
        ("opencode-acp/default", {"theme": "dark", "model": "provider/existing"}, "provider/existing"),
        (
            "opencode-acp/provider/explicit",
            {"theme": "dark", "model": "provider/existing"},
            "provider/explicit",
        ),
    ],
)
async def test_opencode_default_model_preserves_provider_config(
    monkeypatch,
    model,
    existing_config,
    expected_model,
):
    monkeypatch.setattr(opencode_module, "find_opencode_binary", lambda: "opencode")
    captured: dict[str, object] = {}

    class StopBeforeSpawn(RuntimeError):
        pass

    async def capture_spawn(cmd, env, kwargs):
        captured.update(cmd=cmd, env=env, kwargs=kwargs)
        raise StopBeforeSpawn

    client = OpenCodeACPClient(
        make_config(
            provider="opencode",
            model=model,
            inherit_parent_env=False,
            extra_env={"OPENCODE_CONFIG_CONTENT": json.dumps(existing_config)},
        )
    )
    client._spawn_popen = capture_spawn

    with pytest.raises(StopBeforeSpawn):
        await client._start_and_initialize()

    environment = captured["env"]
    assert isinstance(environment, dict)
    child_config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
    assert child_config["theme"] == "dark"
    if expected_model is None:
        assert "model" not in child_config
    else:
        assert child_config["model"] == expected_model


@pytest.mark.skipif(sys.platform != "win32", reason="Windows environment names are case-insensitive")
@pytest.mark.asyncio
async def test_opencode_reads_case_insensitive_explicit_config_before_spawn(monkeypatch):
    monkeypatch.setattr(opencode_module, "find_opencode_binary", lambda: "opencode")
    client = OpenCodeACPClient(
        make_config(
            provider="opencode",
            extra_env={"opencode_config_content": "[]"},
        )
    )

    with pytest.raises(ValueError, match="strict JSON object"):
        await client._start_and_initialize()

    assert client._spawn_task is None
    assert client._popen is None


@pytest.mark.asyncio
async def test_opencode_spawn_timeout_reaps_process_that_arrives_late(monkeypatch):
    process = DelayedSpawnPopen()
    spawn_started = threading.Event()
    release_spawn = threading.Event()

    def delayed_popen(*_args, **_kwargs):
        spawn_started.set()
        release_spawn.wait(timeout=1)
        return process

    monkeypatch.setattr(opencode_module, "find_opencode_binary", lambda: "opencode")
    monkeypatch.setattr(opencode_module.subprocess, "Popen", delayed_popen)
    client = OpenCodeACPClient(make_config(provider="opencode", timeout_connect=0.02))
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    terminate_popen = client._terminate_popen_sync

    def delayed_cleanup(proc):
        cleanup_started.set()
        release_cleanup.wait(timeout=1)
        terminate_popen(proc)

    monkeypatch.setattr(client, "_terminate_popen_sync", delayed_cleanup)
    loop = asyncio.get_running_loop()
    started_at = loop.time()

    with pytest.raises(ConnectionError, match=r"OpenCode startup exceeded 0\.02s"):
        await client.connect()

    assert loop.time() - started_at < 0.5
    assert spawn_started.is_set()
    release_spawn.set()
    for _ in range(100):
        if cleanup_started.is_set():
            break
        await asyncio.sleep(0.01)

    assert cleanup_started.is_set()
    assert client._spawn_cleanup_task is not None
    release_cleanup.set()
    for _ in range(100):
        if client._spawn_task is None and client._spawn_cleanup_task is None:
            break
        await asyncio.sleep(0.01)

    assert process.terminated.is_set()
    assert client._spawn_task is None
    assert client._spawn_cleanup_task is None
    assert client._popen is None


@pytest.mark.asyncio
async def test_opencode_late_spawn_cleanup_retains_unreaped_process_for_retry(monkeypatch):
    process = StubbornPopen()
    spawn_started = threading.Event()
    release_spawn = threading.Event()

    def delayed_popen(*_args, **_kwargs):
        spawn_started.set()
        release_spawn.wait(timeout=1)
        return process

    monkeypatch.setattr(opencode_module, "find_opencode_binary", lambda: "opencode")
    monkeypatch.setattr(opencode_module.subprocess, "Popen", delayed_popen)
    client = OpenCodeACPClient(make_config(provider="opencode", timeout_connect=0.02))

    with pytest.raises(ConnectionError, match=r"OpenCode startup exceeded 0\.02s"):
        await client.connect()

    release_spawn.set()
    for _ in range(100):
        if client._spawn_task is None and client._spawn_cleanup_task is None and client._popen is process:
            break
        await asyncio.sleep(0.01)

    assert client._spawn_task is None
    assert client._spawn_cleanup_task is None
    assert client._popen is process

    process.can_exit = True
    await client.disconnect()
    assert client._popen is None


@pytest.mark.asyncio
async def test_opencode_ping_never_writes_an_invalid_acp_probe_frame():
    client = OpenCodeACPClient(make_config())
    client._connected = True
    client._popen = WriteOnlyPopen()
    client._sync_write = lambda _data: pytest.fail("ping must not write to ACP stdin")

    assert await client.ping() is True

    client._connected = False
    assert await client.ping() is False


@pytest.mark.asyncio
async def test_opencode_rpc_uses_shared_metadata_and_transition_lifecycle():
    client = OpenCodeACPClient(make_config())
    client._connected = True
    client._popen = WriteOnlyPopen()
    loop = asyncio.get_running_loop()

    def respond_from_writer(data: bytes) -> None:
        request = json.loads(data)

        async def respond() -> None:
            method = request["method"]
            if method == "initialize":
                result = {
                    "protocolVersion": 1,
                    "agentCapabilities": {"sessionCapabilities": {"list": {}}},
                    "agentInfo": {"name": "opencode", "version": "test"},
                }
            else:
                target = request["params"]["sessionId"]
                await client._dispatch(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": target,
                            "update": {
                                "sessionUpdate": "config_option_update",
                                "configOptions": [
                                    {
                                        "id": "mode",
                                        "name": "Mode",
                                        "type": "select",
                                        "currentValue": "code",
                                        "options": [{"value": "code", "name": "Code"}],
                                    }
                                ],
                            },
                        },
                    }
                )
                result = {}
            await client._dispatch({"jsonrpc": "2.0", "id": request["id"], "result": result})

        asyncio.run_coroutine_threadsafe(respond(), loop)

    client._sync_write = respond_from_writer
    await client._rpc("initialize", {"protocolVersion": 1, "clientCapabilities": {}}, timeout=1)

    assert client.supports_session_capability("list") is True
    assert client.agent_info == {"name": "opencode", "version": "test"}

    client.session_id = "session-old"
    client.session_config_options = [{"id": "old"}]
    await client._rpc(
        "session/load",
        {"sessionId": "session-target", "cwd": "C:/workspace", "mcpServers": []},
        timeout=1,
    )

    assert client.session_id == "session-target"
    assert client.session_config_options[0]["currentValue"] == "code"
    assert client._session_transition_id is None


def test_opencode_reader_drains_buffered_response_after_process_exit():
    client = OpenCodeACPClient(make_config())
    client._popen = ExitedBufferedPopen()
    messages: list[dict | BaseException | None] = []
    client._schedule_dispatch = messages.append

    client._sync_read_loop()

    assert messages == [
        {"jsonrpc": "2.0", "id": 17, "result": {"ok": True}},
        None,
    ]


@pytest.mark.asyncio
async def test_opencode_dispatch_backlog_fails_closed(monkeypatch):
    monkeypatch.setattr(opencode_module, "MAX_OPENCODE_DISPATCH_BACKLOG", 2)
    client = OpenCodeACPClient(make_config())
    client._loop = asyncio.get_running_loop()
    client._dispatch_queue = asyncio.Queue()

    for identifier in range(4):
        client._schedule_dispatch({"jsonrpc": "2.0", "id": identifier, "result": {}})

    await asyncio.sleep(0)
    queued = [client._dispatch_queue.get_nowait() for _ in range(3)]

    assert [message["id"] for message in queued[:2]] == [0, 1]
    assert isinstance(queued[2], ACPProtocolError)
    assert "backlog" in str(queued[2])
    assert client._dispatch_queue.empty()


@pytest.mark.asyncio
async def test_opencode_stale_scheduled_frame_cannot_enter_replacement_queue():
    client = OpenCodeACPClient(make_config())
    client._loop = asyncio.get_running_loop()
    old_queue: asyncio.Queue = asyncio.Queue()
    replacement_queue: asyncio.Queue = asyncio.Queue()
    client._dispatch_queue = old_queue

    client._schedule_dispatch({"jsonrpc": "2.0", "id": 1, "result": {}})
    client._dispatch_queue = replacement_queue
    await asyncio.sleep(0)

    assert old_queue.empty()
    assert replacement_queue.empty()
    assert client._scheduled_dispatches == 0


def test_opencode_windows_pipe_write_handles_partial_writes(monkeypatch):
    client = OpenCodeACPClient(make_config())
    client._connected = True
    client._popen = WriteOnlyPopen()
    written = bytearray()

    def partial_write(_fd: int, data) -> int:
        chunk = bytes(data[:2])
        written.extend(chunk)
        return len(chunk)

    monkeypatch.setattr(opencode_module.sys, "platform", "win32")
    monkeypatch.setattr(opencode_module.os, "write", partial_write)
    client._sync_write(b"abcdef")

    assert written == b"abcdef"


@pytest.mark.asyncio
async def test_real_local_subprocess_disconnect_reaps_process_and_readers():
    client = LocalProcessClient(make_config())
    await client.connect()
    process = client.process
    try:
        assert process is not None
        assert process.returncode is None
        assert len(client._reader_tasks) == 2
    finally:
        await client.disconnect()

    assert process.returncode is not None
    assert client.process is None
    assert client._reader_tasks == []


@pytest.mark.asyncio
async def test_base_disconnect_retains_unreaped_process_for_retry():
    client = RacingClient(make_config())
    process = StubbornAsyncProcess()
    client.process = process
    client._connected = True

    with pytest.raises(RuntimeError, match="did not exit"):
        await client.disconnect()

    assert client.process is process
    process.can_exit = True
    await client.disconnect()
    assert client.process is None


@pytest.mark.asyncio
async def test_base_disconnect_reaps_parent_when_host_cleanup_fails(monkeypatch):
    client = RacingClient(make_config())
    await client._prepare_host_services()
    process = FakeProcess()
    client.process = process
    terminal_service = client._terminal_service

    async def failed_host_cleanup():
        raise RuntimeError("host cleanup failed")

    monkeypatch.setattr(client, "_close_host_services", failed_host_cleanup)

    with pytest.raises(RuntimeError, match="host cleanup failed"):
        await client.disconnect()

    assert process.terminate_calls == 1
    assert client.process is None

    with pytest.raises(RuntimeError, match="host cleanup failed"):
        await client._prepare_host_services()
    assert client._terminal_service is terminal_service


@pytest.mark.asyncio
async def test_cursor_disconnect_retains_unreaped_process_for_retry():
    client = CursorCLIClient(make_config())
    process = StubbornAsyncProcess()
    client._binary = "fake-cursor"
    client._process = process

    with pytest.raises(RuntimeError, match="did not exit"):
        await client.disconnect()

    assert client._process is process
    process.can_exit = True
    await client.disconnect()
    assert client._process is None


@pytest.mark.asyncio
async def test_cursor_resume_waits_for_in_flight_query_state():
    client = CursorCLIClient(make_config())
    await client._query_lock.acquire()
    resume = asyncio.create_task(client.resume("session-new"))
    await asyncio.sleep(0)

    assert resume.done() is False
    assert client.session_id is None

    client._query_lock.release()
    assert await resume is True
    assert client.session_id == "session-new"


@pytest.mark.asyncio
async def test_natural_stdout_eof_schedules_full_process_cleanup():
    client = RacingClient(make_config())
    process = FakeProcess()
    client.process = process
    client._connected = True
    client._inflight[99] = ("initialize", 0.0)
    reader = asyncio.create_task(client._read_stdout())
    client._reader_tasks = [reader]

    await reader
    assert client._inflight == {}
    assert client._process_cleanup_task is not None
    await client._process_cleanup_task

    assert process.terminate_calls == 1
    assert client.process is None


@pytest.mark.asyncio
async def test_stdout_reader_drains_buffered_response_after_process_exit():
    client = RacingClient(make_config())
    process = FakeProcess()
    process.returncode = 0
    process.stdout = CursorBuffer(lines=[{"jsonrpc": "2.0", "id": 11, "result": {"ok": True}}])
    client.process = process
    client._connected = True
    future = asyncio.get_running_loop().create_future()
    client._pending[11] = future
    client._inflight[11] = ("initialize", 0.0)

    await client._read_stdout()

    assert await future == {"ok": True}
    if client._process_cleanup_task is not None:
        await client._process_cleanup_task


@pytest.mark.asyncio
async def test_stdout_reader_drains_json_frame_larger_than_common_pipe_buffers():
    payload_size = 256 * 1024
    script = (
        "import json; "
        f"print(json.dumps({{'jsonrpc': '2.0', 'id': 21, 'result': {{'payload': 'x' * {payload_size}}}}}), flush=True)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-c",
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=10 * 1024 * 1024,
    )
    client = RacingClient(make_config())
    client.process = process
    client._connected = True
    future = asyncio.get_running_loop().create_future()
    client._pending[21] = future
    client._inflight[21] = ("large-frame", 0.0)

    reader = asyncio.create_task(client._read_stdout())
    result = await asyncio.wait_for(future, timeout=5)
    await asyncio.wait_for(reader, timeout=5)

    assert len(result["payload"]) == payload_size
    if client._process_cleanup_task is not None:
        await client._process_cleanup_task


@pytest.mark.asyncio
async def test_stdout_reader_rejects_invalid_utf8_and_fails_pending_rpc():
    client = RacingClient(make_config())
    process = FakeProcess()
    process.stdout = RawCursorBuffer([b'{"jsonrpc":"2.0","id":12,"result":{"text":"\xff"}}\n'])
    client.process = process
    client._connected = True
    future = asyncio.get_running_loop().create_future()
    client._pending[12] = future
    client._inflight[12] = ("initialize", 0.0)

    await client._read_stdout()

    with pytest.raises(ACPProtocolError, match="valid UTF-8"):
        await future
    if client._process_cleanup_task is not None:
        await client._process_cleanup_task


@pytest.mark.asyncio
async def test_stdout_reader_does_not_drop_empty_json_object():
    client = RacingClient(make_config())
    process = FakeProcess()
    process.stdout = RawCursorBuffer([b"{}\n"])
    client.process = process
    client._connected = True
    future = asyncio.get_running_loop().create_future()
    client._pending[13] = future
    client._inflight[13] = ("initialize", 0.0)

    await client._read_stdout()

    with pytest.raises(ACPProtocolError, match=r"version 2\.0"):
        await future
    if client._process_cleanup_task is not None:
        await client._process_cleanup_task


class CursorStdin:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class HangingCloseCursorStdin(CursorStdin):
    async def wait_closed(self) -> None:
        await asyncio.Event().wait()


class CursorPromptProcess:
    def __init__(self, stdin: CursorStdin) -> None:
        self.stdin = stdin


@pytest.mark.asyncio
async def test_cursor_stdin_close_wait_respects_remaining_prompt_deadline():
    client = CursorCLIClient(make_config(provider="cursor", timeout_prompt=0.02))
    stdin = HangingCloseCursorStdin()
    process = CursorPromptProcess(stdin)
    loop = asyncio.get_running_loop()
    started = loop.time()

    await client._write_prompt(process, "prompt", started + client.config.timeout_prompt)

    elapsed = loop.time() - started
    assert elapsed < 0.2
    assert stdin.closed is True
    assert stdin.data == b"prompt"


class HangingCursorProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.communicate_started = asyncio.Event()
        self.terminate_calls = 0
        self.kill_calls = 0
        self.stdin = CursorStdin()
        self.stdout = HangingCursorBuffer(self.communicate_started)
        self.stderr = HangingCursorBuffer(self.communicate_started)

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode


class ExitedCursorProcessWithInheritedPipes(HangingCursorProcess):
    def __init__(self) -> None:
        super().__init__()
        self.returncode = 0


class HangingCursorLineBuffer:
    async def readline(self) -> bytes:
        await asyncio.Event().wait()
        return b""


class ExitedCursorStreamProcess:
    def __init__(self) -> None:
        self.returncode = 0
        self.stdin = CursorStdin()
        self.stdout = HangingCursorLineBuffer()
        self.stderr = CursorBuffer()

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


class CursorBuffer:
    def __init__(self, lines: list[dict] | None = None, data: bytes = b"") -> None:
        self.lines = [json.dumps(line).encode() + b"\n" for line in (lines or [])]
        self.data = bytearray(data)

    async def readline(self) -> bytes:
        if self.lines:
            return self.lines.pop(0)
        return b""

    async def read(self, size: int = -1) -> bytes:
        if not self.data:
            return b""
        if size < 0:
            size = len(self.data)
        chunk = bytes(self.data[:size])
        del self.data[:size]
        return chunk


class HangingCursorBuffer:
    def __init__(self, started: asyncio.Event) -> None:
        self.started = started

    async def read(self, _size: int = -1) -> bytes:
        self.started.set()
        await asyncio.Event().wait()
        return b""


class RawCursorBuffer(CursorBuffer):
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines


class CompletedCursorProcess:
    def __init__(self, payload: dict | None = None, lines: list[dict] | None = None) -> None:
        self.payload = payload
        payload_bytes = json.dumps(payload).encode() if payload is not None else b""
        self.stdin = CursorStdin()
        self.stdout = CursorBuffer(lines, payload_bytes)
        self.stderr = CursorBuffer()
        self.returncode: int | None = 0 if payload is not None else None
        self.terminate_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode


@pytest.mark.asyncio
async def test_cursor_communicate_stops_when_exited_child_pipes_remain_open(monkeypatch):
    from soulacp.adapters import cursor_client as cursor_module

    monkeypatch.setattr(cursor_module, "CURSOR_EXIT_DRAIN_TIMEOUT", 0.01)
    client = CursorCLIClient(make_config())
    process = ExitedCursorProcessWithInheritedPipes()

    with pytest.raises(ACPProtocolError, match="pipes remained open"):
        await client._communicate_bounded(process)


@pytest.mark.asyncio
async def test_cursor_communicate_cancels_sibling_reader_after_read_failure(monkeypatch):
    client = CursorCLIClient(make_config())
    process = HangingCursorProcess()
    stderr_started = asyncio.Event()
    stderr_cancelled = asyncio.Event()

    async def controlled_read(_reader, _limit, label):
        if label == "stdout":
            await stderr_started.wait()
            raise ACPProtocolError("stdout read failed")
        stderr_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stderr_cancelled.set()

    monkeypatch.setattr(client, "_read_bounded", controlled_read)

    with pytest.raises(ACPProtocolError, match="stdout read failed"):
        await client._communicate_bounded(process)

    assert stderr_cancelled.is_set()


@pytest.mark.asyncio
async def test_cursor_stream_stops_when_exited_child_pipe_remains_open(monkeypatch):
    from soulacp.adapters import cursor_client as cursor_module

    monkeypatch.setattr(cursor_module, "CURSOR_EXIT_DRAIN_TIMEOUT", 0.01)
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = ExitedCursorStreamProcess()

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)

    with pytest.raises(ACPProtocolError, match="pipe remained open"):
        _chunks = [chunk async for chunk in client.query_stream("hello")]


@pytest.mark.asyncio
async def test_cursor_cleanup_closes_transport_for_an_already_exited_process():
    class Transport:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    process = CompletedCursorProcess(payload={})
    process._transport = Transport()

    await CursorCLIClient._terminate_owned_process(process)

    assert process._transport.close_calls == 1


@pytest.mark.asyncio
async def test_cursor_query_cancellation_terminates_child(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1))
    client._binary = "fake-cursor"
    process = HangingCursorProcess()

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    task = asyncio.create_task(client.query("hello"))
    await process.communicate_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminate_calls == 1
    assert client._process is None


@pytest.mark.asyncio
async def test_cursor_query_cleanup_survives_repeated_cancellation(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1))
    client._binary = "fake-cursor"

    class ControlledCursorProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.stdin = CursorStdin()
            self.wait_started = asyncio.Event()
            self.exited = asyncio.Event()
            self.terminate_calls = 0

        def terminate(self) -> None:
            self.terminate_calls += 1

        def kill(self) -> None:
            self.returncode = -9
            self.exited.set()

        async def wait(self):
            self.wait_started.set()
            await self.exited.wait()
            return self.returncode

    process = ControlledCursorProcess()
    communication_started = asyncio.Event()
    removed: list[str | None] = []

    async def fake_spawn(_cmd):
        return process

    async def hanging_communicate(_process):
        communication_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    monkeypatch.setattr(client, "_communicate_bounded", hanging_communicate)
    monkeypatch.setattr(client, "_build_command", lambda *_args, **_kwargs: (["fake"], "prompt.tmp"))
    monkeypatch.setattr(client, "_remove_temp_file", removed.append)

    task = asyncio.create_task(client.query("hello"))
    await communication_started.wait()
    task.cancel()
    await process.wait_started.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    process.returncode = 0
    process.exited.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.terminate_calls == 1
    assert client._process is None
    assert removed == ["prompt.tmp"]


@pytest.mark.asyncio
async def test_cursor_disconnect_waits_for_late_spawn_and_reaps_child(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=10, timeout_connect=10))
    client._binary = "fake-cursor"
    process = HangingCursorProcess()
    spawn_started = asyncio.Event()
    release_spawn = asyncio.Event()

    async def gated_spawn(_cmd):
        spawn_started.set()
        await release_spawn.wait()
        return process

    monkeypatch.setattr(client, "_spawn", gated_spawn)
    query_task = asyncio.create_task(client.query("hello"))
    await spawn_started.wait()
    disconnect_task = asyncio.create_task(client.disconnect())
    await asyncio.sleep(0)
    assert disconnect_task.done() is False

    release_spawn.set()
    await disconnect_task
    query_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await query_task

    assert process.terminate_calls == 1
    assert client._process is None
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_cursor_spawn_timeout_claims_and_reaps_late_child(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=10, timeout_connect=0.01))
    client._binary = "fake-cursor"
    process = HangingCursorProcess()
    spawn_started = asyncio.Event()
    release_spawn = asyncio.Event()

    async def gated_spawn(_cmd):
        spawn_started.set()
        await release_spawn.wait()
        return process

    monkeypatch.setattr(client, "_spawn", gated_spawn)
    query_task = asyncio.create_task(client.query("hello"))
    await spawn_started.wait()

    with pytest.raises(ConnectionError, match="startup exceeded"):
        await query_task

    disconnect_task = asyncio.create_task(client.disconnect())
    await asyncio.sleep(0)
    assert disconnect_task.done() is False

    release_spawn.set()
    await disconnect_task

    assert process.terminate_calls == 1
    assert client._spawn_task is None
    assert client._spawn_cleanup_task is None
    assert client._process is None
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_cursor_cancelled_spawn_claims_and_reaps_late_child(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=10, timeout_connect=10))
    client._binary = "fake-cursor"
    process = HangingCursorProcess()
    spawn_started = asyncio.Event()
    release_spawn = asyncio.Event()

    async def gated_spawn(_cmd):
        spawn_started.set()
        await release_spawn.wait()
        return process

    monkeypatch.setattr(client, "_spawn", gated_spawn)
    query_task = asyncio.create_task(client.query("hello"))
    await spawn_started.wait()
    query_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await query_task

    disconnect_task = asyncio.create_task(client.disconnect())
    await asyncio.sleep(0)
    assert disconnect_task.done() is False

    release_spawn.set()
    await disconnect_task

    assert process.terminate_calls == 1
    assert client._spawn_task is None
    assert client._spawn_cleanup_task is None
    assert client._process is None
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_cursor_non_stream_parses_json_result_and_remembers_session(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        payload={
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "complete response",
            "session_id": "cursor-session",
        }
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    assert await client.query("hello") == "complete response"
    assert client.session_id == "cursor-session"
    assert bytes(process.stdin.data) == b"hello"
    assert process.stdin.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_value", ["\ud800", float("nan")])
async def test_cursor_non_stream_rejects_non_standard_json_values(monkeypatch, invalid_value):
    client = CursorCLIClient(make_config(timeout_prompt=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        payload={
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": invalid_value,
        }
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    with pytest.raises(ACPProtocolError, match="json output was malformed"):
        await client.query("hello")


@pytest.mark.asyncio
async def test_cursor_non_stream_rejects_invalid_utf8(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(payload={"type": "result"})
    process.stdout = CursorBuffer(data=b'{"type":"result","subtype":"success","is_error":false,"result":"\xff"}')

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    with pytest.raises(ACPProtocolError, match="not valid UTF-8"):
        await client.query("hello")


@pytest.mark.asyncio
async def test_cursor_cleanup_failure_does_not_mask_protocol_error(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(payload={"type": "unexpected"})

    async def fake_spawn(_cmd):
        return process

    async def failed_cleanup(*_args):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    monkeypatch.setattr(client, "_finish_query_cleanup", failed_cleanup)

    with pytest.raises(ACPProtocolError, match="terminal result"):
        await client.query("hello")


@pytest.mark.asyncio
async def test_cursor_success_propagates_cleanup_failure(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        payload={
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "complete response",
        }
    )

    async def fake_spawn(_cmd):
        return process

    async def failed_cleanup(*_args):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    monkeypatch.setattr(client, "_finish_query_cleanup", failed_cleanup)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await client.query("hello")


@pytest.mark.asyncio
async def test_cursor_bounded_reader_rejects_oversized_output():
    reader = CursorBuffer(data=b"12345")

    with pytest.raises(ACPProtocolError, match="4-byte"):
        await CursorCLIClient._read_bounded(reader, 4, "stdout")


@pytest.mark.asyncio
async def test_cursor_stream_parses_documented_deltas_and_terminal_result(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        lines=[
            {"type": "system", "subtype": "init", "session_id": "cursor-session"},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello"}]},
                "session_id": "cursor-session",
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": " world"}]},
                "session_id": "cursor-session",
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Hello world!",
                "session_id": "cursor-session",
            },
        ]
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    chunks = [chunk async for chunk in client.query_stream("hello")]

    assert chunks == ["Hello", " world", "!"]
    assert client.session_id == "cursor-session"
    assert process.terminate_calls == 1


@pytest.mark.asyncio
async def test_cursor_stream_prompt_deadline_excludes_downstream_chunk_processing(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=0.02, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        lines=[
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "first"}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "first second",
            },
        ]
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    stream = client.query_stream("hello")

    assert await anext(stream) == "first"
    await asyncio.sleep(0.03)
    assert await anext(stream) == " second"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_cursor_stream_filters_partial_output_buffered_copies(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        lines=[
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "first"}]},
                "timestamp_ms": 1,
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "first"}]},
                "model_call_id": "model-call",
                "timestamp_ms": 2,
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": " second"}]},
                "timestamp_ms": 3,
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "first second"}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "first second",
            },
        ]
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    chunks = [chunk async for chunk in client.query_stream("hello")]

    assert chunks == ["first", " second"]


@pytest.mark.asyncio
async def test_cursor_stream_preserves_repeated_standard_deltas_without_timestamps(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        lines=[
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "ha"}]},
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "ha"}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "haha",
            },
        ]
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    chunks = [chunk async for chunk in client.query_stream("hello")]

    assert chunks == ["ha", "ha"]


@pytest.mark.asyncio
async def test_cursor_stream_bounds_event_count(monkeypatch):
    monkeypatch.setattr(cursor_module, "MAX_CURSOR_STREAM_EVENTS", 2)
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        lines=[
            {"type": "system", "sequence": 1},
            {"type": "system", "sequence": 2},
            {"type": "system", "sequence": 3},
        ]
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    with pytest.raises(ACPProtocolError, match="event limit"):
        _chunks = [chunk async for chunk in client.query_stream("hello")]


@pytest.mark.asyncio
async def test_cursor_stream_bounds_text_chunk_count(monkeypatch):
    monkeypatch.setattr(cursor_module, "MAX_CURSOR_RESPONSE_CHUNKS", 1)
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        lines=[
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "first"}]},
            },
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "second"}]},
            },
        ]
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    with pytest.raises(ACPProtocolError, match="chunk limit") as exc_info:
        _chunks = [chunk async for chunk in client.query_stream("hello")]

    assert exc_info.value.partial_output == "first"


@pytest.mark.asyncio
async def test_cursor_stream_bounds_total_stdout_bytes(monkeypatch):
    monkeypatch.setattr(cursor_module, "MAX_CURSOR_STREAM_BYTES", 8)
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(lines=[])
    process.stdout = RawCursorBuffer([b'{"type":"system"}\n'])

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    with pytest.raises(ACPProtocolError, match="byte limit"):
        _chunks = [chunk async for chunk in client.query_stream("hello")]


@pytest.mark.asyncio
async def test_cursor_stream_rejects_malformed_ndjson(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(lines=[])
    process.stdout = RawCursorBuffer([b"not-json\n"])

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    with pytest.raises(ACPProtocolError, match="malformed stream JSON"):
        _chunks = [chunk async for chunk in client.query_stream("hello")]


@pytest.mark.asyncio
async def test_cursor_stream_rejects_invalid_utf8_with_partial_output(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(lines=[])
    process.stdout = RawCursorBuffer(
        [
            b'{"type":"assistant","message":{"content":[{"type":"text","text":"first"}]}}\n',
            b'{"type":"assistant","message":{"content":[{"type":"text","text":"\xff"}]}}\n',
        ]
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    with pytest.raises(ACPProtocolError, match="not valid UTF-8") as exc_info:
        _chunks = [chunk async for chunk in client.query_stream("hello")]

    assert exc_info.value.partial_output == "first"


@pytest.mark.asyncio
async def test_cursor_stream_rejects_terminal_text_mismatch(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1, timeout_stream=1))
    client._binary = "fake-cursor"
    process = CompletedCursorProcess(
        lines=[
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello"}]},
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Goodbye",
            },
        ]
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    with pytest.raises(ACPProtocolError) as exc_info:
        _chunks = [chunk async for chunk in client.query_stream("hello")]

    assert exc_info.value.partial_output == "Hello"


def test_cursor_prompt_is_not_exposed_in_process_arguments():
    client = CursorCLIClient(make_config())
    client._binary = "fake-cursor"
    prompt = "x" * 5_000

    command, temp_path = client._build_command(prompt, stream=True)

    assert prompt not in command
    assert temp_path is None
    assert "--force" in command
    assert "-f" not in command


@pytest.mark.asyncio
async def test_cursor_provider_details_are_retained_but_not_rendered(monkeypatch):
    client = CursorCLIClient(make_config(timeout_prompt=1))
    client._binary = "fake-cursor"
    secret = "private provider diagnostic"
    process = CompletedCursorProcess(
        payload={
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": secret,
        }
    )

    async def fake_spawn(_cmd):
        return process

    monkeypatch.setattr(client, "_spawn", fake_spawn)
    with pytest.raises(ACPPartialResponseError) as exc_info:
        await client.query("hello")

    assert secret not in str(exc_info.value)
    assert isinstance(exc_info.value.cause, ACPProviderError)
    assert exc_info.value.cause.details == secret


@pytest.mark.asyncio
async def test_cursor_startup_obeys_connect_timeout_and_cleans_temp_file(monkeypatch):
    client = CursorCLIClient(make_config(timeout_connect=0.01, timeout_prompt=1))
    client._binary = "fake-cursor"
    removed: list[str | None] = []
    process = HangingCursorProcess()
    release_spawn = asyncio.Event()

    async def hanging_spawn(_cmd):
        await release_spawn.wait()
        return process

    monkeypatch.setattr(client, "_build_command", lambda *_args, **_kwargs: (["fake"], "prompt.tmp"))
    monkeypatch.setattr(client, "_spawn", hanging_spawn)
    monkeypatch.setattr(client, "_remove_temp_file", removed.append)

    with pytest.raises(ConnectionError, match="startup exceeded"):
        await client.query("hello")
    assert removed == ["prompt.tmp"]
    assert client._process is None

    release_spawn.set()
    await client.disconnect()
    assert process.terminate_calls == 1


# --- Process-tree termination on disconnect; synthetic CLI and tool fixtures. ---


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = _subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return f" {pid} " in out
    try:
        _os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_disconnect_terminates_the_process_tree_before_the_parent(monkeypatch, tmp_path):
    process = FakeProcess()
    seen: list[tuple[object, int]] = []

    async def fake_tree(proc, descendants=None):
        # runs before terminate(); it receives the descendants recorded at the start of
        # the disconnect (the CLI may already have exited on stdin EOF by now)
        seen.append((proc, proc.terminate_calls, descendants))
        return "recorded"

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(base_client_module, "_terminate_process_tree", fake_tree)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    client = InitializeFailureClient(make_config(cwd=str(tmp_path)))

    with pytest.raises(RuntimeError, match="initialize failed"):
        await client.connect()

    # a fake process is never snapshotted (no live pid), so the recorded set is empty
    assert seen == [(process, 0, set())]
    assert process.terminate_calls == 1 and process.stdin.closed and client.process is None


def test_snapshot_descendants_skips_fakes_and_missing_pids():
    assert base_client_module._snapshot_descendants(None) == set()
    assert base_client_module._snapshot_descendants(0) == set()


@pytest.mark.asyncio
async def test_terminate_process_tree_skips_fakes_and_exited_processes():
    assert await base_client_module._terminate_process_tree(FakeProcess()) is None
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "pass",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    assert await base_client_module._terminate_process_tree(proc) is None


def test_process_group_kwargs_follow_the_platform():
    kwargs = base_client_module._process_group_kwargs()
    if sys.platform == "win32":
        assert kwargs == {}
    else:
        assert kwargs == {"start_new_session": True}


@pytest.mark.asyncio
async def test_terminate_process_tree_kills_the_grandchild_of_a_live_cli():
    """A stand-in CLI spawns a long-running tool (its grandchild from our point of view); taking the tree down must end
    the tool within seconds even though only the CLI's pid is known."""
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(120)\n"
    )
    parent = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        **base_client_module._process_group_kwargs(),
    )
    try:
        line = await asyncio.wait_for(parent.stdout.readline(), timeout=30)
        grandchild = int(line.strip())
        assert _pid_alive(grandchild)

        outcome = await base_client_module._terminate_process_tree(parent)
        assert outcome is not None and "failed" not in outcome

        if parent.returncode is None:
            with _suppress(ProcessLookupError):
                parent.terminate()
        await asyncio.wait_for(parent.wait(), timeout=10)
        deadline = _time.monotonic() + 5.0
        while _pid_alive(grandchild) and _time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert not _pid_alive(grandchild)
    finally:
        if parent.returncode is None:
            with _suppress(ProcessLookupError):
                parent.kill()
            await parent.wait()
        with _suppress(Exception):
            await asyncio.wait_for(parent.stdout.read(), timeout=5)


@pytest.mark.asyncio
async def test_descendants_are_terminated_after_the_cli_exited():
    """The live failure mode: the CLI is gone before any tree walk runs, leaving the tool it spawned as an orphan.
    Windows keeps the orphan's parent id, so the walk from the CLI's pid still finds it; POSIX reaches it through
    the CLI's process group. Either way the orphan must be terminated."""
    script = (
        "import os, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print(os.getpid(), flush=True)\n"
        "print(child.pid, flush=True)\n"  # then exit at once, orphaning the child
    )
    # Windows venv redirectors insert a short-lived interpreter between this
    # Process and the tool. Exercise the intended direct-parent contract, not
    # an already-missing intermediate link that a live snapshot cannot prove.
    executable = getattr(sys, "_base_executable", sys.executable) if sys.platform == "win32" else sys.executable
    parent = await asyncio.create_subprocess_exec(
        executable,
        "-c",
        script,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        **base_client_module._process_group_kwargs(),
    )
    grandchild = None
    try:
        parent_line = await asyncio.wait_for(parent.stdout.readline(), timeout=30)
        line = await asyncio.wait_for(parent.stdout.readline(), timeout=30)
        grandchild = int(line.strip())
        assert int(parent_line.strip()) == parent.pid
        # Process.wait() only resolves once every pipe is closed, and the orphan still
        # holds the inherited stdout pipe: poll the exit code instead.
        deadline = _time.monotonic() + 10.0
        while parent.returncode is None and _time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert parent.returncode is not None and _pid_alive(grandchild)
        recorded = base_client_module._snapshot_descendants(parent.pid)
        if sys.platform == "win32":
            assert grandchild in recorded

        outcome = await base_client_module._terminate_process_tree(parent, recorded)
        assert outcome is not None and "failed" not in outcome
        deadline = _time.monotonic() + 5.0
        while _pid_alive(grandchild) and _time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert not _pid_alive(grandchild)
    finally:
        if grandchild is not None and _pid_alive(grandchild):
            if sys.platform == "win32":
                _subprocess.run(["taskkill", "/F", "/PID", str(grandchild)], capture_output=True, check=False)
            else:
                with _suppress(ProcessLookupError):
                    _os.kill(grandchild, 9)
        if parent.returncode is None:
            with _suppress(ProcessLookupError):
                parent.kill()
            await parent.wait()
        with _suppress(Exception):
            await asyncio.wait_for(parent.stdout.read(), timeout=5)
