"""Fail closed across cancellation, ambiguous permissions and malformed configuration."""

import asyncio
import copy
import os
import subprocess
import sys
import traceback

import pytest

from soulacp import ACPConfig
from soulacp.adapters.base_client import ACPClientBase


class CaptureClient(ACPClientBase):
    def __init__(self, auto_approve=True):
        super().__init__(ACPConfig(auto_approve_permissions=auto_approve))
        self._connected = True
        self.session_id = "synthetic-session"
        self.results = []
        self.errors = []
        self.notifications = []
        self.notification_started = asyncio.Event()
        self.notification_release = None
        self.notification_failure = False
        self.request_started = asyncio.Event()
        self.request_release = None

    def _get_acp_command(self):
        raise AssertionError("This fixture cannot start a provider")

    async def _initialize(self):
        raise AssertionError("This fixture cannot initialize a provider")

    async def _send_result(self, mid, result):
        self.results.append((mid, copy.deepcopy(result)))

    async def _send_error(self, mid, code, message):
        self.errors.append((mid, code, message))

    async def _send_notification(self, method, params=None):
        self.notifications.append((method, params))
        self.notification_started.set()
        if self.notification_release is not None:
            await self.notification_release.wait()
        if self.notification_failure:
            raise ConnectionError("synthetic failure")

    async def _handle_request(self, msg):
        self.request_started.set()
        if self.request_release is not None:
            await self.request_release.wait()
        await super()._handle_request(msg)


def request(client, options=None, mid=1):
    if options is None:
        options = [
            dict(optionId="allow", name="Allow", kind="allow_once"),
            dict(optionId="reject", name="Reject", kind="reject_once"),
        ]
    return dict(
        jsonrpc="2.0",
        id=mid,
        method="session/request_permission",
        params=dict(sessionId=client.session_id, toolCall=dict(toolCallId="tool"), options=options),
    )


async def deliver(client, message):
    await client._dispatch(message)
    if client._request_tasks:
        await asyncio.gather(*list(client._request_tasks))
    await asyncio.sleep(0)


@pytest.mark.parametrize("auto_approve,expected", [(True, "allow"), (False, "reject")])
async def test_valid_permissions_preserve_policy(auto_approve, expected):
    client = CaptureClient(auto_approve)
    await deliver(client, request(client))
    assert not client.errors
    assert client.results == [(1, {"outcome": {"outcome": "selected", "optionId": expected}})]


@pytest.mark.parametrize("auto_approve", [True, False])
@pytest.mark.parametrize("phase", ["after", "during", "failed", "repeated"])
async def test_cancelled_turn_never_selects_permission(auto_approve, phase):
    client = CaptureClient(auto_approve)
    if phase == "during":
        client.notification_release = asyncio.Event()
        cancelling = asyncio.create_task(client.cancel())
        await client.notification_started.wait()
    else:
        client.notification_failure = phase == "failed"
        await client.cancel()
        if phase == "repeated":
            await client.cancel()
    try:
        await deliver(client, request(client))
        assert client.results == [(1, {"outcome": {"outcome": "cancelled"}})]
    finally:
        if phase == "during":
            client.notification_release.set()
            await cancelling


@pytest.mark.parametrize("stream", [True, False])
async def test_next_prompt_resets_current_turn_without_reauthorizing_queued_old_request(stream):
    client = CaptureClient()
    client.request_release = asyncio.Event()
    await client._dispatch(request(client))
    await client.request_started.wait()
    await client.cancel()

    async def rpc(method, params, timeout=None):
        assert method == "session/prompt"
        client.request_release.set()
        await asyncio.gather(*list(client._request_tasks))
        await deliver(client, request(client, mid=2))
        return {"stopReason": "end_turn"}

    client._rpc = rpc
    if stream:
        assert [chunk async for chunk in client.query_stream("synthetic")] == []
    else:
        assert await client.query("synthetic") == ""
    outcomes = dict(client.results)
    assert outcomes[1] == {"outcome": {"outcome": "cancelled"}}
    assert outcomes[2] == {"outcome": {"outcome": "selected", "optionId": "allow"}}
    await asyncio.sleep(0)
    assert not client._request_tasks_by_id


async def test_cancel_does_not_weaken_cross_session_rejection():
    client = CaptureClient()
    await client.cancel()
    message = request(client)
    message["params"]["sessionId"] = "other-session"
    await deliver(client, message)
    assert not client.results
    assert client.errors[0][1] == -32602


@pytest.mark.parametrize("auto_approve", [True, False])
@pytest.mark.parametrize("shape", ["mixed-kinds", "same-kind", "legacy", "mixed-shapes"])
async def test_duplicate_option_ids_are_rejected_before_selection(auto_approve, shape):
    options = [
        dict(optionId="same", name="Allow", kind="allow_once"),
        dict(optionId="same", name="Reject", kind="reject_once"),
    ]
    if shape == "same-kind":
        options[1]["kind"] = "allow_once"
    elif shape == "legacy":
        options = [dict(optionId="same"), dict(optionId="same")]
    elif shape == "mixed-shapes":
        options[1].pop("kind")
    client = CaptureClient(auto_approve)
    await deliver(client, request(client, options))
    assert not client.results
    assert client.errors[0][1] == -32602


@pytest.mark.parametrize("auto_approve", [True, False])
async def test_unique_legacy_options_keep_previous_compatibility(auto_approve):
    client = CaptureClient(auto_approve)
    await deliver(client, request(client, [dict(optionId="legacy")]))
    if auto_approve:
        assert client.results[0][1]["outcome"]["optionId"] == "legacy"
    else:
        assert not client.results
        assert client.errors[0][1] == -32602


MARKER = "SYNTHETIC_CONFIG_PRIVATE_VALUE"


def assert_redacted(error):
    assert isinstance(error, ValueError)
    assert MARKER not in str(error)
    assert MARKER not in repr(error)
    assert MARKER not in "".join(traceback.format_exception(type(error), error, error.__traceback__))


@pytest.mark.parametrize("name", ["pool_size", "timeout_prompt", "enable_fallback", "inherit_parent_env"])
def test_config_wrong_type_has_safe_default_diagnostics(name):
    with pytest.raises(ValueError) as caught:
        ACPConfig(**{name: MARKER})
    assert_redacted(caught.value)


@pytest.mark.parametrize("name", ["ACP_POOL_SIZE", "ACP_TIMEOUT_PROMPT", "ACP_AUTO_APPROVE", "ACP_MAX_TURNS"])
def test_environment_parser_does_not_leak_invalid_values_or_chains(monkeypatch, name):
    monkeypatch.setenv(name, MARKER)
    with pytest.raises(ValueError) as caught:
        ACPConfig.from_env()
    assert_redacted(caught.value)
    assert name in str(caught.value)


def test_unicode_environment_value_error_does_not_echo_its_private_key():
    with pytest.raises(ValueError) as caught:
        ACPConfig(extra_env={MARKER: "\ud800"})
    assert_redacted(caught.value)


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False), ("yes", True), ("0", False)])
def test_valid_boolean_environment_parsing_is_preserved(monkeypatch, value, expected):
    monkeypatch.setenv("ACP_AUTO_APPROVE", value)
    assert ACPConfig.from_env().auto_approve_permissions is expected


@pytest.mark.parametrize("shape", ["list", "dict", "server"])
def test_cyclic_mcp_input_is_bounded_and_rejected(shape):
    code = """import sys
from soulacp import ACPConfig
server = dict(name='synthetic', command=sys.executable, args=[], env=[])
shape = sys.argv[1]
if shape == 'list':
    value = []; value.append(value)
elif shape == 'dict':
    value = {}; value['self'] = value
else:
    value = server
server['_meta'] = {'nested': value}
try:
    ACPConfig(mcp_servers=[server])
except ValueError:
    print('REJECTED')
else:
    print('ACCEPTED')
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, shape], capture_output=True, text=True, timeout=2)
    assert result.returncode == 0
    assert result.stdout.strip() == "REJECTED"


def test_shared_acyclic_mcp_metadata_is_still_legal_and_detached():
    shared = {"values": [1, 2]}
    server = dict(name="synthetic", command=sys.executable, args=[], env=[], _meta={"a": shared, "b": shared})
    config = ACPConfig(mcp_servers=[server])
    shared["values"].append(3)
    assert config.mcp_servers[0]["_meta"] == {"a": {"values": [1, 2]}, "b": {"values": [1, 2]}}


def test_directory_scan_is_lazy_and_closes_at_limit(tmp_path, monkeypatch):
    from soulacp.services.fs_service import FSService

    for index in range(12):
        (tmp_path / str(index)).touch()
    original = os.scandir
    consumed = []
    closed = []

    class Scan:
        def __init__(self, path):
            self.inner = original(path)

        def __enter__(self):
            return self

        def __iter__(self):
            return self

        def __next__(self):
            value = next(self.inner)
            consumed.append(value.name)
            return value

        def __exit__(self, *args):
            self.inner.close()
            closed.append(True)

    monkeypatch.setattr(os, "scandir", Scan)
    service = FSService(str(tmp_path))
    service.MAX_DIRECTORY_SCAN_ENTRIES = 3
    with pytest.raises(ValueError, match="scan limit"):
        service._list_resolved(tmp_path)
    assert len(consumed) == 4
    assert closed == [True]


def test_directory_scan_counts_hidden_entries_and_preserves_sorted_output(tmp_path):
    from soulacp.services.fs_service import FSService

    for name in ["b", "a", ".hidden"]:
        (tmp_path / name).touch()
    service = FSService(str(tmp_path))
    assert service._list_resolved(tmp_path) == ["a", "b"]
    service.MAX_DIRECTORY_SCAN_ENTRIES = 2
    with pytest.raises(ValueError, match="scan limit"):
        service._list_resolved(tmp_path)
