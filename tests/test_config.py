"""Test ACPConfig validation and routing."""

import pytest


def test_config_defaults():
    from soulacp.config import ACPConfig

    config = ACPConfig()
    assert config.provider == "claude"
    assert config.pool_size == 10
    assert config.max_retries == 3


def test_config_validation_pool_size():
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match="pool_size"):
        ACPConfig(pool_size=0)


def test_config_validation_timeout():
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match="timeout_connect"):
        ACPConfig(timeout_connect=-1)


def test_config_validation_retries():
    from soulacp.config import ACPConfig

    with pytest.raises(ValueError, match="max_retries"):
        ACPConfig(max_retries=-1)


def test_config_validation_zero_retries_ok():
    from soulacp.config import ACPConfig

    config = ACPConfig(max_retries=0)
    assert config.max_retries == 0


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
    monkeypatch.setenv("ACP_MAX_RETRIES", "1")
    config = ACPConfig.from_env()
    assert config.provider == "gemini"
    assert config.model == "flash"
    assert config.pool_size == 5
    assert config.max_retries == 1
