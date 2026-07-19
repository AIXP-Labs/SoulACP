"""Offline command-contract tests for thin ACP adapters."""

from __future__ import annotations

import importlib
import os
from typing import Any, cast

import pytest

from soulacp.adapters import claude_client as claude_module
from soulacp.adapters import codex_client as codex_module
from soulacp.adapters import cursor_client as cursor_module
from soulacp.adapters import gemini_client as gemini_module
from soulacp.adapters.claude_client import ClaudeACPClient
from soulacp.adapters.codex_client import CodexACPClient
from soulacp.adapters.cursor_client import CursorCLIClient
from soulacp.adapters.gemini_client import GeminiACPClient
from soulacp.config import ACPConfig, resolve_client_class
from soulacp.registry import list_agents

NATIVE_ACP_PROVIDERS = tuple(agent.name for agent in list_agents() if agent.name != "cursor-cli")

LOAD_SESSION_PROVIDERS = {"gemini", "opencode", "openclaw", "codex"}


@pytest.mark.parametrize("binary", ["claude-agent-acp", "claude-code-acp"])
def test_claude_command_uses_only_an_acp_bridge(monkeypatch: pytest.MonkeyPatch, binary: str) -> None:
    monkeypatch.setattr(claude_module, "find_claude_binary", lambda: binary)
    client = ClaudeACPClient(ACPConfig(provider="claude", model="claude-acp/default"))

    assert client._get_acp_command() == [binary]


@pytest.mark.parametrize(
    ("module_name", "class_name", "finder_name", "provider", "binary", "expected"),
    [
        ("amp_client", "AmpACPClient", "find_amp_binary", "amp", "amp-acp", ["amp-acp"]),
        ("auggie_client", "AuggieACPClient", "find_auggie_binary", "auggie", "auggie", ["auggie", "--acp"]),
        (
            "autohand_client",
            "AutohandACPClient",
            "find_autohand_binary",
            "autohand",
            "autohand-acp",
            ["autohand-acp"],
        ),
        ("cline_client", "ClineACPClient", "find_cline_binary", "cline", "cline", ["cline", "--acp"]),
        (
            "codebuddy_client",
            "CodebuddyACPClient",
            "find_codebuddy_binary",
            "codebuddy",
            "codebuddy-code",
            ["codebuddy-code", "--acp"],
        ),
        (
            "copilot_client",
            "CopilotACPClient",
            "find_copilot_binary",
            "copilot",
            "copilot",
            ["copilot", "--acp"],
        ),
        (
            "copilot_ls_client",
            "CopilotLSACPClient",
            "find_copilot_ls_binary",
            "copilot-ls",
            "copilot-language-server",
            ["copilot-language-server", "--acp"],
        ),
        (
            "corust_client",
            "CorustACPClient",
            "find_corust_binary",
            "corust",
            "corust-agent-acp",
            ["corust-agent-acp"],
        ),
        ("crow_client", "CrowACPClient", "find_crow_binary", "crow", "crow-cli", ["crow-cli", "acp"]),
        (
            "cursor_acp_client",
            "CursorACPClient",
            "find_cursor_binary",
            "cursor",
            "cursor-agent",
            ["cursor-agent", "acp"],
        ),
        (
            "deepagents_client",
            "DeepAgentsACPClient",
            "find_deepagents_binary",
            "deepagents",
            "deepagents-acp",
            ["deepagents-acp"],
        ),
        (
            "droid_client",
            "DroidACPClient",
            "find_droid_binary",
            "droid",
            "droid",
            ["droid", "exec", "--output-format", "acp-daemon"],
        ),
        (
            "fastagent_client",
            "FastAgentACPClient",
            "find_fastagent_binary",
            "fastagent",
            "fast-agent-acp",
            ["fast-agent-acp", "-x"],
        ),
        ("goose_client", "GooseACPClient", "find_goose_binary", "goose", "goose", ["goose", "acp"]),
        ("junie_client", "JunieACPClient", "find_junie_binary", "junie", "junie", ["junie", "--acp=true"]),
        ("kilo_client", "KiloACPClient", "find_kilo_binary", "kilo", "kilo", ["kilo", "acp"]),
        ("kimi_client", "KimiACPClient", "find_kimi_binary", "kimi", "kimi", ["kimi", "acp"]),
        (
            "minion_client",
            "MinionACPClient",
            "find_minion_binary",
            "minion",
            "minion-code",
            ["minion-code", "acp"],
        ),
        ("nova_client", "NovaACPClient", "find_nova_binary", "nova", "nova", ["nova", "acp"]),
        ("pi_client", "PiACPClient", "find_pi_binary", "pi", "pi-acp", ["pi-acp"]),
        ("qoder_client", "QoderACPClient", "find_qoder_binary", "qoder", "qodercli", ["qodercli", "--acp"]),
        (
            "qwen_client",
            "QwenACPClient",
            "find_qwen_binary",
            "qwen",
            "qwen-code",
            ["qwen-code", "--acp", "--experimental-skills"],
        ),
        (
            "stakpak_client",
            "StakpakACPClient",
            "find_stakpak_binary",
            "stakpak",
            "stakpak",
            ["stakpak", "acp"],
        ),
        ("vibe_client", "VibeACPClient", "find_vibe_binary", "vibe", "vibe-acp", ["vibe-acp"]),
    ],
)
def test_thin_adapter_command_matches_registry_contract(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    class_name: str,
    finder_name: str,
    provider: str,
    binary: str,
    expected: list[str],
) -> None:
    module = importlib.import_module(f"soulacp.adapters.{module_name}")
    monkeypatch.setattr(module, finder_name, lambda: binary)
    client_class = getattr(module, class_name)
    client = client_class(ACPConfig(provider=provider, model=f"{provider}-acp/default"))

    assert client._get_acp_command() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", NATIVE_ACP_PROVIDERS)
async def test_every_native_adapter_establishes_and_resumes_a_session(provider: str) -> None:
    client_class = cast(Any, resolve_client_class(provider))
    client = client_class(ACPConfig(provider=provider, model=f"{provider}-acp/default", cwd="C:/workspace"))
    calls: list[tuple[str, dict, float | None]] = []
    notifications: list[tuple[str, dict | None]] = []

    async def fake_rpc(method: str, params: dict, timeout: float | None = None):
        calls.append((method, params, timeout))
        if method == "initialize":
            return {"protocolVersion": 1, "authMethods": []}
        if method == "session/new":
            return {"sessionId": f"{provider}-new"}
        if method == "session/list":
            return {"sessions": [{"sessionId": f"{provider}-resume", "cwd": "C:/workspace"}]}
        return {}

    async def fake_notification(method: str, params: dict | None = None) -> None:
        notifications.append((method, params))

    client._rpc = fake_rpc
    client._send_notification = fake_notification

    await client._initialize()
    assert client.session_id == f"{provider}-new"
    assert "initialize" in {method for method, _params, _timeout in calls}
    assert "session/new" in {method for method, _params, _timeout in calls}
    assert "session/set_model" not in {method for method, _params, _timeout in calls}

    calls.clear()
    if provider in LOAD_SESSION_PROVIDERS:
        client.agent_capabilities = {"loadSession": True}
    else:
        client.agent_capabilities = {"sessionCapabilities": {"resume": {}}}
    assert await client.resume(f"{provider}-resume") is True
    assert client.session_id == f"{provider}-resume"
    expected_method = "session/load" if provider in LOAD_SESSION_PROVIDERS else "session/resume"
    assert expected_method in {method for method, _params, _timeout in calls}


def test_codex_command_uses_configured_child_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_module, "find_codex_binary", lambda: "codex-acp")
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "low")
    monkeypatch.setenv("CODEX_SANDBOX_MODE", "read-only")
    monkeypatch.setenv("CODEX_APPROVAL_POLICY", "untrusted")
    monkeypatch.setenv("CODEX_NETWORK_ACCESS", "false")
    client = CodexACPClient(
        ACPConfig(
            provider="codex",
            model="codex-acp/default",
            extra_env={
                "CODEX_REASONING_EFFORT": "high",
                "CODEX_SANDBOX_MODE": "workspace-write",
                "CODEX_APPROVAL_POLICY": "never",
                "CODEX_NETWORK_ACCESS": "true",
            },
        )
    )

    assert client._get_acp_command() == [
        "codex-acp",
        "-c",
        'model_reasoning_effort="high"',
        "-c",
        'sandbox_mode="workspace-write"',
        "-c",
        'approval_policy="never"',
        "-c",
        "sandbox_workspace_write.network_access=true",
    ]


def test_default_model_does_not_force_gemini_or_cursor_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gemini_module, "find_gemini_binary", lambda: "gemini")
    monkeypatch.setattr(cursor_module, "find_cursor_binary", lambda: "cursor-agent")

    gemini = GeminiACPClient(ACPConfig(provider="gemini", model="gemini-acp/default"))
    cursor = CursorCLIClient(ACPConfig(provider="cursor-cli", model="cursor-cli/default"))

    assert gemini._get_acp_command() == ["gemini", "--acp"]
    assert cursor._build_command("ignored", stream=False)[0] == [
        "cursor-agent",
        "-p",
        "--output-format",
        "json",
        "--force",
    ]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gemini-acp/default", ""),
        ("gemini/default", ""),
        ("gemini-acp/DEFAULT", ""),
        ("default", ""),
        ("other/default", "other/default"),
        ("gemini-acp/gemini-3-flash-preview", "gemini-3-flash-preview"),
    ],
)
def test_configured_model_id_treats_only_provider_default_as_a_sentinel(model: str, expected: str) -> None:
    client = GeminiACPClient(ACPConfig(provider="gemini", model=model))

    assert client._configured_model_id() == expected


def test_codex_command_does_not_read_parent_in_minimal_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_module, "find_codex_binary", lambda: "codex-acp")
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "xhigh")
    monkeypatch.setenv("CODEX_SANDBOX_MODE", "danger-full-access")
    monkeypatch.setenv("CODEX_APPROVAL_POLICY", "never")
    monkeypatch.setenv("CODEX_NETWORK_ACCESS", "true")
    client = CodexACPClient(
        ACPConfig(
            provider="codex",
            model="codex-acp/default",
            inherit_parent_env=False,
        )
    )

    assert client._get_acp_command() == [
        "codex-acp",
        "-c",
        'model_reasoning_effort="medium"',
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows environment names are case-insensitive")
def test_codex_command_matches_case_insensitive_explicit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_module, "find_codex_binary", lambda: "codex-acp")
    client = CodexACPClient(
        ACPConfig(
            provider="codex",
            model="codex-acp/default",
            inherit_parent_env=False,
            extra_env={"codex_reasoning_effort": "high"},
        )
    )

    assert client._get_acp_command() == [
        "codex-acp",
        "-c",
        'model_reasoning_effort="high"',
    ]


def test_codex_extra_args_remain_highest_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_module, "find_codex_binary", lambda: "codex-acp")
    client = CodexACPClient(
        ACPConfig(
            provider="codex",
            model="codex-acp/default",
            extra_args=["-c", 'model_reasoning_effort="xhigh"'],
            extra_env={"CODEX_REASONING_EFFORT": "low"},
        )
    )

    assert client._get_acp_command() == ["codex-acp"]
