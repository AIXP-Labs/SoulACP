"""ACP connection configuration and provider routing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from soulacp.adapters.protocol import ACPClient


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------


def resolve_provider(model: str) -> str:
    """Extract provider name from a model identifier.

    Examples:
        ``claude-acp/sonnet`` → ``claude``
        ``gemini-acp/pro`` → ``gemini``
        ``opencode-acp/default`` → ``opencode``
        ``cursor-acp/default`` → ``cursor``
        ``cursor-cli/gpt-4`` → ``cursor-cli``
        ``codex-acp/gpt-5`` → ``codex``
        ``qwen-acp/qwen-coder`` → ``qwen``
        ``kimi-acp/default`` → ``kimi``
        ``codebuddy-acp/default`` → ``codebuddy``
        ``cline-acp/default`` → ``cline``
        ``copilot-acp/default`` → ``copilot``
        ``minion-acp/default`` → ``minion``
        ``vibe-acp/default`` → ``vibe``
        ``nova-acp/default`` → ``nova``
        ``crow-acp/default`` → ``crow``
    """
    model_lower = model.lower()
    if model_lower.startswith("claude"):
        return "claude"
    if model_lower.startswith("gemini"):
        return "gemini"
    if model_lower.startswith("opencode"):
        return "opencode"
    if model_lower.startswith("openclaw"):
        return "openclaw"
    if model_lower.startswith("cursor-cli"):
        return "cursor-cli"
    if model_lower.startswith("cursor"):
        return "cursor"
    if model_lower.startswith("codex"):
        return "codex"
    if model_lower.startswith("qwen"):
        return "qwen"
    if model_lower.startswith("kimi"):
        return "kimi"
    if model_lower.startswith("codebuddy"):
        return "codebuddy"
    if model_lower.startswith("cline"):
        return "cline"
    if model_lower.startswith("copilot-ls"):
        return "copilot-ls"
    if model_lower.startswith("copilot"):
        return "copilot"
    if model_lower.startswith("minion"):
        return "minion"
    if model_lower.startswith("vibe"):
        return "vibe"
    if model_lower.startswith("nova"):
        return "nova"
    if model_lower.startswith("crow"):
        return "crow"
    if model_lower.startswith("amp"):
        return "amp"
    if model_lower.startswith("auggie"):
        return "auggie"
    if model_lower.startswith("autohand"):
        return "autohand"
    if model_lower.startswith("corust"):
        return "corust"
    if model_lower.startswith("deepagents"):
        return "deepagents"
    if model_lower.startswith("droid"):
        return "droid"
    if model_lower.startswith("fastagent"):
        return "fastagent"
    if model_lower.startswith("goose"):
        return "goose"
    if model_lower.startswith("junie"):
        return "junie"
    if model_lower.startswith("kilo"):
        return "kilo"
    if model_lower.startswith("pi"):
        return "pi"
    if model_lower.startswith("qoder"):
        return "qoder"
    if model_lower.startswith("stakpak"):
        return "stakpak"
    return "claude"  # default


def resolve_client_class(provider: str) -> type[ACPClient]:
    """Return the client class for a given provider name.

    Lazy imports to avoid circular dependencies.
    """
    if provider == "claude":
        from soulacp.adapters.claude_client import ClaudeACPClient

        return ClaudeACPClient
    if provider == "gemini":
        from soulacp.adapters.gemini_client import GeminiACPClient

        return GeminiACPClient
    if provider == "opencode":
        from soulacp.adapters.opencode_client import OpenCodeACPClient

        return OpenCodeACPClient
    if provider == "openclaw":
        from soulacp.adapters.openclaw_client import OpenClawACPClient

        return OpenClawACPClient
    if provider == "cursor":
        from soulacp.adapters.cursor_acp_client import CursorACPClient

        return CursorACPClient
    if provider == "cursor-cli":
        from soulacp.adapters.cursor_client import CursorCLIClient

        return CursorCLIClient
    if provider == "codex":
        from soulacp.adapters.codex_client import CodexACPClient

        return CodexACPClient
    if provider == "qwen":
        from soulacp.adapters.qwen_client import QwenACPClient

        return QwenACPClient
    if provider == "kimi":
        from soulacp.adapters.kimi_client import KimiACPClient

        return KimiACPClient
    if provider == "codebuddy":
        from soulacp.adapters.codebuddy_client import CodebuddyACPClient

        return CodebuddyACPClient
    if provider == "cline":
        from soulacp.adapters.cline_client import ClineACPClient

        return ClineACPClient
    if provider == "copilot":
        from soulacp.adapters.copilot_client import CopilotACPClient

        return CopilotACPClient
    if provider == "minion":
        from soulacp.adapters.minion_client import MinionACPClient

        return MinionACPClient
    if provider == "vibe":
        from soulacp.adapters.vibe_client import VibeACPClient

        return VibeACPClient
    if provider == "nova":
        from soulacp.adapters.nova_client import NovaACPClient

        return NovaACPClient
    if provider == "crow":
        from soulacp.adapters.crow_client import CrowACPClient

        return CrowACPClient
    if provider == "amp":
        from soulacp.adapters.amp_client import AmpACPClient

        return AmpACPClient
    if provider == "auggie":
        from soulacp.adapters.auggie_client import AuggieACPClient

        return AuggieACPClient
    if provider == "autohand":
        from soulacp.adapters.autohand_client import AutohandACPClient

        return AutohandACPClient
    if provider == "corust":
        from soulacp.adapters.corust_client import CorustACPClient

        return CorustACPClient
    if provider == "deepagents":
        from soulacp.adapters.deepagents_client import DeepAgentsACPClient

        return DeepAgentsACPClient
    if provider == "droid":
        from soulacp.adapters.droid_client import DroidACPClient

        return DroidACPClient
    if provider == "fastagent":
        from soulacp.adapters.fastagent_client import FastAgentACPClient

        return FastAgentACPClient
    if provider == "copilot-ls":
        from soulacp.adapters.copilot_ls_client import CopilotLSACPClient

        return CopilotLSACPClient
    if provider == "goose":
        from soulacp.adapters.goose_client import GooseACPClient

        return GooseACPClient
    if provider == "junie":
        from soulacp.adapters.junie_client import JunieACPClient

        return JunieACPClient
    if provider == "kilo":
        from soulacp.adapters.kilo_client import KiloACPClient

        return KiloACPClient
    if provider == "pi":
        from soulacp.adapters.pi_client import PiACPClient

        return PiACPClient
    if provider == "qoder":
        from soulacp.adapters.qoder_client import QoderACPClient

        return QoderACPClient
    if provider == "stakpak":
        from soulacp.adapters.stakpak_client import StakpakACPClient

        return StakpakACPClient
    raise ValueError(f"Unknown provider: {provider}")


# ---------------------------------------------------------------------------
# Fallback configuration
# ---------------------------------------------------------------------------

# Format: provider → "fallback_provider-acp/fallback_model"
# Parsed by resolve_provider() to extract provider name.
FALLBACK_MAP: dict[str, str] = {
    "claude": "gemini-acp/gemini-2.5-flash",
    "gemini": "claude-acp/sonnet",
    "opencode": "claude-acp/sonnet",
    "openclaw": "claude-acp/sonnet",
    "cursor": "claude-acp/sonnet",
    "cursor-cli": "claude-acp/sonnet",
    "codex": "claude-acp/sonnet",
    "qwen": "claude-acp/sonnet",
    "kimi": "claude-acp/sonnet",
    "codebuddy": "claude-acp/sonnet",
    "cline": "claude-acp/sonnet",
    "copilot": "claude-acp/sonnet",
    "minion": "claude-acp/sonnet",
    "vibe": "claude-acp/sonnet",
    "nova": "claude-acp/sonnet",
    "crow": "claude-acp/sonnet",
    "amp": "claude-acp/sonnet",
    "auggie": "claude-acp/sonnet",
    "autohand": "claude-acp/sonnet",
    "corust": "claude-acp/sonnet",
    "deepagents": "claude-acp/sonnet",
    "droid": "claude-acp/sonnet",
    "fastagent": "claude-acp/sonnet",
    "copilot-ls": "claude-acp/sonnet",
    "goose": "claude-acp/sonnet",
    "junie": "claude-acp/sonnet",
    "kilo": "claude-acp/sonnet",
    "pi": "claude-acp/sonnet",
    "qoder": "claude-acp/sonnet",
    "stakpak": "claude-acp/sonnet",
}


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class ACPConfig:
    """Configuration for an ACP CLI subprocess connection.

    All settings can be overridden via environment variables with the
    ``ACP_`` prefix (e.g. ``ACP_POOL_SIZE=5``).
    """

    provider: str = "claude"
    """Provider name — see README for full list. 30 providers including cursor-cli (legacy)."""

    model: str = "claude-acp/sonnet"
    """Full model identifier used by ModelRegistry."""

    pool_size: int = 10
    """Maximum number of idle connections kept in the pool."""

    pool_idle_timeout: int = 2592000
    """Idle timeout in seconds before a pooled connection is closed (30 days)."""

    pool_keepalive_interval: int = 300
    """Keepalive check interval in seconds (5min). 0 = disabled."""

    timeout_connect: int = 30
    """Timeout in seconds for subprocess startup + initialize."""

    timeout_prompt: int = 2592000
    """Timeout in seconds for a single prompt/response cycle (30 days)."""

    timeout_stream: int = 2592000
    """Timeout in seconds for individual stream chunks (30 days)."""

    cwd: str = field(default_factory=os.getcwd)
    """Working directory for the CLI subprocess."""

    auto_approve_permissions: bool = True
    """Automatically approve permission requests from the CLI."""

    enable_fallback: bool = False
    """Enable automatic fallback to another provider on failure."""

    max_retries: int = 3
    """Maximum retry attempts for transient errors (Doc 25)."""

    retry_base_delay: float = 1.0
    """Base delay in seconds for exponential backoff (Doc 25)."""

    def __post_init__(self) -> None:
        if self.pool_size < 1:
            raise ValueError(f"pool_size must be >= 1, got {self.pool_size}")
        if self.timeout_connect <= 0:
            raise ValueError(f"timeout_connect must be > 0, got {self.timeout_connect}")
        if self.timeout_prompt <= 0:
            raise ValueError(f"timeout_prompt must be > 0, got {self.timeout_prompt}")
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries}")

    @classmethod
    def from_env(cls, **overrides) -> ACPConfig:
        """Create config from environment variables.

        Environment variables (all optional):
            ACP_PROVIDER          — provider name
            ACP_MODEL             — model identifier
            ACP_POOL_SIZE         — max idle connections
            ACP_POOL_IDLE_TIMEOUT — idle timeout seconds
            ACP_TIMEOUT_CONNECT   — connect timeout
            ACP_TIMEOUT_PROMPT    — prompt timeout
            ACP_TIMEOUT_STREAM    — stream timeout
            ACP_CWD               — working directory
            ACP_AUTO_APPROVE      — auto-approve permissions (true/false)
        """

        def _env(key: str, default: str | None = None) -> str | None:
            return os.environ.get(f"ACP_{key}", default)

        kwargs: dict = {}

        v = _env("PROVIDER")
        if v:
            kwargs["provider"] = v

        v = _env("MODEL")
        if v:
            kwargs["model"] = v

        v = _env("POOL_SIZE")
        if v:
            kwargs["pool_size"] = int(v)

        v = _env("POOL_IDLE_TIMEOUT")
        if v:
            kwargs["pool_idle_timeout"] = int(v)

        v = _env("POOL_KEEPALIVE_INTERVAL")
        if v:
            kwargs["pool_keepalive_interval"] = int(v)

        v = _env("TIMEOUT_CONNECT")
        if v:
            kwargs["timeout_connect"] = int(v)

        v = _env("TIMEOUT_PROMPT")
        if v:
            kwargs["timeout_prompt"] = int(v)

        v = _env("TIMEOUT_STREAM")
        if v:
            kwargs["timeout_stream"] = int(v)

        v = _env("CWD")
        if v:
            kwargs["cwd"] = v

        v = _env("AUTO_APPROVE")
        if v:
            kwargs["auto_approve_permissions"] = v.lower() in ("true", "1", "yes")

        v = _env("ENABLE_FALLBACK")
        if v:
            kwargs["enable_fallback"] = v.lower() in ("true", "1", "yes")

        v = _env("MAX_RETRIES")
        if v:
            kwargs["max_retries"] = int(v)

        v = _env("RETRY_BASE_DELAY")
        if v:
            kwargs["retry_base_delay"] = float(v)

        kwargs.update(overrides)
        return cls(**kwargs)
