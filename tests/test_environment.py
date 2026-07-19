"""Tests for provider and terminal subprocess environment isolation."""

import os

import pytest


def test_minimal_subprocess_environment_excludes_unrelated_parent_secrets(monkeypatch, tmp_path):
    from soulacp.environment import build_subprocess_environment

    trusted_path = str(tmp_path / "trusted-bin")
    monkeypatch.setenv("PATH", f".{os.pathsep}{trusted_path}")
    monkeypatch.setenv("LC_TEST", "locale")
    monkeypatch.setenv("SOULACP_TEST_PARENT_SECRET", "do-not-inherit")

    environment = build_subprocess_environment(False)

    assert environment["PATH"] == trusted_path
    assert environment["LC_TEST"] == "locale"
    assert "SOULACP_TEST_PARENT_SECRET" not in environment

    monkeypatch.setenv("PATH", f".{os.pathsep}")
    fallback_path = build_subprocess_environment(False)["PATH"]
    assert fallback_path
    assert all(entry and os.path.isabs(entry) for entry in fallback_path.split(os.pathsep))


def test_default_subprocess_environment_preserves_parent_compatibility(monkeypatch):
    from soulacp.environment import build_subprocess_environment

    monkeypatch.setenv("SOULACP_TEST_PROVIDER_TOKEN", "inherited")

    assert build_subprocess_environment(True)["SOULACP_TEST_PROVIDER_TOKEN"] == "inherited"


@pytest.mark.skipif(os.name != "nt", reason="Windows environment names are case-insensitive")
def test_windows_environment_override_removes_case_variant():
    from soulacp.environment import get_subprocess_environment_value, update_subprocess_environment

    environment = {"Path": "old", "TOKEN": "old"}

    update_subprocess_environment(environment, {"PATH": "new", "token": "new"})

    assert environment == {"PATH": "new", "token": "new"}
    assert get_subprocess_environment_value(environment, "path") == "new"
    assert get_subprocess_environment_value(environment, "TOKEN") == "new"


@pytest.mark.asyncio
async def test_cursor_minimal_environment_keeps_explicit_overrides(monkeypatch, tmp_path):
    from soulacp.adapters import cursor_client as cursor_module
    from soulacp.adapters.cursor_client import CursorCLIClient
    from soulacp.config import ACPConfig

    captured = {}

    async def fake_spawn(*_command, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(cursor_module.asyncio, "create_subprocess_exec", fake_spawn)
    monkeypatch.setenv("SOULACP_TEST_PARENT_SECRET", "do-not-inherit")
    client = CursorCLIClient(
        ACPConfig(
            provider="cursor-cli",
            model="cursor-cli/default",
            cwd=str(tmp_path),
            inherit_parent_env=False,
            extra_env={"CURSOR_EXPLICIT_TOKEN": "explicit"},
        )
    )

    await client._spawn(["cursor-agent"])

    environment = captured["env"]
    assert environment["CURSOR_EXPLICIT_TOKEN"] == "explicit"
    assert "SOULACP_TEST_PARENT_SECRET" not in environment


def test_terminal_minimal_environment_keeps_request_overrides(monkeypatch, tmp_path):
    from soulacp.services.terminal_service import TerminalService

    monkeypatch.setenv("SOULACP_TEST_PARENT_SECRET", "do-not-inherit")
    service = TerminalService(str(tmp_path), inherit_parent_env=False)

    environment = service._build_env([{"name": "EXPLICIT", "value": "value"}])

    assert environment["EXPLICIT"] == "value"
    assert "SOULACP_TEST_PARENT_SECRET" not in environment
