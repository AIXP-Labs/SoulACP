"""ACP agent registry helpers.

Exposes the list of ACP-compatible agents that soulacp can adapt to,
and a small helper to check which CLIs are installed locally.

The full remote ACP Registry (https://acp.zed.dev/registry) is not yet
queried; this module currently returns the static set of providers that
have adapters in ``soulacp.adapters``. Remote sync may be added later
without changing the public API of this module.
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
    """Static info about an ACP-compatible agent supported by soulacp."""

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
    AgentInfo("claude", "claude-acp/",
              "npm install -g @anthropic-ai/claude-code",
              "Login: claude login (ChatGPT/Pro/Max account or API key)"),
    AgentInfo("codex", "codex-acp/",
              "npm install -g @zed-industries/codex-acp",
              "Login: codex login (ChatGPT account); model: gpt-5.5"),
    AgentInfo("gemini", "gemini-acp/",
              "npm install -g @google/gemini-cli",
              "Login: gemini login"),
    AgentInfo("opencode", "opencode-acp/",
              "npm install -g opencode",
              "No login required (free models); supports many providers"),
    AgentInfo("openclaw", "openclaw/", "npm install -g openclaw", ""),
    AgentInfo("cursor", "cursor-acp/", "Install Cursor IDE", ""),
    AgentInfo("kimi", "kimi-acp/", "npm install -g kimi-cli", ""),
    AgentInfo("qwen", "qwen-acp/", "npm install -g qwen-code", ""),
    AgentInfo("codebuddy", "codebuddy-acp/", "Install codebuddy", ""),
    AgentInfo("cline", "cline-acp/", "Install cline", ""),
    AgentInfo("copilot", "copilot-acp/", "Install GitHub Copilot CLI", ""),
    AgentInfo("copilot-ls", "copilot-ls/", "Install Copilot LS", ""),
    AgentInfo("amp", "amp-acp/", "Install Sourcegraph Amp", ""),
    AgentInfo("auggie", "auggie-acp/", "Install Auggie", ""),
    AgentInfo("autohand", "autohand-acp/", "Install Autohand", ""),
    AgentInfo("corust", "corust-acp/", "Install corust", ""),
    AgentInfo("crow", "crow-acp/", "Install crow", ""),
    AgentInfo("deepagents", "deepagents-acp/", "Install deepagents", ""),
    AgentInfo("droid", "droid-acp/", "Install droid", ""),
    AgentInfo("fastagent", "fastagent-acp/", "Install fast-agent", ""),
    AgentInfo("goose", "goose-acp/", "Install goose", ""),
    AgentInfo("junie", "junie-acp/", "Install JetBrains Junie", ""),
    AgentInfo("kilo", "kilo-acp/", "Install kilo", ""),
    AgentInfo("minion", "minion-acp/", "Install minion", ""),
    AgentInfo("nova", "nova-acp/", "Install nova", ""),
    AgentInfo("pi", "pi-acp/", "Install pi", ""),
    AgentInfo("qoder", "qoder-acp/", "Install qoder", ""),
    AgentInfo("stakpak", "stakpak-acp/", "Install stakpak", ""),
    AgentInfo("vibe", "vibe-acp/", "Install Mistral Vibe", ""),
)


# Map agent name → binary-finder function (for installed-check).
_FINDER_MAP = {
    "claude": find_claude_binary,
    "codex": find_codex_binary,
    "gemini": find_gemini_binary,
    "opencode": find_opencode_binary,
    "openclaw": find_openclaw_binary,
    "cursor": find_cursor_binary,
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
    """Return the full list of ACP-compatible agents soulacp supports."""
    return list(_AGENTS)


def list_installed_agents() -> list[AgentInfo]:
    """Return the subset of supported agents whose CLI binary is on PATH."""
    return [a for a in _AGENTS
            if _FINDER_MAP.get(a.name)
            and _FINDER_MAP[a.name]() is not None]


def is_installed(agent_name: str) -> bool:
    """Return True if the CLI binary for *agent_name* is on PATH."""
    finder = _FINDER_MAP.get(agent_name)
    return bool(finder and finder() is not None)
