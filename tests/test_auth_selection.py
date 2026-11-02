"""Explicit authentication is bound, non-replaying and redacted."""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from dataclasses import replace
from pathlib import Path

import pytest

from soulacp import (
    ACPAuthenticationError,
    ACPConfig,
    ACPConnectionPool,
    ManagedSession,
    list_agents,
    resolve_client_class,
)
from soulacp.adapters.claude_client import ClaudeACPClient
from soulacp.adapters.cursor_client import CursorCLIClient


class AuthPeer(ClaudeACPClient):
    scenario = "ok"
    instances = 0

    def __init__(self, config):
        super().__init__(config)
        type(self).instances += 1

    def _get_acp_command(self):
        return [
            sys.executable,
            "-u",
            str(Path(__file__).parent / "fixtures" / "auth_stdio_agent.py"),
            str(Path(self.config.cwd) / "journal.jsonl"),
            self.scenario,
        ]


def config(tmp_path, **overrides):
    return ACPConfig(
        provider="claude",
        model="claude-acp/default",
        cwd=str(tmp_path),
        pool_keepalive_interval=0,
        retry_base_delay=0,
        **overrides,
    )


def journal(tmp_path):
    path = tmp_path / "journal.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


@pytest.mark.parametrize("bad", ["", 1, True, [], "synthetic-private-config\x00", "synthetic-private-config\ud800"])
def test_invalid_choice_is_redacted(tmp_path, bad):
    with pytest.raises(ValueError) as caught:
        config(tmp_path, auth_method_id=bad)
    assert "auth_method_id" in str(caught.value)
    assert "synthetic-private-config" not in "".join(traceback.format_exception(caught.type, caught.value, caught.tb))


def test_provider_binding_and_repr(tmp_path):
    cfg = config(tmp_path, auth_method_id="synthetic-private-method")
    assert "synthetic-private-method" not in repr(cfg)
    assert replace(cfg).auth_method_id == "synthetic-private-method"
    with pytest.raises(ValueError):
        replace(cfg, provider="gemini")
    with pytest.raises(ValueError):
        ManagedSession(provider="gemini", model="default", config=cfg)


@pytest.mark.parametrize("scenario", ["ok", "reverse"])
@pytest.mark.asyncio
async def test_real_stdio_selects_only_requested_method(tmp_path, scenario):
    class Peer(AuthPeer):
        pass

    Peer.scenario = scenario
    cfg = config(tmp_path, auth_method_id="selected")
    async with ACPConnectionPool(cfg, Peer) as pool:
        cfg.auth_method_id = "first"
        async with pool.acquire() as (client, sid):
            assert await client.query("1+2") == "3"
            client.config.auth_method_id = "first"
            with pytest.raises(Exception) as caught:
                await client.authenticate("first")
            assert type(caught.value).__name__ == "ACPAuthenticationError"
        async with pool.acquire(session_id=sid) as (client, _):
            assert await client.query("already computed") == "3"
    calls = journal(tmp_path)
    assert [row["params"]["methodId"] for row in calls if row["method"] == "authenticate"] == ["selected"]
    assert [row["method"] for row in calls][:3] == ["initialize", "authenticate", "session/new"]


@pytest.mark.parametrize("scenario,choice", [("none", "selected"), ("duplicate", "selected"), ("ok", "missing")])
@pytest.mark.asyncio
async def test_ambiguous_or_missing_choice_never_authenticates(tmp_path, scenario, choice):
    class Peer(AuthPeer):
        pass

    Peer.scenario = scenario
    async with ACPConnectionPool(config(tmp_path, auth_method_id=choice), Peer) as pool:
        with pytest.raises(Exception) as caught:
            async with pool.acquire():
                pytest.fail("must not acquire")
        assert type(caught.value).__name__ == "ACPAuthenticationError"
    assert [row["method"] for row in journal(tmp_path)] == ["initialize"]


@pytest.mark.parametrize("scenario", ["reject", "disconnect", "timeout"])
@pytest.mark.asyncio
async def test_auth_failure_never_retries_or_falls_back(tmp_path, monkeypatch, scenario):
    class Peer(AuthPeer):
        pass

    Peer.scenario = scenario
    Peer.instances = 0
    monkeypatch.setattr("soulacp.session.resolve_client_class", lambda provider: Peer)
    cfg = config(tmp_path, auth_method_id="selected", enable_fallback=True, timeout_connect=1.5)
    async with ManagedSession(provider="claude", model=cfg.model, config=cfg) as session:
        with pytest.raises(Exception) as caught:
            await session.query("must not run")
        assert type(caught.value).__name__ == "ACPAuthenticationError"
        rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
        assert "synthetic-private-auth-message" not in rendered
        assert len(session._pools) == 1
    assert Peer.instances == 1
    assert [row["method"] for row in journal(tmp_path)] == ["initialize", "authenticate"]


@pytest.mark.asyncio
async def test_legacy_mode_still_skips_claude_auth(tmp_path):
    async with ACPConnectionPool(config(tmp_path), AuthPeer) as pool, pool.acquire() as (client, _):
        assert await client.query("hello") == "3"
    assert "authenticate" not in [row["method"] for row in journal(tmp_path)]


def test_legacy_cursor_rejects_explicit_configuration(tmp_path):
    with pytest.raises(Exception) as caught:
        CursorCLIClient(config(tmp_path, auth_method_id="selected"))
    assert type(caught.value).__name__ == "ACPAuthenticationError"


@pytest.mark.asyncio
async def test_auth_cancellation_cleans_up_without_retry(tmp_path):
    class Peer(AuthPeer):
        scenario = "timeout"

    pool = ACPConnectionPool(config(tmp_path, auth_method_id="selected", timeout_connect=10), Peer)

    async def acquire():
        async with pool.acquire():
            pytest.fail("must not acquire")

    task = asyncio.create_task(acquire())
    try:
        for _ in range(200):
            if any(row["method"] == "authenticate" for row in journal(tmp_path)):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("peer did not reach authentication")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await pool.close_all()
    assert pool.get_stats()["total_open"] == 0
    assert [row["method"] for row in journal(tmp_path)] == ["initialize", "authenticate"]


@pytest.mark.parametrize("provider", [agent.name for agent in list_agents() if agent.name != "cursor-cli"])
@pytest.mark.asyncio
async def test_every_native_adapter_uses_explicit_selection(tmp_path, provider):
    base = resolve_client_class(provider)

    class Peer(base):
        def _get_acp_command(self):
            return [
                sys.executable,
                "-u",
                str(Path(__file__).parent / "fixtures" / "auth_stdio_agent.py"),
                str(tmp_path / "journal.jsonl"),
                "ok",
            ]

    cfg = ACPConfig(
        provider=provider,
        model=f"{provider}-acp/default",
        cwd=str(tmp_path),
        auth_method_id="selected",
        pool_keepalive_interval=0,
        retry_base_delay=0,
    )
    async with ACPConnectionPool(cfg, Peer) as pool, pool.acquire() as (client, _):
        assert await client.query("1+2") == "3"
    calls = journal(tmp_path)
    assert [row["params"]["methodId"] for row in calls if row["method"] == "authenticate"] == ["selected"]
    assert [row["method"] for row in calls][:3] == ["initialize", "authenticate", "session/new"]


@pytest.mark.parametrize("provider", ["gemini", "kimi", "amp", "opencode"])
@pytest.mark.asyncio
async def test_rejection_for_distinct_authentication_paths(tmp_path, provider):
    base = resolve_client_class(provider)

    class Peer(base):
        def _get_acp_command(self):
            return [
                sys.executable,
                "-u",
                str(Path(__file__).parent / "fixtures" / "auth_stdio_agent.py"),
                str(tmp_path / "journal.jsonl"),
                "reject",
            ]

    cfg = ACPConfig(
        provider=provider,
        model=f"{provider}-acp/default",
        cwd=str(tmp_path),
        auth_method_id="selected",
        pool_keepalive_interval=0,
        retry_base_delay=0,
    )
    async with ACPConnectionPool(cfg, Peer) as pool:
        with pytest.raises(ACPAuthenticationError) as caught:
            async with pool.acquire():
                pytest.fail("rejected authentication must not acquire")
        assert caught.value.cause is not None
        assert "synthetic-private-auth-message" not in "".join(
            traceback.format_exception(caught.type, caught.value, caught.tb)
        )
    assert [row["method"] for row in journal(tmp_path)] == ["initialize", "authenticate"]


@pytest.mark.asyncio
async def test_before_authentication_retry_remains_bounded_without_fallback(tmp_path, monkeypatch):
    class Peer(AuthPeer):
        attempts = 0

        async def connect(self):
            type(self).attempts += 1
            raise ConnectionError("synthetic pre-auth connection failure")

    monkeypatch.setattr("soulacp.session.resolve_client_class", lambda provider: Peer)
    cfg = config(tmp_path, auth_method_id="selected", enable_fallback=True)
    async with ManagedSession(provider=cfg.provider, model=cfg.model, config=cfg) as session:
        with pytest.raises(ConnectionError):
            await session.query("must not run")
        assert len(session._pools) == 1
        with pytest.raises(ValueError):
            session._get_pool("gemini", "gemini-acp/default")
    assert Peer.attempts == cfg.max_retries
    assert journal(tmp_path) == []


def test_auth_error_cannot_retry_and_env_does_not_select_auth(tmp_path, monkeypatch):
    from soulacp.retry import is_retryable

    monkeypatch.setenv("ACP_AUTH_METHOD_ID", "synthetic-env-secret")
    cfg = ACPConfig.from_env(cwd=str(tmp_path))
    assert cfg.auth_method_id is None
    assert not is_retryable(ACPAuthenticationError())
