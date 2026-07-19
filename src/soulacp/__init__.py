"""soulacp — Python ACP client library.

Connect to 30 AI coding-agent integrations through native CLI sessions and bounded
connection pooling. See README for the full list of supported agents.

Usage::

    import asyncio
    from soulacp import ManagedSession

    async def main():
        async with ManagedSession(provider="claude", model="claude-acp/sonnet") as session:
            response = await session.query("Hello!")
            print(response)

    asyncio.run(main())
"""

from soulacp.adapters.amp_client import AmpACPClient
from soulacp.adapters.auggie_client import AuggieACPClient
from soulacp.adapters.autohand_client import AutohandACPClient
from soulacp.adapters.base_client import ACPClientBase, RPCError
from soulacp.adapters.claude_client import ClaudeACPClient
from soulacp.adapters.cline_client import ClineACPClient
from soulacp.adapters.codebuddy_client import CodebuddyACPClient
from soulacp.adapters.codex_client import CodexACPClient
from soulacp.adapters.copilot_client import CopilotACPClient
from soulacp.adapters.copilot_ls_client import CopilotLSACPClient
from soulacp.adapters.corust_client import CorustACPClient
from soulacp.adapters.crow_client import CrowACPClient
from soulacp.adapters.cursor_acp_client import CursorACPClient
from soulacp.adapters.cursor_client import CursorCLIClient
from soulacp.adapters.deepagents_client import DeepAgentsACPClient
from soulacp.adapters.droid_client import DroidACPClient
from soulacp.adapters.fastagent_client import FastAgentACPClient
from soulacp.adapters.gemini_client import GeminiACPClient
from soulacp.adapters.goose_client import GooseACPClient
from soulacp.adapters.junie_client import JunieACPClient
from soulacp.adapters.kilo_client import KiloACPClient
from soulacp.adapters.kimi_client import KimiACPClient
from soulacp.adapters.minion_client import MinionACPClient
from soulacp.adapters.nova_client import NovaACPClient
from soulacp.adapters.openclaw_client import OpenClawACPClient
from soulacp.adapters.opencode_client import OpenCodeACPClient
from soulacp.adapters.pi_client import PiACPClient
from soulacp.adapters.protocol import ACPClient
from soulacp.adapters.qoder_client import QoderACPClient
from soulacp.adapters.qwen_client import QwenACPClient
from soulacp.adapters.stakpak_client import StakpakACPClient
from soulacp.adapters.vibe_client import VibeACPClient
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
    find_openclaw_binary,
    find_opencode_binary,
    find_pi_binary,
    find_qoder_binary,
    find_qwen_binary,
    find_stakpak_binary,
    find_vibe_binary,
)
from soulacp.cache import CacheBackend, FileCache, MemoryCache
from soulacp.config import ACPConfig, resolve_client_class, resolve_provider
from soulacp.errors import (
    ACPError,
    ACPPartialResponseError,
    ACPPoolAcquireTimeoutError,
    ACPPoolClosedError,
    ACPPoolError,
    ACPPromptTimeoutError,
    ACPProtocolError,
    ACPProviderError,
    ACPRequestOutcomeUnknownError,
    ACPResponseError,
    ACPResumeError,
    ACPStreamTimeoutError,
    ACPUnsupportedCapabilityError,
)
from soulacp.meta import CLIENT_NAME, __version__
from soulacp.pool import ACPConnectionPool
from soulacp.registry import AgentInfo, is_installed, list_agents, list_installed_agents
from soulacp.retry import retry_async
from soulacp.session import ManagedSession, is_context_overflow
from soulacp.session_store import ProviderSessionStore

__all__ = [
    # Meta
    "__version__",
    "CLIENT_NAME",
    # High-level API
    "ManagedSession",
    "is_context_overflow",
    # Session management
    "ProviderSessionStore",
    "CacheBackend",
    "MemoryCache",
    "FileCache",
    # Config
    "ACPConfig",
    "resolve_provider",
    "resolve_client_class",
    # Pool
    "ACPConnectionPool",
    # Registry
    "AgentInfo",
    "list_agents",
    "list_installed_agents",
    "is_installed",
    # Retry
    "retry_async",
    # Binary discovery
    "find_binary",
    "find_claude_binary",
    "find_gemini_binary",
    "find_opencode_binary",
    "find_openclaw_binary",
    "find_cursor_binary",
    "find_codex_binary",
    "find_qwen_binary",
    "find_kimi_binary",
    "find_codebuddy_binary",
    "find_cline_binary",
    "find_copilot_binary",
    "find_minion_binary",
    "find_vibe_binary",
    "find_nova_binary",
    "find_crow_binary",
    "find_amp_binary",
    "find_auggie_binary",
    "find_autohand_binary",
    "find_corust_binary",
    "find_copilot_ls_binary",
    "find_deepagents_binary",
    "find_droid_binary",
    "find_fastagent_binary",
    "find_goose_binary",
    "find_junie_binary",
    "find_kilo_binary",
    "find_pi_binary",
    "find_qoder_binary",
    "find_stakpak_binary",
    # Protocol
    "ACPClient",
    # Errors
    "RPCError",
    "ACPError",
    "ACPPoolError",
    "ACPPoolClosedError",
    "ACPPoolAcquireTimeoutError",
    "ACPResumeError",
    "ACPResponseError",
    "ACPProtocolError",
    "ACPPartialResponseError",
    "ACPProviderError",
    "ACPRequestOutcomeUnknownError",
    "ACPPromptTimeoutError",
    "ACPStreamTimeoutError",
    "ACPUnsupportedCapabilityError",
    # Adapters
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
