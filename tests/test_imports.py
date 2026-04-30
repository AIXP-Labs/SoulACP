"""Test that all soulacp modules import correctly."""


def test_package_import():
    import soulacp

    assert soulacp.__version__ == "0.1.2"
    # Verify key exports exist rather than hardcoding count
    expected = {"ManagedSession", "ACPConfig", "ACPConnectionPool", "ACPClient", "ACPClientBase"}
    assert expected.issubset(set(soulacp.__all__))


def test_config_import():
    from soulacp.config import ACPConfig

    config = ACPConfig(provider="claude", model="claude-sonnet-4-20250514")
    assert config.provider == "claude"
    assert config.pool_size == 10
    assert config.timeout_connect == 30
    assert config.max_retries == 3


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("ACP_PROVIDER", "gemini-acp")
    monkeypatch.setenv("ACP_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("ACP_POOL_SIZE", "5")
    from soulacp.config import ACPConfig

    config = ACPConfig.from_env()
    assert config.provider == "gemini-acp"
    assert config.model == "gemini-2.5-flash"
    assert config.pool_size == 5


def test_resolve_provider():
    from soulacp.config import resolve_provider

    assert resolve_provider("claude-acp/claude-sonnet-4-20250514") == "claude"
    assert resolve_provider("gemini-acp/gemini-2.5-flash") == "gemini"


def test_resolve_client_class():
    from soulacp.adapters.claude_client import ClaudeACPClient
    from soulacp.adapters.gemini_client import GeminiACPClient
    from soulacp.config import resolve_client_class

    assert resolve_client_class("claude") is ClaudeACPClient
    assert resolve_client_class("gemini") is GeminiACPClient


def test_adapters_import():
    from soulacp.adapters import (
        ACPClientBase,
        AmpACPClient,
        AuggieACPClient,
        AutohandACPClient,
        ClaudeACPClient,
        ClineACPClient,
        CodebuddyACPClient,
        CodexACPClient,
        CopilotACPClient,
        CopilotLSACPClient,
        CorustACPClient,
        CrowACPClient,
        CursorACPClient,
        CursorCLIClient,
        DeepAgentsACPClient,
        DroidACPClient,
        FastAgentACPClient,
        GeminiACPClient,
        GooseACPClient,
        JunieACPClient,
        KiloACPClient,
        KimiACPClient,
        MinionACPClient,
        NovaACPClient,
        OpenClawACPClient,
        OpenCodeACPClient,
        PiACPClient,
        QoderACPClient,
        QwenACPClient,
        StakpakACPClient,
        VibeACPClient,
    )

    assert ACPClientBase is not None
    assert ClaudeACPClient is not None
    assert GeminiACPClient is not None
    assert OpenCodeACPClient is not None
    assert OpenClawACPClient is not None
    assert CursorACPClient is not None
    assert CursorCLIClient is not None
    assert CodexACPClient is not None
    assert QwenACPClient is not None
    assert KimiACPClient is not None
    assert CodebuddyACPClient is not None
    assert ClineACPClient is not None
    assert CopilotACPClient is not None
    assert MinionACPClient is not None
    assert VibeACPClient is not None
    assert NovaACPClient is not None
    assert CrowACPClient is not None
    assert AmpACPClient is not None
    assert AuggieACPClient is not None
    assert AutohandACPClient is not None
    assert CorustACPClient is not None
    assert CopilotLSACPClient is not None
    assert DeepAgentsACPClient is not None
    assert DroidACPClient is not None
    assert FastAgentACPClient is not None
    assert GooseACPClient is not None
    assert JunieACPClient is not None
    assert KiloACPClient is not None
    assert PiACPClient is not None
    assert QoderACPClient is not None
    assert StakpakACPClient is not None


def test_pool_import():
    from soulacp.pool import ACPConnectionPool

    assert ACPConnectionPool is not None


def test_retry_import():
    from soulacp.retry import is_retryable, retry_async

    assert callable(retry_async)
    assert callable(is_retryable)


def test_binary_import():
    from soulacp.binary import (
        find_amp_binary,
        find_auggie_binary,
        find_autohand_binary,
        find_binary,
        find_claude_binary,
        find_cline_binary,
        find_codebuddy_binary,
        find_codex_binary,
        find_copilot_binary,
        find_copilot_ls_binary,
        find_corust_binary,
        find_crow_binary,
        find_cursor_binary,
        find_deepagents_binary,
        find_droid_binary,
        find_fastagent_binary,
        find_gemini_binary,
        find_goose_binary,
        find_junie_binary,
        find_kilo_binary,
        find_kimi_binary,
        find_minion_binary,
        find_nova_binary,
        find_opencode_binary,
        find_openclaw_binary,
        find_pi_binary,
        find_qoder_binary,
        find_qwen_binary,
        find_stakpak_binary,
        find_vibe_binary,
    )

    assert callable(find_binary)
    assert callable(find_claude_binary)
    assert callable(find_gemini_binary)
    assert callable(find_opencode_binary)
    assert callable(find_openclaw_binary)
    assert callable(find_cursor_binary)
    assert callable(find_codex_binary)
    assert callable(find_qwen_binary)
    assert callable(find_kimi_binary)
    assert callable(find_codebuddy_binary)
    assert callable(find_cline_binary)
    assert callable(find_copilot_binary)
    assert callable(find_minion_binary)
    assert callable(find_vibe_binary)
    assert callable(find_nova_binary)
    assert callable(find_crow_binary)
    assert callable(find_amp_binary)
    assert callable(find_auggie_binary)
    assert callable(find_autohand_binary)
    assert callable(find_corust_binary)
    assert callable(find_copilot_ls_binary)
    assert callable(find_deepagents_binary)
    assert callable(find_droid_binary)
    assert callable(find_fastagent_binary)
    assert callable(find_goose_binary)
    assert callable(find_junie_binary)
    assert callable(find_kilo_binary)
    assert callable(find_pi_binary)
    assert callable(find_qoder_binary)
    assert callable(find_stakpak_binary)


def test_services_import():
    from soulacp.services import FSService, TerminalService, resolve_path

    assert FSService is not None
    assert TerminalService is not None
    assert callable(resolve_path)


def test_no_external_deps():
    """Verify soulacp has zero external package dependencies."""
    external = ["requests", "httpx", "aiohttp", "websockets", "pydantic"]
    # soulacp should not import any of these
    import soulacp  # noqa: F811

    for mod_name in external:
        # Check soulacp source doesn't import these
        assert mod_name not in str(soulacp.__dict__), f"soulacp imports {mod_name}"
