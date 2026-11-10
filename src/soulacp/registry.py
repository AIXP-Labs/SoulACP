"""Agent-integration registry helpers.

Exposes the list of agent integrations that soulacp can adapt to,
and a small helper to check which CLIs are installed locally.

The remote ACP Registry index at
https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json is not
queried at runtime; this module returns the static set of providers that have
adapters in ``soulacp.adapters``. Remote sync may be added later without
changing this module's public API.
"""

from __future__ import annotations

from dataclasses import dataclass

from soulacp.binary import (
    find_amp_binary,
    find_auggie_binary,
    find_autohand_binary,
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


@dataclass(frozen=True)
class AgentInfo:
    """Static info about an agent integration supported by soulacp."""

    name: str
    """Short canonical name (e.g. ``"claude"`` / ``"codex"``)."""

    model_prefix: str
    """Model identifier prefix to pass into ``ACPConfig.model``
    (e.g. ``"claude-acp/"`` / ``"codex-acp/"``)."""

    install_cmd: str
    """One-line install command (best-effort, may differ per platform)."""

    notes: str
    """Short note about auth or special requirements."""


_AGENTS: tuple[AgentInfo, ...] = (
    AgentInfo(
        "claude",
        "claude-acp/",
        'npm install -g "@agentclientprotocol/claude-agent-acp@>=0.100.0"',
        "Auth: Claude Code/Anthropic credentials; legacy claude-code-acp remains supported",
    ),
    AgentInfo(
        "codex",
        "codex-acp/",
        'npm install -g "@agentclientprotocol/codex-acp@>=2.5.0"',
        "Login: codex login (ChatGPT account); example model: gpt-5.5",
    ),
    AgentInfo(
        "gemini",
        "gemini-acp/",
        "npm install -g @google/gemini-cli",
        "Auth: Enterprise/Google Cloud/paid API; personal OAuth moved to Antigravity",
    ),
    AgentInfo(
        "opencode",
        "opencode-acp/",
        "Install from https://opencode.ai",
        "No login required for configured free models; supports many providers",
    ),
    AgentInfo("openclaw", "openclaw/", "npm install -g openclaw", ""),
    AgentInfo("cursor", "cursor-acp/", "Install Cursor IDE", ""),
    AgentInfo(
        "cursor-cli",
        "cursor-cli/",
        "Install Cursor CLI",
        "Legacy non-ACP print-mode adapter",
    ),
    AgentInfo("kimi", "kimi-acp/", "Install from https://github.com/MoonshotAI/kimi-cli/releases", ""),
    AgentInfo("qwen", "qwen-acp/", "npm install -g @qwen-code/qwen-code", ""),
    AgentInfo("codebuddy", "codebuddy-acp/", "npm install -g @tencent-ai/codebuddy-code", ""),
    AgentInfo("cline", "cline-acp/", "npm install -g cline", ""),
    AgentInfo("copilot", "copilot-acp/", "npm install -g @github/copilot", ""),
    AgentInfo("copilot-ls", "copilot-ls/", "npm install -g @github/copilot-language-server", ""),
    AgentInfo("amp", "amp-acp/", "Install from the tao12345666333/amp-acp releases", ""),
    AgentInfo("auggie", "auggie-acp/", "npm install -g @augmentcode/auggie", ""),
    AgentInfo("autohand", "autohand-acp/", "npm install -g @autohandai/autohand-acp", ""),
    AgentInfo(
        "corust",
        "corust-acp/",
        "Install from https://github.com/Corust-ai/corust-agent-release/releases",
        "",
    ),
    AgentInfo("crow", "crow-acp/", "Install from https://github.com/crow-cli/crow-cli/releases", ""),
    AgentInfo("deepagents", "deepagents-acp/", "npm install -g deepagents-acp", ""),
    AgentInfo("droid", "droid-acp/", "npm install -g droid", ""),
    AgentInfo("fastagent", "fastagent-acp/", "uv tool install fast-agent-acp", ""),
    AgentInfo("goose", "goose-acp/", "Install from https://github.com/block/goose/releases", ""),
    AgentInfo("junie", "junie-acp/", "Install from https://github.com/JetBrains/junie/releases", ""),
    AgentInfo("kilo", "kilo-acp/", "npm install -g @kilocode/cli", ""),
    AgentInfo("minion", "minion-acp/", "uv tool install minion-code", ""),
    AgentInfo("nova", "nova-acp/", "npm install -g @compass-ai/nova", ""),
    AgentInfo("pi", "pi-acp/", "npm install -g pi-acp", ""),
    AgentInfo("qoder", "qoder-acp/", "npm install -g @qoder-ai/qodercli", ""),
    AgentInfo("stakpak", "stakpak-acp/", "Install from https://github.com/stakpak/agent/releases", ""),
    AgentInfo(
        "vibe",
        "vibe-acp/",
        "Install from https://github.com/mistralai/mistral-vibe/releases",
        "",
    ),
)


# Map agent name → binary-finder function (for installed-check).
_FINDER_MAP = {
    "claude": find_claude_binary,
    "codex": find_codex_binary,
    "gemini": find_gemini_binary,
    "opencode": find_opencode_binary,
    "openclaw": find_openclaw_binary,
    "cursor": find_cursor_binary,
    "cursor-cli": find_cursor_binary,
    "kimi": find_kimi_binary,
    "qwen": find_qwen_binary,
    "codebuddy": find_codebuddy_binary,
    "cline": find_cline_binary,
    "copilot": find_copilot_binary,
    "copilot-ls": find_copilot_ls_binary,
    "amp": find_amp_binary,
    "auggie": find_auggie_binary,
    "autohand": find_autohand_binary,
    "corust": find_corust_binary,
    "crow": find_crow_binary,
    "deepagents": find_deepagents_binary,
    "droid": find_droid_binary,
    "fastagent": find_fastagent_binary,
    "goose": find_goose_binary,
    "junie": find_junie_binary,
    "kilo": find_kilo_binary,
    "minion": find_minion_binary,
    "nova": find_nova_binary,
    "pi": find_pi_binary,
    "qoder": find_qoder_binary,
    "stakpak": find_stakpak_binary,
    "vibe": find_vibe_binary,
}


def list_agents() -> list[AgentInfo]:
    """Return all native ACP and legacy CLI integrations soulacp supports."""
    return list(_AGENTS)


def list_installed_agents() -> list[AgentInfo]:
    """Return the subset of supported agents whose CLI binary is on PATH."""
    return [a for a in _AGENTS if _FINDER_MAP.get(a.name) and _FINDER_MAP[a.name]() is not None]


def is_installed(agent_name: str) -> bool:
    """Return True if the CLI binary for *agent_name* is on PATH."""
    finder = _FINDER_MAP.get(agent_name)
    return bool(finder and finder() is not None)
