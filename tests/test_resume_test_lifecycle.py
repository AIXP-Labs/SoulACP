"""Deterministic lifecycle regressions for both opt-in integration tests."""

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("outcome", ["ok", "unavailable", "protocol_error", "connect_error", "prompt_error"])
def test_resume_test_releases_owner_and_always_closes_receiver(monkeypatch, provider, outcome):
    import soulacp
    from soulacp.adapters import claude_client, codex_client
    from soulacp.adapters.base_client import RPCError

    source = Path(__file__).parent / ("test_integration_codex.py" if provider == "codex" else "test_integration.py")
    spec = importlib.util.spec_from_file_location("resume_integration_under_test", source)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    events = []
    owner = SimpleNamespace(is_connected=True)
    error = RPCError(-32600, "synthetic-protocol-rejection")
    connect_error = ConnectionError("synthetic-connect-failure")
    prompt_error = RuntimeError("synthetic-prompt-failure")

    class FirstClient:
        async def query(self, prompt):
            events.append("query")
            if outcome == "prompt_error":
                raise prompt_error
            return "hi"

    class FakePool:
        def __init__(self, config, client_class):
            pass

        @asynccontextmanager
        async def acquire(self):
            events.append("acquire")
            try:
                yield FirstClient(), "synthetic-session"
            finally:
                events.append("release-lease")

        async def close_all(self):
            events.append("close-source-pool")
            owner.is_connected = False

    class SecondClient:
        def __init__(self, config):
            events.append("create-receiver")

        async def connect(self):
            events.append("connect-receiver")
            if outcome == "connect_error":
                raise connect_error

        async def resume(self, session_id):
            events.append("resume")
            assert not owner.is_connected, "Old connection still owns the session"
            if outcome == "protocol_error":
                raise error
            return outcome != "unavailable"

        async def disconnect(self):
            events.append("disconnect-receiver")

    monkeypatch.setattr(soulacp, "ACPConnectionPool", FakePool)
    target_module = codex_client if provider == "codex" else claude_client
    monkeypatch.setattr(target_module, "CodexACPClient" if provider == "codex" else "ClaudeACPClient", SecondClient)
    expected = {"protocol_error": error, "connect_error": connect_error, "prompt_error": prompt_error}.get(outcome)
    if expected is None:
        original.test_session_resume(SimpleNamespace(provider=provider), SecondClient)
    else:
        with pytest.raises(type(expected)) as caught:
            original.test_session_resume(SimpleNamespace(provider=provider), SecondClient)
        assert caught.value is expected
    assert not owner.is_connected
    assert events.count("close-source-pool") >= 1
    if outcome == "prompt_error":
        assert "create-receiver" not in events
    else:
        assert events.index("close-source-pool") < events.index("create-receiver")
        assert events.count("disconnect-receiver") == 1
