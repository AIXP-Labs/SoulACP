"""Model selection via session/set_config_option (doc 34 v2)."""

from __future__ import annotations

import pytest

from soulacp.adapters.base_client import RPCError
from soulacp.adapters.claude_client import ClaudeACPClient
from soulacp.config import ACPConfig


def make_client() -> ClaudeACPClient:
    client = ClaudeACPClient(ACPConfig(provider="claude", model="claude-acp/claude-fable-5"))
    client.session_id = "sess-1"
    return client


def model_options_res(values: list[str], *, current: str = "default") -> dict:
    return {
        "sessionId": "sess-1",
        "configOptions": [
            {
                "id": "mode",
                "name": "Mode",
                "category": "mode",
                "type": "select",
                "currentValue": "default",
                "options": [{"value": "default", "name": "Default"}],
            },
            {
                "id": "model",
                "name": "Model",
                "category": "model",
                "type": "select",
                "currentValue": current,
                "options": [{"value": value, "name": value} for value in values],
            },
        ],
    }


def capture_model_options(client: ClaudeACPClient, values: list[str], *, current: str = "default") -> None:
    client._capture_session_config("session/new", model_options_res(values, current=current))


@pytest.mark.asyncio
async def test_exact_match_sets_config_option():
    client = make_client()
    values = ["default", "claude-fable-5", "claude-opus-4-8"]
    capture_model_options(client, values)
    calls: list[tuple] = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params))
        return model_options_res(values, current="claude-fable-5")

    client._rpc = fake_rpc  # type: ignore[method-assign]
    ok = await client._select_model_config_option("claude-fable-5")
    assert ok is True
    assert calls == [
        (
            "session/set_config_option",
            {"sessionId": "sess-1", "configId": "model", "value": "claude-fable-5"},
        )
    ]


@pytest.mark.asyncio
async def test_unique_substring_match():
    client = make_client()
    values = ["default", "claude-sonnet-5", "claude-opus-4-8"]
    capture_model_options(client, values)
    calls: list[tuple] = []

    async def fake_rpc(method, params, timeout=None):
        calls.append((method, params))
        return model_options_res(values, current="claude-sonnet-5")

    client._rpc = fake_rpc  # type: ignore[method-assign]
    ok = await client._select_model_config_option("sonnet")
    assert ok is True
    assert calls[0][1]["value"] == "claude-sonnet-5"


@pytest.mark.asyncio
async def test_current_model_and_current_alias_skip_rpc():
    client = make_client()
    capture_model_options(client, ["default", "opus[1m]", "sonnet"], current="opus[1m]")

    async def fail_rpc(method, params, timeout=None):  # pragma: no cover - must not run
        raise AssertionError("RPC must not be called when the configured model is already current")

    client._rpc = fail_rpc  # type: ignore[method-assign]
    assert await client._select_model_config_option("opus[1m]") is True
    assert await client._select_model_config_option("opus") is True


@pytest.mark.asyncio
async def test_no_match_degrades_without_rpc():
    client = make_client()
    capture_model_options(client, ["default", "claude-opus-4-8", "claude-haiku-4-5"])

    async def fail_rpc(method, params, timeout=None):  # pragma: no cover - must not run
        raise AssertionError("RPC must not be called on no-match")

    client._rpc = fail_rpc  # type: ignore[method-assign]
    ok = await client._select_model_config_option("claude-fable-5")
    assert ok is False


@pytest.mark.asyncio
async def test_ambiguous_substring_degrades_without_rpc():
    client = make_client()
    capture_model_options(client, ["default", "claude-sonnet-5", "claude-sonnet-5-long"])

    async def fail_rpc(method, params, timeout=None):  # pragma: no cover - must not run
        raise AssertionError("RPC must not be called on an ambiguous alias")

    client._rpc = fail_rpc  # type: ignore[method-assign]
    assert await client._select_model_config_option("sonnet") is False


@pytest.mark.asyncio
async def test_missing_config_options_degrades():
    client = make_client()
    ok = await client._select_model_config_option("claude-fable-5")
    assert ok is False


@pytest.mark.asyncio
async def test_rpc_rejection_degrades():
    client = make_client()
    capture_model_options(client, ["default", "claude-fable-5"])

    async def reject_rpc(method, params, timeout=None):
        raise RPCError(code=-32603, message="boom", method=method, msg_id=9)

    client._rpc = reject_rpc  # type: ignore[method-assign]
    ok = await client._select_model_config_option("claude-fable-5")
    assert ok is False


@pytest.mark.asyncio
async def test_initialize_applies_the_configured_model_after_session_new():
    client = make_client()
    client.session_id = None
    selected: list[str] = []

    async def fake_rpc(method, params, timeout=None):
        if method == "session/new":
            return {"sessionId": "sess-1"}
        return {}

    async def capture_selection(model_id: str) -> bool:
        selected.append(model_id)
        return True

    client._rpc = fake_rpc  # type: ignore[method-assign]
    client._select_model_config_option = capture_selection  # type: ignore[method-assign]
    await client._initialize()

    assert client.session_id == "sess-1"
    assert selected == ["claude-fable-5"]
