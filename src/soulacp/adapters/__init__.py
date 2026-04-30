"""soulacp.adapters — CLI agent adapters."""

from soulacp.adapters.base_client import ACPClientBase
from soulacp.adapters.claude_client import ClaudeACPClient
from soulacp.adapters.cline_client import ClineACPClient
from soulacp.adapters.codebuddy_client import CodebuddyACPClient
from soulacp.adapters.codex_client import CodexACPClient
from soulacp.adapters.copilot_client import CopilotACPClient
from soulacp.adapters.crow_client import CrowACPClient
from soulacp.adapters.cursor_acp_client import CursorACPClient
from soulacp.adapters.cursor_client import CursorCLIClient
from soulacp.adapters.gemini_client import GeminiACPClient
from soulacp.adapters.kimi_client import KimiACPClient
from soulacp.adapters.minion_client import MinionACPClient
from soulacp.adapters.nova_client import NovaACPClient
from soulacp.adapters.openclaw_client import OpenClawACPClient
from soulacp.adapters.opencode_client import OpenCodeACPClient
from soulacp.adapters.protocol import ACPClient
from soulacp.adapters.qwen_client import QwenACPClient
from soulacp.adapters.vibe_client import VibeACPClient
from soulacp.adapters.amp_client import AmpACPClient
from soulacp.adapters.auggie_client import AuggieACPClient
from soulacp.adapters.autohand_client import AutohandACPClient
from soulacp.adapters.corust_client import CorustACPClient
from soulacp.adapters.copilot_ls_client import CopilotLSACPClient
from soulacp.adapters.deepagents_client import DeepAgentsACPClient
from soulacp.adapters.droid_client import DroidACPClient
from soulacp.adapters.fastagent_client import FastAgentACPClient
from soulacp.adapters.goose_client import GooseACPClient
from soulacp.adapters.junie_client import JunieACPClient
from soulacp.adapters.kilo_client import KiloACPClient
from soulacp.adapters.pi_client import PiACPClient
from soulacp.adapters.qoder_client import QoderACPClient
from soulacp.adapters.stakpak_client import StakpakACPClient

__all__ = [
    "ACPClient",
    "ACPClientBase",
    "ClaudeACPClient",
    "CodexACPClient",
    "GeminiACPClient",
    "OpenCodeACPClient",
    "OpenClawACPClient",
    "CursorACPClient",
    "CursorCLIClient",
    "QwenACPClient",
    "KimiACPClient",
    "CodebuddyACPClient",
    "ClineACPClient",
    "CopilotACPClient",
    "MinionACPClient",
    "VibeACPClient",
    "NovaACPClient",
    "CrowACPClient",
    "AmpACPClient",
    "AuggieACPClient",
    "AutohandACPClient",
    "CorustACPClient",
    "CopilotLSACPClient",
    "DeepAgentsACPClient",
    "DroidACPClient",
    "FastAgentACPClient",
    "GooseACPClient",
    "JunieACPClient",
    "KiloACPClient",
    "PiACPClient",
    "QoderACPClient",
    "StakpakACPClient",
]
