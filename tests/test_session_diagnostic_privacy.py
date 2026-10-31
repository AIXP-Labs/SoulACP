"""Public control and parser errors reject inputs without exposing their contents."""

import asyncio
import json
import traceback
from hashlib import sha256

import pytest

from soulacp import ACPConfig
from soulacp._json import strict_json_loads
from soulacp.adapters import openclaw_client
from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.adapters.opencode_client import OpenCodeACPClient
from soulacp.cache import FileCache
from soulacp.errors import ACPProtocolError, ACPUnsupportedCapabilityError
from soulacp.privacy import exception_summary

MARKER = "SYNTHETIC_SESSION_DIAGNOSTIC_VALUE"


class LocalClient(ACPClientBase):
    def _get_acp_command(self):
        raise AssertionError("No real provider may start")

    async def _initialize(self):
        raise AssertionError("No real provider may initialize")


def assert_safe(error):
    assert MARKER not in str(error)
    assert MARKER not in repr(error)
    assert MARKER not in exception_summary(error, include_sensitive=False)
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert MARKER not in rendered


@pytest.fixture(autouse=True)
def no_sensitive_opt_in(monkeypatch):
    monkeypatch.delenv("ACP_LOG_SENSITIVE_DATA", raising=False)


@pytest.mark.parametrize("case", ["unknown-option", "boolean-type", "select-type", "select-value", "unknown-mode"])
async def test_session_control_errors_keep_types_and_hide_inputs(case):
    client = LocalClient(ACPConfig())
    client.session_id = "synthetic-session"
    client.session_config_options = ACPClientBase._validate_config_options(
        [
            dict(id=MARKER, name="Boolean", type="boolean", currentValue=True),
            dict(
                id="select",
                name="Select",
                type="select",
                currentValue="safe",
                options=[dict(value="safe", name="Safe")],
            ),
            dict(
                id=MARKER + "-select",
                name="Select",
                type="select",
                currentValue="safe",
                options=[dict(value="safe", name="Safe")],
            ),
        ],
        source="session/new",
    )
    client.session_mode_state = ACPClientBase._validate_session_modes(
        dict(currentModeId="safe", availableModes=[dict(id="safe", name="Safe")]), source="session/new"
    )

    async def never_rpc(*args, **kwargs):
        raise AssertionError("Rejected input must not send an RPC")

    client._rpc = never_rpc
    error_type = (
        ACPUnsupportedCapabilityError
        if case == "unknown-option"
        else (TypeError if case in {"boolean-type", "select-type"} else ValueError)
    )
    with pytest.raises(error_type) as caught:
        if case == "unknown-option":
            await client.set_config_option(MARKER + "-unknown", True)
        elif case == "boolean-type":
            await client.set_config_option(MARKER, "invalid")
        elif case == "select-type":
            await client.set_config_option(MARKER + "-select", True)
        elif case == "select-value":
            await client.set_config_option("select", MARKER)
        else:
            await client.set_mode(MARKER)
    if case == "unknown-option":
        assert caught.value.capability == "session config option " + MARKER + "-unknown"
    assert_safe(caught.value)


@pytest.mark.parametrize("kind", ["boolean", "select", "mode"])
async def test_valid_controls_preserve_exact_wire_and_result(kind):
    client = LocalClient(ACPConfig())
    client.session_id = "synthetic-session"
    option = dict(
        id="choice",
        type=kind,
        currentValue=True if kind == "boolean" else "safe",
        options=[dict(value="safe", name="Safe")],
    )
    client.session_config_options = [option]
    client.session_mode_state = dict(currentModeId="old", availableModes=[dict(id="safe", name="Safe")])
    calls = []

    async def rpc(method, params, timeout=None):
        calls.append((method, params))
        return {}

    client._rpc = rpc
    if kind == "mode":
        result = await client.set_mode("safe")
        assert result["currentModeId"] == "safe"
        assert calls == [("session/set_mode", dict(sessionId="synthetic-session", modeId="safe"))]
    else:
        value = True if kind == "boolean" else "safe"
        result = await client.set_config_option("choice", value)
        assert result == [option] and result is not client.session_config_options
        expected = dict(sessionId="synthetic-session", configId="choice", value=value)
        if kind == "boolean":
            expected["type"] = "boolean"
        assert calls == [("session/set_config_option", expected)]


@pytest.mark.parametrize("boundary", ["mcp", "gateway"])
@pytest.mark.parametrize("bad_shape", ["port", "bracket-host"])
def test_url_parse_chains_hide_values(boundary, bad_shape, monkeypatch):
    scheme = "https" if boundary == "mcp" else "wss"
    suffix = "example.invalid:" + MARKER if bad_shape == "port" else "[" + MARKER + "]/"
    url = scheme + "://" + suffix
    monkeypatch.setattr(openclaw_client, "find_openclaw_binary", lambda: "/synthetic/openclaw")
    with pytest.raises(ValueError) as caught:
        if boundary == "mcp":
            ACPConfig(mcp_servers=[dict(type="http", name="safe", url=url, headers=[])])
        else:
            client = openclaw_client.OpenClawACPClient(ACPConfig(provider="openclaw", extra_env={"OPENCLAW_URL": url}))
            client._get_acp_command()
    assert_safe(caught.value)


@pytest.mark.parametrize("transport", ["http", "sse"])
@pytest.mark.parametrize("host", ["example.invalid:443/mcp", "[::1]:443/mcp"])
def test_valid_mcp_urls_are_not_rewritten(transport, host):
    url = "https://" + host
    config = ACPConfig(mcp_servers=[dict(type=transport, name="safe", url=url, headers=[])])
    assert config.mcp_servers[0]["url"] == url


@pytest.mark.parametrize("url", ["ws://example.invalid:80/acp", "wss://[::1]:443/acp"])
def test_valid_gateway_urls_are_not_rewritten(url):
    assert openclaw_client._validate_gateway_url(url) == url


@pytest.mark.parametrize("boundary", ["mcp", "gateway"])
@pytest.mark.parametrize("url_shape", ["credentials", "wrong-scheme", "whitespace", "backslash"])
def test_url_security_rejections_are_retained(boundary, url_shape):
    scheme = "https" if boundary == "mcp" else "wss"
    values = dict(
        credentials=scheme + "://user:synthetic@example.invalid/",
        **{
            "wrong-scheme": "file:///synthetic",
            "whitespace": " " + scheme + "://example.invalid/",
            "backslash": scheme + "://example.invalid/\\synthetic",
        },
    )
    with pytest.raises(ValueError):
        if boundary == "mcp":
            ACPConfig(mcp_servers=[dict(type="http", name="safe", url=values[url_shape], headers=[])])
        else:
            openclaw_client._validate_gateway_url(values[url_shape])


def duplicate_frame():
    return "{" + json.dumps(MARKER) + ":1," + json.dumps(MARKER) + ":2}"


@pytest.mark.parametrize("boundary", ["strict-json", "protocol"])
def test_duplicate_json_keys_are_still_rejected_without_echo(boundary):
    with pytest.raises(ValueError if boundary == "strict-json" else ACPProtocolError) as caught:
        if boundary == "strict-json":
            strict_json_loads(duplicate_frame())
        else:
            ACPClientBase._parse_json(duplicate_frame())
    assert_safe(caught.value)


async def test_reader_future_and_default_logs_do_not_leak_duplicate_keys(caplog):
    client = LocalClient(ACPConfig())
    stream = asyncio.StreamReader()
    stream.feed_data((duplicate_frame() + "\n").encode())
    stream.feed_eof()

    class Process:
        stdout = stream
        returncode = 0

    client.process = Process()
    client._connected = True
    future = asyncio.get_running_loop().create_future()
    client._pending[1] = future
    client._schedule_host_cleanup = lambda: None
    client._schedule_process_cleanup = lambda: None
    await client._read_stdout()
    with pytest.raises(ACPProtocolError) as caught:
        await future
    assert_safe(caught.value)
    assert MARKER not in caplog.text
    assert not client._connected


async def test_opencode_duplicate_configuration_is_rejected_before_spawn():
    client = OpenCodeACPClient(ACPConfig(provider="opencode", extra_env={"OPENCODE_CONFIG_CONTENT": duplicate_frame()}))

    async def no_services():
        pass

    async def never_spawn(*args, **kwargs):
        raise AssertionError("Bad configuration must not spawn")

    client._prepare_host_services = no_services
    client._get_acp_command = lambda: ["/synthetic/opencode"]
    client._spawn_popen = never_spawn
    with pytest.raises(ValueError) as caught:
        await client._start_and_initialize()
    assert_safe(caught.value)


async def test_cache_rejects_duplicate_snapshot_without_writing_or_logging_it(tmp_path, caplog):
    path = tmp_path / "synthetic.json"
    path.write_text(duplicate_frame(), encoding="utf-8")
    original = sha256(path.read_bytes()).hexdigest()
    cache = FileCache(str(path))
    assert await cache.get("missing") is None
    assert MARKER not in caplog.text
    assert sha256(path.read_bytes()).hexdigest() == original
    await cache.close()


def test_valid_json_and_explicit_rpc_diagnostics_still_work():
    frame = dict(jsonrpc="2.0", id=1, result={MARKER: [1, True, None]})
    assert ACPClientBase._parse_json(json.dumps(frame)) == frame
    assert strict_json_loads(json.dumps(frame)) == frame
    error = RPCError(-32603, MARKER, data={"synthetic": MARKER})
    assert MARKER not in str(error)
    assert MARKER in error.format_diagnostic(include_sensitive=True)


def test_ordinary_missing_capability_message_is_backward_compatible():
    error = ACPUnsupportedCapabilityError("sessionCapabilities.list")
    assert error.capability == "sessionCapabilities.list"
    assert "sessionCapabilities.list" in str(error)
