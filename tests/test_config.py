"""Test ACPConfig validation and routing."""

import pytest


def test_config_defaults():
    from soulacp.config import ACPConfig

    config = ACPConfig()
    assert config.provider == "claude"
    assert config.pool_size == 5
    assert config.pool_max_connections == 8
    assert config.pool_acquire_timeout == 30
    assert config.pool_idle_timeout == 600
    assert config.pool_keepalive_interval == 300
    assert config.timeout_connect == 30
    assert config.timeout_prompt == 21600
    assert config.timeout_stream == 1800
    assert config.pool_max_age == 7200
    assert config.pool_max_uses == 50
    assert config.pool_cross_session_reuse is False
    assert config.inherit_parent_env is True
    assert config.max_retries == 3
    assert config.max_turns is None
    assert config.additional_directories == []
    assert config.mcp_servers == []


@pytest.mark.parametrize(
    ("provider", "model", "expected"),
    [
        ("claude", "claude-acp/openai/gpt-5", "openai/gpt-5"),
        ("claude", "claude/anthropic/sonnet", "anthropic/sonnet"),
        ("claude", "claude-fable-5", "claude-fable-5"),
        ("copilot-ls", "copilot-ls-acp/vendor/model", "vendor/model"),
        ("cursor-cli", "cursor-cli/openai/gpt-5", "openai/gpt-5"),
        ("gemini", "GEMINI-ACP/vendor/model", "vendor/model"),
    ],
)
def test_model_id_removes_only_the_provider_namespace(provider, model, expected):
    from soulacp.config import _model_id_for_provider

    assert _model_id_for_provider(model, provider) == expected


def test_config_validation_pool_size():
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match="pool_size"):
        ACPConfig(pool_size=0)


def test_config_validation_timeout():
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match="timeout_connect"):
        ACPConfig(timeout_connect=-1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pool_max_connections", 0),
        ("pool_acquire_timeout", 0),
        ("pool_idle_timeout", 0),
        ("pool_keepalive_interval", -1),
        ("pool_max_age", 0),
        ("pool_max_uses", 0),
        ("timeout_prompt", 0),
        ("timeout_stream", 0),
        ("pool_acquire_timeout", float("nan")),
        ("timeout_prompt", float("inf")),
        ("retry_base_delay", -1),
    ],
)
def test_config_validation_new_limits(field, value):
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match=field):
        ACPConfig(**{field: value})


def test_config_validation_idle_must_fit_hard_limit():
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match="pool_size must be <= pool_max_connections"):
        ACPConfig(pool_size=9, pool_max_connections=8)


def test_config_keepalive_zero_disables_loop():
    from soulacp.config import ACPConfig

    assert ACPConfig(pool_keepalive_interval=0).pool_keepalive_interval == 0


def test_config_copies_caller_owned_mutable_values():
    from soulacp.config import ACPConfig

    args = ["--flag"]
    env = {"TOKEN": "original"}
    config = ACPConfig(extra_args=args, extra_env=env)

    args.append("--later")
    env["TOKEN"] = "changed"

    assert config.extra_args == ["--flag"]
    assert config.extra_env == {"TOKEN": "original"}


def test_config_repr_omits_sensitive_launch_configuration(tmp_path):
    from soulacp.config import ACPConfig

    config = ACPConfig(
        cwd=str(tmp_path / "private-workspace"),
        additional_directories=[str(tmp_path / "private-root")],
        mcp_servers=[
            {
                "type": "http",
                "name": "private-mcp",
                "url": "https://mcp.example.test/private-endpoint",
                "headers": [{"name": "Authorization", "value": "Bearer private-token"}],
            }
        ],
        extra_args=["--private-argument"],
        extra_env={"PRIVATE_TOKEN": "private-env-value"},
    )

    rendered = repr(config)

    for private_value in (
        "private-workspace",
        "private-root",
        "private-mcp",
        "private-endpoint",
        "private-token",
        "private-argument",
        "PRIVATE_TOKEN",
        "private-env-value",
    ):
        assert private_value not in rendered
    assert "pool_size=5" in rendered


def test_config_validates_normalizes_and_detaches_session_context():
    from soulacp.config import ACPConfig

    roots = ["C:/workspace/shared"]
    servers = [
        {
            "type": "stdio",
            "name": "local-tools",
            "command": "C:/tools/mcp-server.exe",
            "args": ["--stdio"],
            "env": [{"name": "MODE", "value": "test"}],
        },
        {
            "type": "http",
            "name": "remote-tools",
            "url": "https://mcp.example.test",
            "headers": [{"name": "Authorization", "value": "Bearer token"}],
        },
    ]

    config = ACPConfig(additional_directories=roots, mcp_servers=servers)
    roots.append("C:/workspace/later")
    servers[0]["args"].append("--changed")

    assert config.additional_directories == ["C:/workspace/shared"]
    assert config.mcp_servers[0] == {
        "name": "local-tools",
        "command": "C:/tools/mcp-server.exe",
        "args": ["--stdio"],
        "env": [{"name": "MODE", "value": "test"}],
    }


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"additional_directories": "C:/workspace/shared"}, "additional_directories"),
        ({"additional_directories": ["relative/root"]}, "additional_directories"),
        ({"mcp_servers": {}}, "mcp_servers"),
        ({"mcp_servers": ["server"]}, "mcp_servers\\[0\\]"),
        (
            {"mcp_servers": [{"command": "C:/tools/mcp.exe", "args": [], "env": []}]},
            "mcp_servers\\[0\\]\\.name",
        ),
        (
            {"mcp_servers": [{"name": "local", "command": "mcp", "args": [], "env": []}]},
            "command must be an absolute path",
        ),
        (
            {"mcp_servers": [{"name": "local", "command": "C:/mcp.exe", "args": "", "env": []}]},
            "args",
        ),
        (
            {
                "mcp_servers": [
                    {"name": "local", "command": "C:/mcp.exe", "args": [], "env": [{"name": "A=B", "value": "1"}]}
                ]
            },
            "must not contain",
        ),
        (
            {"mcp_servers": [{"type": "http", "name": "remote", "url": "https://example.test"}]},
            "headers",
        ),
        (
            {"mcp_servers": [{"type": "http", "name": "remote", "url": "file:///tmp/socket", "headers": []}]},
            "HTTP\\(S\\) URL",
        ),
        (
            {
                "mcp_servers": [
                    {
                        "type": "sse",
                        "name": "remote",
                        "url": "https://user@example.test/events",
                        "headers": [],
                    }
                ]
            },
            "must not contain embedded credentials",
        ),
        (
            {
                "mcp_servers": [
                    {
                        "type": "http",
                        "name": "remote",
                        "url": "https://example.test/rpc\r\nInjected: yes",
                        "headers": [],
                    }
                ]
            },
            "must not contain whitespace, control characters, or backslashes",
        ),
        (
            {
                "mcp_servers": [
                    {
                        "type": "sse",
                        "name": "remote",
                        "url": "https://example.test/a path",
                        "headers": [],
                    }
                ]
            },
            "must not contain whitespace, control characters, or backslashes",
        ),
        (
            {
                "mcp_servers": [
                    {
                        "type": "http",
                        "name": "remote",
                        "url": "https://example.test\\alternate",
                        "headers": [],
                    }
                ]
            },
            "must not contain whitespace, control characters, or backslashes",
        ),
        (
            {
                "mcp_servers": [
                    {
                        "type": "http",
                        "name": "remote",
                        "url": "https://example.test",
                        "headers": [{"name": "Bad Header", "value": "value"}],
                    }
                ]
            },
            "valid HTTP field name",
        ),
        (
            {
                "mcp_servers": [
                    {
                        "type": "http",
                        "name": "remote",
                        "url": "https://example.test",
                        "headers": [{"name": "Authorization", "value": "value\r\nInjected: yes"}],
                    }
                ]
            },
            "must not contain CR or LF",
        ),
        (
            {"mcp_servers": [{"type": "websocket", "name": "remote"}]},
            "type",
        ),
        (
            {
                "mcp_servers": [
                    {
                        "type": "http",
                        "name": "remote",
                        "url": "https://example.test",
                        "headers": [],
                        "_meta": {"unsupported": {1, 2}},
                    }
                ]
            },
            "finite JSON values",
        ),
        (
            {
                "mcp_servers": [
                    {
                        "type": "http",
                        "name": "remote",
                        "url": "https://example.test",
                        "headers": [],
                        "_meta": {"weight": float("nan")},
                    }
                ]
            },
            "finite JSON values",
        ),
    ],
)
def test_config_rejects_invalid_session_context(kwargs, match):
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match=match):
        ACPConfig(**kwargs)


@pytest.mark.parametrize(
    "metadata",
    [
        {1: "integer key"},
        {"tuple": ("not", "a", "JSON", "array")},
    ],
)
def test_config_rejects_mcp_metadata_that_requires_json_coercion(metadata):
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match="finite JSON values"):
        ACPConfig(
            mcp_servers=[
                {
                    "type": "http",
                    "name": "remote",
                    "url": "https://example.test",
                    "headers": [],
                    "_meta": metadata,
                }
            ]
        )


def test_config_validation_retries():
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match="max_retries"):
        ACPConfig(max_retries=-1)


def test_config_validation_zero_retries_ok():
    from soulacp.config import ACPConfig

    config = ACPConfig(max_retries=0)
    assert config.max_retries == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pool_size", True),
        ("pool_max_connections", 1.5),
        ("timeout_prompt", "30"),
        ("pool_cross_session_reuse", "false"),
        ("inherit_parent_env", "false"),
        ("max_turns", 0),
        ("extra_args", ["ok", 1]),
        ("extra_env", {"KEY": 1}),
    ],
)
def test_config_rejects_invalid_explicit_types(field, value):
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match=field):
        ACPConfig(**{field: value})


def test_config_rejects_integer_timeout_outside_runtime_float_range():
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match="timeout_prompt"):
        ACPConfig(timeout_prompt=10**400)


@pytest.mark.asyncio
async def test_claude_defaults_do_not_inject_downstream_resource_policy():
    from soulacp.adapters.claude_client import ClaudeACPClient
    from soulacp.config import ACPConfig

    config = ACPConfig(model="claude-acp/sonnet")
    client = ClaudeACPClient(config)
    client._is_acp = True
    calls = []

    async def rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        if method == "session/new":
            return {"sessionId": "session"}
        return {}

    client._rpc = rpc
    await client._initialize()

    new_params = next(params for method, params, _timeout in calls if method == "session/new")
    assert "_meta" not in new_params
    assert "NODE_OPTIONS" not in config.extra_env


@pytest.mark.asyncio
async def test_claude_forwards_explicit_max_turns_only():
    from soulacp.adapters.claude_client import ClaudeACPClient
    from soulacp.config import ACPConfig

    client = ClaudeACPClient(ACPConfig(model="claude-acp/sonnet", max_turns=321))
    client._is_acp = True
    calls = []

    async def rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        if method == "session/new":
            return {"sessionId": "session"}
        return {}

    client._rpc = rpc
    await client._initialize()

    new_params = next(params for method, params, _timeout in calls if method == "session/new")
    assert new_params["_meta"]["claudeCode"]["options"]["maxTurns"] == 321


@pytest.mark.asyncio
async def test_gemini_initialize_establishes_session_before_connect_returns():
    from soulacp.adapters.gemini_client import GeminiACPClient
    from soulacp.config import ACPConfig

    client = GeminiACPClient(ACPConfig(provider="gemini", model="gemini-acp/gemini-3-flash-preview"))
    calls = []

    async def rpc(method, params, timeout=None):
        calls.append((method, params, timeout))
        if method == "initialize":
            return {"protocolVersion": 1, "authMethods": []}
        if method == "session/new":
            return {"sessionId": "gemini-session"}
        return {}

    client._rpc = rpc
    await client._initialize()

    assert client.session_id == "gemini-session"
    assert [method for method, _params, _timeout in calls] == ["initialize", "session/new"]


def test_gemini_model_prefix_is_removed_only_once(monkeypatch):
    from soulacp.adapters import gemini_client as gemini_module
    from soulacp.adapters.gemini_client import GeminiACPClient
    from soulacp.config import ACPConfig

    monkeypatch.setattr(gemini_module, "find_gemini_binary", lambda: "gemini")
    client = GeminiACPClient(ACPConfig(provider="gemini", model="gemini-acp/model-gemini-acp/suffix"))

    assert client._get_acp_command() == ["gemini", "--acp", "--model", "model-gemini-acp/suffix"]


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_methods", ["oauth", ["oauth"], [{}]])
async def test_gemini_initialize_rejects_malformed_auth_methods(auth_methods):
    from soulacp.adapters.gemini_client import GeminiACPClient
    from soulacp.config import ACPConfig
    from soulacp.errors import ACPProtocolError

    client = GeminiACPClient(ACPConfig(provider="gemini", model="gemini-acp/gemini-3-flash-preview"))

    async def rpc(method, _params, timeout=None):
        assert timeout is not None
        if method == "initialize":
            return {"protocolVersion": 1, "authMethods": auth_methods}
        raise AssertionError(f"unexpected RPC: {method}")

    client._rpc = rpc

    with pytest.raises(ACPProtocolError, match="auth"):
        await client._initialize()


def test_resolve_provider_claude():
    from soulacp.config import resolve_provider

    assert resolve_provider("claude-acp/sonnet") == "claude"


def test_resolve_provider_gemini():
    from soulacp.config import resolve_provider

    assert resolve_provider("gemini-acp/flash") == "gemini"


def test_resolve_provider_openclaw():
    from soulacp.config import resolve_provider

    assert resolve_provider("openclaw/default") == "openclaw"


def test_resolve_provider_qwen():
    from soulacp.config import resolve_provider

    assert resolve_provider("qwen-acp/qwen-coder") == "qwen"


def test_resolve_client_class_qwen():
    from soulacp.adapters.qwen_client import QwenACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("qwen") is QwenACPClient


def test_resolve_provider_kimi():
    from soulacp.config import resolve_provider

    assert resolve_provider("kimi-acp/default") == "kimi"


def test_resolve_client_class_kimi():
    from soulacp.adapters.kimi_client import KimiACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("kimi") is KimiACPClient


def test_resolve_provider_cursor_acp():
    from soulacp.config import resolve_provider

    assert resolve_provider("cursor-acp/default") == "cursor"


def test_resolve_provider_cursor_cli():
    from soulacp.config import resolve_provider

    assert resolve_provider("cursor-cli/gpt-4") == "cursor-cli"


def test_resolve_provider_unknown_fails_closed():
    import pytest

    from soulacp.config import resolve_provider

    with pytest.raises(ValueError, match="Cannot resolve provider"):
        resolve_provider("typo-provider/model")
    with pytest.raises(ValueError, match="Cannot resolve provider"):
        resolve_provider("claudette/model")
    with pytest.raises(ValueError, match="Cannot resolve provider"):
        resolve_provider(None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider": " claude"},
        {"model": "claude-acp/sonnet\x00"},
        {"cwd": "\x00"},
        {"extra_args": ["ok", "bad\x00"]},
        {"extra_env": {"BAD=NAME": "value"}},
        {"extra_env": {"GOOD": "bad\x00value"}},
        {"provider": "\ud800"},
        {"model": "claude-acp/\ud800"},
        {"cwd": "\ud800"},
        {"extra_args": ["\ud800"]},
        {"extra_env": {"\ud800": "value"}},
        {"extra_env": {"GOOD": "\ud800"}},
    ],
)
def test_config_rejects_process_argument_injection_shapes(kwargs):
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError):
        ACPConfig(**kwargs)


def test_config_normalizes_relative_cwd_to_absolute(monkeypatch, tmp_path):
    from pathlib import Path

    from soulacp.config import ACPConfig

    monkeypatch.chdir(tmp_path)
    config = ACPConfig(cwd=".")

    assert Path(config.cwd).is_absolute()
    assert Path(config.cwd).resolve() == tmp_path.resolve()


def test_registry_covers_all_routable_providers():
    from soulacp.config import FALLBACK_MAP, resolve_client_class
    from soulacp.registry import list_agents

    agents = list_agents()
    names = [agent.name for agent in agents]

    assert len(names) == 30
    assert len(names) == len(set(names))
    assert set(names) == set(FALLBACK_MAP)
    for name in names:
        assert resolve_client_class(name) is not None


def test_resolve_client_class_cursor_acp():
    from soulacp.adapters.cursor_acp_client import CursorACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("cursor") is CursorACPClient


def test_resolve_client_class_cursor_cli_legacy():
    from soulacp.adapters.cursor_client import CursorCLIClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("cursor-cli") is CursorCLIClient


def test_resolve_provider_codebuddy():
    from soulacp.config import resolve_provider

    assert resolve_provider("codebuddy-acp/default") == "codebuddy"


def test_resolve_client_class_codebuddy():
    from soulacp.adapters.codebuddy_client import CodebuddyACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("codebuddy") is CodebuddyACPClient


def test_resolve_provider_cline():
    from soulacp.config import resolve_provider

    assert resolve_provider("cline-acp/default") == "cline"


def test_resolve_client_class_cline():
    from soulacp.adapters.cline_client import ClineACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("cline") is ClineACPClient


def test_resolve_provider_copilot():
    from soulacp.config import resolve_provider

    assert resolve_provider("copilot-acp/default") == "copilot"


def test_resolve_client_class_copilot():
    from soulacp.adapters.copilot_client import CopilotACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("copilot") is CopilotACPClient


def test_resolve_provider_minion():
    from soulacp.config import resolve_provider

    assert resolve_provider("minion-acp/default") == "minion"


def test_resolve_client_class_minion():
    from soulacp.adapters.minion_client import MinionACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("minion") is MinionACPClient


def test_resolve_provider_vibe():
    from soulacp.config import resolve_provider

    assert resolve_provider("vibe-acp/default") == "vibe"


def test_resolve_client_class_vibe():
    from soulacp.adapters.vibe_client import VibeACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("vibe") is VibeACPClient


def test_resolve_provider_nova():
    from soulacp.config import resolve_provider

    assert resolve_provider("nova-acp/default") == "nova"


def test_resolve_client_class_nova():
    from soulacp.adapters.nova_client import NovaACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("nova") is NovaACPClient


def test_resolve_provider_crow():
    from soulacp.config import resolve_provider

    assert resolve_provider("crow-acp/default") == "crow"


def test_resolve_client_class_crow():
    from soulacp.adapters.crow_client import CrowACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("crow") is CrowACPClient


def test_resolve_provider_amp():
    from soulacp.config import resolve_provider

    assert resolve_provider("amp-acp/default") == "amp"


def test_resolve_client_class_amp():
    from soulacp.adapters.amp_client import AmpACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("amp") is AmpACPClient


def test_resolve_provider_auggie():
    from soulacp.config import resolve_provider

    assert resolve_provider("auggie-acp/default") == "auggie"


def test_resolve_client_class_auggie():
    from soulacp.adapters.auggie_client import AuggieACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("auggie") is AuggieACPClient


def test_resolve_provider_autohand():
    from soulacp.config import resolve_provider

    assert resolve_provider("autohand-acp/default") == "autohand"


def test_resolve_client_class_autohand():
    from soulacp.adapters.autohand_client import AutohandACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("autohand") is AutohandACPClient


def test_resolve_provider_corust():
    from soulacp.config import resolve_provider

    assert resolve_provider("corust-acp/default") == "corust"


def test_resolve_client_class_corust():
    from soulacp.adapters.corust_client import CorustACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("corust") is CorustACPClient


def test_resolve_provider_deepagents():
    from soulacp.config import resolve_provider

    assert resolve_provider("deepagents-acp/default") == "deepagents"


def test_resolve_client_class_deepagents():
    from soulacp.adapters.deepagents_client import DeepAgentsACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("deepagents") is DeepAgentsACPClient


def test_resolve_provider_droid():
    from soulacp.config import resolve_provider

    assert resolve_provider("droid-acp/default") == "droid"


def test_resolve_client_class_droid():
    from soulacp.adapters.droid_client import DroidACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("droid") is DroidACPClient


def test_droid_command_and_child_env_match_registry(monkeypatch):
    from soulacp.adapters import droid_client
    from soulacp.adapters.droid_client import DroidACPClient
    from soulacp.config import ACPConfig

    monkeypatch.setattr(droid_client, "find_droid_binary", lambda: "droid")
    monkeypatch.delenv("DROID_DISABLE_AUTO_UPDATE", raising=False)
    client = DroidACPClient(ACPConfig(provider="droid", model="droid-acp/default"))

    assert client._get_acp_command() == ["droid", "exec", "--output-format", "acp-daemon"]
    assert client._get_acp_env() == {
        "DROID_DISABLE_AUTO_UPDATE": "true",
        "FACTORY_DROID_AUTO_UPDATE_ENABLED": "false",
    }
    assert "DROID_DISABLE_AUTO_UPDATE" not in droid_client.os.environ


def test_auggie_adapter_env_does_not_mutate_parent(monkeypatch):
    from soulacp.adapters import auggie_client
    from soulacp.adapters.auggie_client import AuggieACPClient
    from soulacp.config import ACPConfig

    monkeypatch.setattr(auggie_client, "find_auggie_binary", lambda: "auggie")
    monkeypatch.delenv("AUGMENT_DISABLE_AUTO_UPDATE", raising=False)
    client = AuggieACPClient(ACPConfig(provider="auggie", model="auggie-acp/default"))

    assert client._get_acp_command() == ["auggie", "--acp"]
    assert client._get_acp_env() == {"AUGMENT_DISABLE_AUTO_UPDATE": "1"}
    assert "AUGMENT_DISABLE_AUTO_UPDATE" not in auggie_client.os.environ


def test_resolve_provider_fastagent():
    from soulacp.config import resolve_provider

    assert resolve_provider("fastagent-acp/default") == "fastagent"


def test_resolve_client_class_fastagent():
    from soulacp.adapters.fastagent_client import FastAgentACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("fastagent") is FastAgentACPClient


def test_resolve_provider_copilot_ls():
    from soulacp.config import resolve_provider

    assert resolve_provider("copilot-ls-acp/default") == "copilot-ls"


def test_resolve_client_class_copilot_ls():
    from soulacp.adapters.copilot_ls_client import CopilotLSACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("copilot-ls") is CopilotLSACPClient


def test_resolve_provider_goose():
    from soulacp.config import resolve_provider

    assert resolve_provider("goose-acp/default") == "goose"


def test_resolve_client_class_goose():
    from soulacp.adapters.goose_client import GooseACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("goose") is GooseACPClient


def test_resolve_provider_junie():
    from soulacp.config import resolve_provider

    assert resolve_provider("junie-acp/default") == "junie"


def test_resolve_client_class_junie():
    from soulacp.adapters.junie_client import JunieACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("junie") is JunieACPClient


def test_resolve_provider_kilo():
    from soulacp.config import resolve_provider

    assert resolve_provider("kilo-acp/default") == "kilo"


def test_resolve_client_class_kilo():
    from soulacp.adapters.kilo_client import KiloACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("kilo") is KiloACPClient


def test_resolve_provider_pi():
    from soulacp.config import resolve_provider

    assert resolve_provider("pi-acp/default") == "pi"


def test_resolve_client_class_pi():
    from soulacp.adapters.pi_client import PiACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("pi") is PiACPClient


def test_resolve_provider_qoder():
    from soulacp.config import resolve_provider

    assert resolve_provider("qoder-acp/default") == "qoder"


def test_resolve_client_class_qoder():
    from soulacp.adapters.qoder_client import QoderACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("qoder") is QoderACPClient


def test_resolve_provider_stakpak():
    from soulacp.config import resolve_provider

    assert resolve_provider("stakpak-acp/default") == "stakpak"


def test_resolve_client_class_stakpak():
    from soulacp.adapters.stakpak_client import StakpakACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("stakpak") is StakpakACPClient


def test_resolve_client_class_unknown():
    from soulacp.config import resolve_client_class

    with pytest.raises(ValueError, match="Unknown provider"):
        resolve_client_class("nonexistent")


def test_from_env(monkeypatch):
    from soulacp.config import ACPConfig

    monkeypatch.setenv("ACP_PROVIDER", "gemini")
    monkeypatch.setenv("ACP_MODEL", "flash")
    monkeypatch.setenv("ACP_POOL_SIZE", "5")
    monkeypatch.setenv("ACP_POOL_MAX_CONNECTIONS", "8")
    monkeypatch.setenv("ACP_POOL_ACQUIRE_TIMEOUT", "30")
    monkeypatch.setenv("ACP_POOL_IDLE_TIMEOUT", "600")
    monkeypatch.setenv("ACP_POOL_KEEPALIVE_INTERVAL", "300")
    monkeypatch.setenv("ACP_POOL_MAX_AGE", "7200")
    monkeypatch.setenv("ACP_POOL_MAX_USES", "50")
    monkeypatch.setenv("ACP_POOL_CROSS_SESSION_REUSE", "false")
    monkeypatch.setenv("ACP_INHERIT_PARENT_ENV", "false")
    monkeypatch.setenv("ACP_TIMEOUT_CONNECT", "30")
    monkeypatch.setenv("ACP_TIMEOUT_PROMPT", "21600")
    monkeypatch.setenv("ACP_TIMEOUT_STREAM", "1800")
    monkeypatch.setenv("ACP_MAX_RETRIES", "1")
    config = ACPConfig.from_env()
    assert config.provider == "gemini"
    assert config.model == "flash"
    assert config.pool_size == 5
    assert config.pool_max_connections == 8
    assert config.pool_acquire_timeout == 30
    assert config.pool_idle_timeout == 600
    assert config.pool_keepalive_interval == 300
    assert config.pool_max_age == 7200
    assert config.pool_max_uses == 50
    assert config.pool_cross_session_reuse is False
    assert config.inherit_parent_env is False
    assert config.timeout_prompt == 21600
    assert config.timeout_stream == 1800
    assert config.max_retries == 1


def test_from_env_accepts_fractional_second_limits(monkeypatch):
    from soulacp.config import ACPConfig

    monkeypatch.setenv("ACP_POOL_ACQUIRE_TIMEOUT", "0.25")
    monkeypatch.setenv("ACP_TIMEOUT_CONNECT", "0.5")
    monkeypatch.setenv("ACP_TIMEOUT_STREAM", "1.5")

    config = ACPConfig.from_env()
    assert config.pool_acquire_timeout == 0.25
    assert config.timeout_connect == 0.5
    assert config.timeout_stream == 1.5


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE"])
def test_from_env_false_values(monkeypatch, value):
    from soulacp.config import ACPConfig

    monkeypatch.setenv("ACP_POOL_CROSS_SESSION_REUSE", value)
    assert ACPConfig.from_env().pool_cross_session_reuse is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
def test_from_env_true_values(monkeypatch, value):
    from soulacp.config import ACPConfig

    monkeypatch.setenv("ACP_POOL_CROSS_SESSION_REUSE", value)
    assert ACPConfig.from_env().pool_cross_session_reuse is True


def test_from_env_rejects_invalid_bool(monkeypatch):
    from soulacp.config import ACPConfig

    monkeypatch.setenv("ACP_POOL_CROSS_SESSION_REUSE", "sometimes")
    with pytest.raises(ValueError, match="ACP_POOL_CROSS_SESSION_REUSE"):
        ACPConfig.from_env()


def test_from_env_rejects_invalid_inherit_parent_env(monkeypatch):
    from soulacp.config import ACPConfig

    monkeypatch.setenv("ACP_INHERIT_PARENT_ENV", "sometimes")
    with pytest.raises(ValueError, match="ACP_INHERIT_PARENT_ENV"):
        ACPConfig.from_env()
